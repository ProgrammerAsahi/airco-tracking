from __future__ import annotations

import logging
from dataclasses import dataclass
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


class AzureTableOutbox:
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

    @staticmethod
    def partition_key(event_id: str) -> str:
        return f"o-{event_id[:2]}"

    def create_if_absent(self, event: StockAvailableEvent) -> bool:
        try:
            from azure.core.exceptions import ResourceExistsError
        except ImportError as exc:
            raise RuntimeError("Install the 'azure' extra to use the alert outbox") from exc
        entity = {
            "PartitionKey": self.partition_key(event.event_id),
            "RowKey": event.event_id,
            "schemaVersion": event.schema_version,
            "eventType": event.event_type,
            "payloadJson": event.to_json(),
            "status": "pending",
            "attempts": 0,
            "createdAt": event.created_at,
            "updatedAt": utc_now_iso(),
        }
        try:
            self._table.create_entity(entity)
            return True
        except ResourceExistsError:
            return False

    def get(self, event_id: str) -> OutboxRecord:
        entity = self._table.get_entity(self.partition_key(event_id), event_id)
        return _record_from_entity(entity)

    def pending(self, *, limit: int = 100) -> list[OutboxRecord]:
        records: list[OutboxRecord] = []
        query = self._table.query_entities("status eq 'pending'")
        for entity in query:
            records.append(_record_from_entity(entity))
            if len(records) >= limit:
                break
        records.sort(key=lambda item: item.event.created_at)
        return records

    def mark_published(self, event_ids: Iterable[str]) -> None:
        try:
            from azure.core import MatchConditions
            from azure.core.exceptions import ResourceModifiedError
            from azure.data.tables import UpdateMode
        except ImportError as exc:
            raise RuntimeError("Install the 'azure' extra to use the alert outbox") from exc
        now = utc_now_iso()
        for event_id in event_ids:
            for _attempt in range(4):
                current = self.get(event_id)
                if current.status == "published":
                    break
                if not current.etag:
                    raise RuntimeError("Alert outbox entity is missing its ETag")
                try:
                    self._table.update_entity(
                        {
                            "PartitionKey": self.partition_key(event_id),
                            "RowKey": event_id,
                            "status": "published",
                            "attempts": current.attempts + 1,
                            "publishedAt": now,
                            "updatedAt": now,
                        },
                        mode=UpdateMode.MERGE,
                        etag=current.etag,
                        match_condition=MatchConditions.IfNotModified,
                    )
                    break
                except ResourceModifiedError:
                    continue
            else:
                raise RuntimeError("Alert outbox changed repeatedly while marking published")

    def mark_attempt_failed(self, event_ids: Iterable[str], error_code: str) -> None:
        try:
            from azure.core import MatchConditions
            from azure.core.exceptions import ResourceModifiedError
            from azure.data.tables import UpdateMode
        except ImportError as exc:
            raise RuntimeError("Install the 'azure' extra to use the alert outbox") from exc
        now = utc_now_iso()
        for event_id in event_ids:
            for _attempt in range(4):
                current = self.get(event_id)
                # A concurrent successful publisher is authoritative. Never
                # let a late failure move an event from published to pending.
                if current.status == "published":
                    break
                if not current.etag:
                    raise RuntimeError("Alert outbox entity is missing its ETag")
                try:
                    self._table.update_entity(
                        {
                            "PartitionKey": self.partition_key(event_id),
                            "RowKey": event_id,
                            "status": "pending",
                            "attempts": current.attempts + 1,
                            "lastErrorCode": error_code[:120],
                            "updatedAt": now,
                        },
                        mode=UpdateMode.MERGE,
                        etag=current.etag,
                        match_condition=MatchConditions.IfNotModified,
                    )
                    break
                except ResourceModifiedError:
                    continue
            else:
                raise RuntimeError("Alert outbox changed repeatedly while recording failure")


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


def _etag(entity) -> str:
    metadata = getattr(entity, "metadata", None)
    if isinstance(metadata, dict) and metadata.get("etag"):
        return str(metadata["etag"])
    if isinstance(entity, dict):
        for key in ("etag", "odata.etag", "@odata.etag"):
            if entity.get(key):
                return str(entity[key])
    return ""
