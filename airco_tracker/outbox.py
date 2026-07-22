from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

from .alert_events import StockAvailableEvent, utc_now_iso
from .azure_auth import default_azure_credential, table_endpoint_from_storage_url
from .config import Config


LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class OutboxRecord:
    event: StockAvailableEvent
    status: str
    attempts: int = 0
    etag: str = ""
    claim_owner: str = ""


class AzureTableOutbox:
    _CLAIM_SECONDS = 120

    def __init__(self, config: Config) -> None:
        try:
            from azure.data.tables import TableClient
        except ImportError as exc:
            raise RuntimeError("Install the 'azure' extra to use the alert outbox") from exc
        self._table = TableClient(
            endpoint=table_endpoint_from_storage_url(config.azure_storage_account_url),
            table_name=config.alert_outbox_table,
            credential=default_azure_credential(),
        )
        self._pending = TableClient(
            endpoint=table_endpoint_from_storage_url(config.azure_storage_account_url),
            table_name=getattr(config, "alert_outbox_pending_table", "alertoutboxpending"),
            credential=default_azure_credential(),
        )

    @staticmethod
    def partition_key(event_id: str) -> str:
        return f"o-{event_id[:2]}"

    def create_if_absent(self, event: StockAvailableEvent) -> bool:
        try:
            from azure.core.exceptions import ResourceExistsError
        except ImportError as exc:
            raise RuntimeError("Install the 'azure' extra to use the alert outbox") from exc
        current = self._try_get(event.event_id)
        if current is not None:
            if current.status == "pending":
                self._create_pending_journal(current.event)
            else:
                self._delete_pending_journal(current.event)
            return False

        # ``alertoutboxpending`` is the authoritative enqueue journal, not a
        # pointer which may be deleted when the archive row is temporarily
        # absent.  It contains the complete immutable event and uses the
        # deterministic event id as its row key.  Creating this one entity is
        # therefore the durable enqueue commit point even though Azure Table
        # cannot transact across the journal and sharded archive tables.
        journal_created = self._create_pending_journal(event)
        canonical_event = event
        if not journal_created:
            try:
                canonical_event = self._pending_journal_event(event.event_id)
            except Exception as exc:
                if type(exc).__name__ != "ResourceNotFoundError":
                    raise
                # A publisher may have acknowledged and removed the journal
                # after our failed create but before this point.  It must have
                # committed the published archive first, so that row is the
                # durable proof that this deterministic event already won.
                raced = self._try_get(event.event_id)
                if raced is None:
                    raise RuntimeError(
                        "Pending journal disappeared without a durable archive"
                    ) from exc
                return False
        entity = self._main_entity(canonical_event, status="pending")
        try:
            self._table.create_entity(entity)
        except ResourceExistsError:
            pass
        except Exception:
            # The full event is already durable and discoverable in the hot
            # journal.  The publisher repairs/creates the archive before it
            # acknowledges the journal, so an archive outage must not make
            # the scanner repeat or lose the stock transition.
            LOG.warning(
                "Alert event %s is durable in the pending journal but its archive row "
                "could not be written; publisher will repair it",
                event.event_id,
                exc_info=True,
            )
        return journal_created

    @classmethod
    def _main_entity(
        cls,
        event: StockAvailableEvent,
        *,
        status: str,
        attempts: int = 0,
        error_code: str = "",
        published_at: str = "",
    ) -> dict[str, object]:
        now = utc_now_iso()
        entity: dict[str, object] = {
            "PartitionKey": cls.partition_key(event.event_id),
            "RowKey": event.event_id,
            "schemaVersion": event.schema_version,
            "eventType": event.event_type,
            "payloadJson": event.to_json(),
            "status": status,
            "attempts": attempts,
            "createdAt": event.created_at,
            "updatedAt": now,
            "pendingRowKey": cls.pending_row_key(event),
        }
        if error_code:
            entity["lastErrorCode"] = error_code[:120]
        if published_at:
            entity["publishedAt"] = published_at
        return entity

    def get(self, event_id: str) -> OutboxRecord:
        entity = self._table.get_entity(self.partition_key(event_id), event_id)
        return _record_from_entity(entity)

    def _try_get(self, event_id: str) -> OutboxRecord | None:
        try:
            return self.get(event_id)
        except Exception as exc:
            if type(exc).__name__ == "ResourceNotFoundError":
                return None
            raise

    def pending(self, *, limit: int = 100) -> list[OutboxRecord]:
        if limit <= 0:
            return []
        if not hasattr(self, "_pending"):
            return self._legacy_pending(limit=limit)
        self._backfill_legacy_pending()
        records: list[OutboxRecord] = []
        seen_event_ids: set[str] = set()
        query = self._pending.query_entities("PartitionKey eq 'pending'")
        for index_entity in query:
            event_id = str(index_entity.get("eventId") or "")
            if not event_id:
                self._delete_pending_index_entity(index_entity)
                continue
            is_journal = bool(index_entity.get("payloadJson"))
            if event_id in seen_event_ids:
                # A v2 journal is authoritative.  It is safe to remove an old
                # pointer-only row for the same event, but never remove the
                # journal merely because a concurrent reader saw a duplicate.
                if not is_journal:
                    self._delete_pending_index_entity(index_entity)
                continue
            record = self._try_get(event_id)
            if record is None:
                if not is_journal:
                    # Pointer-only rows were written by the old two-table
                    # protocol.  An old scanner may still be between writes,
                    # so deleting this row can strand a later archive commit.
                    # Keep it for a future repair/deployment migration.
                    continue
                record = _record_from_pending_entity(index_entity)
            if record.status != "pending":
                self._delete_pending_index_entity(index_entity)
                continue
            if is_journal:
                journal_record = _record_from_pending_entity(index_entity)
                if journal_record.event.event_id != record.event.event_id:
                    raise RuntimeError("Pending journal identity does not match its archive row")
                record = journal_record
            record = self._claim_pending_entity(index_entity, record)
            if record is None:
                continue
            seen_event_ids.add(event_id)
            records.append(record)
            if len(records) >= limit:
                break
        records.sort(key=lambda item: item.event.created_at)
        return records

    @staticmethod
    def pending_row_key(event: StockAvailableEvent) -> str:
        return event.event_id

    def mark_published(self, records: Iterable[str | OutboxRecord]) -> None:
        try:
            from azure.core import MatchConditions
            from azure.core.exceptions import ResourceExistsError, ResourceModifiedError
            from azure.data.tables import UpdateMode
        except ImportError as exc:
            raise RuntimeError("Install the 'azure' extra to use the alert outbox") from exc
        now = utc_now_iso()
        for event_id, _claim_owner in _transition_refs(records):
            event = self._event_for_transition(event_id)
            for _attempt in range(4):
                current = self._try_get(event_id)
                if current is None:
                    try:
                        self._table.create_entity(
                            self._main_entity(
                                event,
                                status="published",
                                attempts=1,
                                published_at=now,
                            )
                        )
                        self._delete_pending_journal(event)
                        break
                    except ResourceExistsError:
                        continue
                if current.status == "published":
                    self._delete_pending_journal(event)
                    break
                if not current.etag:
                    raise RuntimeError("Alert outbox entity is missing its ETag")
                try:
                    self._table.update_entity(
                        {
                            "PartitionKey": self.partition_key(event_id),
                            "RowKey": event_id,
                            "schemaVersion": event.schema_version,
                            "eventType": event.event_type,
                            "payloadJson": event.to_json(),
                            "status": "published",
                            "attempts": current.attempts + 1,
                            "createdAt": event.created_at,
                            "publishedAt": now,
                            "updatedAt": now,
                            "pendingRowKey": self.pending_row_key(event),
                        },
                        mode=UpdateMode.MERGE,
                        etag=current.etag,
                        match_condition=MatchConditions.IfNotModified,
                    )
                    self._delete_pending_journal(event)
                    break
                except ResourceModifiedError:
                    continue
            else:
                raise RuntimeError("Alert outbox changed repeatedly while marking published")

    def mark_attempt_failed(
        self,
        records: Iterable[str | OutboxRecord],
        error_code: str,
    ) -> None:
        try:
            from azure.core import MatchConditions
            from azure.core.exceptions import ResourceExistsError, ResourceModifiedError
            from azure.data.tables import UpdateMode
        except ImportError as exc:
            raise RuntimeError("Install the 'azure' extra to use the alert outbox") from exc
        now = utc_now_iso()
        for event_id, claim_owner in _transition_refs(records):
            event = self._event_for_transition(event_id)
            for _attempt in range(4):
                current = self._try_get(event_id)
                if current is None:
                    try:
                        self._table.create_entity(
                            self._main_entity(
                                event,
                                status="pending",
                                attempts=1,
                                error_code=error_code,
                            )
                        )
                        self._create_pending_journal(event)
                        self._release_pending_claim(event, claim_owner)
                        break
                    except ResourceExistsError:
                        continue
                # A concurrent successful publisher is authoritative. Never
                # let a late failure move an event from published to pending.
                if current.status == "published":
                    self._delete_pending_journal(event)
                    break
                if not current.etag:
                    raise RuntimeError("Alert outbox entity is missing its ETag")
                try:
                    self._table.update_entity(
                        {
                            "PartitionKey": self.partition_key(event_id),
                            "RowKey": event_id,
                            "schemaVersion": event.schema_version,
                            "eventType": event.event_type,
                            "payloadJson": event.to_json(),
                            "status": "pending",
                            "attempts": current.attempts + 1,
                            "createdAt": event.created_at,
                            "lastErrorCode": error_code[:120],
                            "updatedAt": now,
                            "pendingRowKey": self.pending_row_key(event),
                        },
                        mode=UpdateMode.MERGE,
                        etag=current.etag,
                        match_condition=MatchConditions.IfNotModified,
                    )
                    self._create_pending_journal(event)
                    self._release_pending_claim(event, claim_owner)
                    break
                except ResourceModifiedError:
                    continue
            else:
                raise RuntimeError("Alert outbox changed repeatedly while recording failure")

    def _create_pending_journal(self, event: StockAvailableEvent) -> bool:
        pending = getattr(self, "_pending", None)
        if pending is None:
            return False
        try:
            from azure.core.exceptions import ResourceExistsError
        except ImportError as exc:
            raise RuntimeError("Install the 'azure' extra to use the alert outbox") from exc
        try:
            pending.create_entity(
                {
                    "PartitionKey": "pending",
                    "RowKey": self.pending_row_key(event),
                    "recordVersion": 2,
                    "eventId": event.event_id,
                    "eventPartitionKey": self.partition_key(event.event_id),
                    "schemaVersion": event.schema_version,
                    "eventType": event.event_type,
                    "payloadJson": event.to_json(),
                    "status": "pending",
                    "attempts": 0,
                    "createdAt": event.created_at,
                    "updatedAt": utc_now_iso(),
                }
            )
            return True
        except ResourceExistsError:
            return False

    def _claim_pending_entity(
        self,
        entity,
        record: OutboxRecord,
    ) -> OutboxRecord | None:
        try:
            from azure.core import MatchConditions
            from azure.core.exceptions import ResourceModifiedError
            from azure.data.tables import UpdateMode
        except ImportError as exc:
            raise RuntimeError("Install the 'azure' extra to use the alert outbox") from exc
        now = datetime.now(timezone.utc)
        lease_expires = _optional_datetime(entity.get("leaseExpiresAt"))
        if lease_expires is not None and lease_expires > now:
            return None
        etag = _etag(entity)
        if not etag:
            raise RuntimeError("Pending journal entity is missing its ETag")
        owner = str(uuid.uuid4())
        try:
            self._pending.update_entity(
                {
                    "PartitionKey": str(entity["PartitionKey"]),
                    "RowKey": str(entity["RowKey"]),
                    "leaseOwner": owner,
                    "leaseExpiresAt": (
                        now + timedelta(seconds=self._CLAIM_SECONDS)
                    ).isoformat(),
                    "updatedAt": now.isoformat(),
                },
                mode=UpdateMode.MERGE,
                etag=etag,
                match_condition=MatchConditions.IfNotModified,
            )
        except ResourceModifiedError:
            # Another publisher won the same row from the query snapshot.
            return None
        return OutboxRecord(
            event=record.event,
            status=record.status,
            attempts=record.attempts,
            etag=record.etag,
            claim_owner=owner,
        )

    def _release_pending_claim(self, event: StockAvailableEvent, claim_owner: str) -> None:
        if not claim_owner or getattr(self, "_pending", None) is None:
            return
        try:
            from azure.core import MatchConditions
            from azure.core.exceptions import ResourceModifiedError
            from azure.data.tables import UpdateMode
        except ImportError as exc:
            raise RuntimeError("Install the 'azure' extra to use the alert outbox") from exc
        try:
            entity = self._pending.get_entity("pending", self.pending_row_key(event))
        except Exception as exc:
            if type(exc).__name__ == "ResourceNotFoundError":
                return
            raise
        if str(entity.get("leaseOwner") or "") != claim_owner:
            return
        etag = _etag(entity)
        if not etag:
            raise RuntimeError("Pending journal entity is missing its ETag")
        try:
            self._pending.update_entity(
                {
                    "PartitionKey": "pending",
                    "RowKey": self.pending_row_key(event),
                    "leaseOwner": "",
                    "leaseExpiresAt": "",
                    "updatedAt": utc_now_iso(),
                },
                mode=UpdateMode.MERGE,
                etag=etag,
                match_condition=MatchConditions.IfNotModified,
            )
        except ResourceModifiedError:
            # A lease rollover is authoritative; never clear its new owner.
            return

    # Compatibility name retained for callers/tests from the first hot-index
    # implementation.  New rows are complete journal records.
    def _create_pending_index(self, event: StockAvailableEvent) -> bool:
        return self._create_pending_journal(event)

    def _delete_pending_journal(self, event: StockAvailableEvent) -> None:
        pending = getattr(self, "_pending", None)
        if pending is None:
            return
        try:
            pending.delete_entity("pending", self.pending_row_key(event))
        except Exception as exc:
            if type(exc).__name__ != "ResourceNotFoundError":
                raise

    def _delete_pending_index(self, event: StockAvailableEvent) -> None:
        self._delete_pending_journal(event)

    def _pending_journal_event(self, event_id: str) -> StockAvailableEvent:
        pending = getattr(self, "_pending", None)
        if pending is None:
            raise RuntimeError("Pending journal is not configured")
        entity = pending.get_entity("pending", event_id)
        return _record_from_pending_entity(entity).event

    def _event_for_transition(self, event_id: str) -> StockAvailableEvent:
        if getattr(self, "_pending", None) is not None:
            try:
                return self._pending_journal_event(event_id)
            except Exception as exc:
                if type(exc).__name__ != "ResourceNotFoundError":
                    raise
        current = self._try_get(event_id)
        if current is None:
            raise RuntimeError("Outbox event is missing from both journal and archive")
        return current.event

    def _delete_pending_index_entity(self, entity) -> None:
        pending = getattr(self, "_pending", None)
        if pending is None:
            return
        try:
            pending.delete_entity(str(entity["PartitionKey"]), str(entity["RowKey"]))
        except Exception as exc:
            # Multiple publisher executions may observe the same stale index.
            # Cleanup is idempotent: whichever reader loses the delete race
            # must continue processing the remaining hot-partition rows.
            if type(exc).__name__ != "ResourceNotFoundError":
                raise

    def _legacy_pending(self, *, limit: int) -> list[OutboxRecord]:
        records: list[OutboxRecord] = []
        for entity in self._table.query_entities("status eq 'pending'"):
            records.append(_record_from_entity(entity))
            if len(records) >= limit:
                break
        records.sort(key=lambda item: item.event.created_at)
        return records

    def _backfill_legacy_pending(self) -> None:
        pending = getattr(self, "_pending", None)
        if pending is None:
            return
        try:
            pending.get_entity("_meta", "journal-v2")
            return
        except Exception as exc:
            if type(exc).__name__ != "ResourceNotFoundError":
                raise
        # Azure's query iterator transparently follows continuation tokens.
        # Drain it completely before writing the completion marker: applying a
        # fixed first-page limit would revisit the same oldest rows forever
        # and strand every legacy row beyond that page.
        for entity in self._table.query_entities("status eq 'pending'"):
            self._create_pending_journal(_record_from_entity(entity).event)
        try:
            pending.create_entity(
                {
                    "PartitionKey": "_meta",
                    "RowKey": "journal-v2",
                    "completedAt": utc_now_iso(),
                }
            )
        except Exception as exc:
            if type(exc).__name__ != "ResourceExistsError":
                raise


def build_outbox(config: Config) -> AzureTableOutbox:
    if not config.azure_storage_account_url:
        raise ValueError("AZURE_STORAGE_ACCOUNT_URL is required for the alert outbox")
    return AzureTableOutbox(config)


def _record_from_entity(entity) -> OutboxRecord:
    return OutboxRecord(
        event=StockAvailableEvent.from_json(str(entity["payloadJson"])),
        status=str(entity.get("status") or "pending"),
        attempts=int(entity.get("attempts") or 0),
        etag=_etag(entity),
    )


def _record_from_pending_entity(entity) -> OutboxRecord:
    if not entity.get("payloadJson"):
        raise RuntimeError("Pending journal row is missing its immutable event payload")
    record = _record_from_entity(entity)
    if record.status != "pending":
        raise RuntimeError("Pending journal row has an invalid status")
    event_id = str(entity.get("eventId") or "")
    if event_id != record.event.event_id or str(entity.get("RowKey") or "") != event_id:
        raise RuntimeError("Pending journal row identity does not match its payload")
    return record


def _transition_refs(
    records: Iterable[str | OutboxRecord],
) -> list[tuple[str, str]]:
    return [
        (
            value.event.event_id if isinstance(value, OutboxRecord) else str(value),
            value.claim_owner if isinstance(value, OutboxRecord) else "",
        )
        for value in records
    ]


def _optional_datetime(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _etag(entity) -> str:
    metadata = getattr(entity, "metadata", None)
    if isinstance(metadata, dict) and metadata.get("etag"):
        return str(metadata["etag"])
    if isinstance(entity, dict):
        for key in ("etag", "odata.etag", "@odata.etag"):
            if entity.get(key):
                return str(entity[key])
    return ""
