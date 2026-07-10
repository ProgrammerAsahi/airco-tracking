from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterator

from .alert_events import recipient_shard, utc_now_iso
from .azure_auth import default_azure_credential, table_endpoint_from_storage_url
from .config import Config
from .i18n import supported_lang
from .subscribers import has_email_alert_entitlement


LOG = logging.getLogger(__name__)
_DELIVERY_AUTHORITY_FIELDS = [
    "PartitionKey",
    "RowKey",
    "recordType",
    "recordState",
    "userId",
    "profileRevision",
    "email",
    "languagePreference",
    "deliveryCountry",
    "subscriptionPlan",
    "subscriptionStatus",
    "subscriptionCurrentPeriodEnd",
    "emailAlertsEnabled",
    "emailAlertsTokenVersion",
    "updatedAt",
]
_PROJECTION_AUTHORITY_FIELDS = ["PartitionKey", "RowKey", "sourceUserRowKey"]


@dataclass(frozen=True)
class ProjectedRecipient:
    recipient_id: str
    email: str
    language: str
    delivery_country: str | None
    plan: str
    status: str
    entitlement_end: str
    enabled: bool
    unsubscribe_token_version: int = 1

    def entitled(self, *, now: datetime | None = None) -> bool:
        if (
            not self.enabled
            or not _valid_email(self.email)
            or self.plan not in {"weekly_basic", "weekly_priority", "monthly_basic", "monthly_priority"}
            or self.status not in {"active", "canceled"}
        ):
            return False
        expires = _parse_datetime(self.entitlement_end)
        if expires is None:
            return False
        reference = now or datetime.now(timezone.utc)
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=timezone.utc)
        return expires > reference


class RecipientProjection:
    def __init__(self, config: Config) -> None:
        try:
            from azure.data.tables import TableClient
        except ImportError as exc:
            raise RuntimeError("Install the 'azure' extra to use recipient projection") from exc
        endpoint = table_endpoint_from_storage_url(config.azure_storage_account_url)
        credential = default_azure_credential()
        self._projection = TableClient(
            endpoint=endpoint,
            table_name=config.alert_recipients_table,
            credential=credential,
        )
        self._users = TableClient(
            endpoint=endpoint,
            table_name=config.auth_users_table,
            credential=credential,
        )
        self.shard_count = config.recipient_shard_count
        self.page_size = config.recipient_page_size

    def partition_key(self, recipient_id: str) -> str:
        return recipient_partition_key(recipient_id, self.shard_count)

    def get(self, recipient_id: str) -> ProjectedRecipient | None:
        try:
            from azure.core.exceptions import ResourceNotFoundError
        except ImportError as exc:
            raise RuntimeError("Install the 'azure' extra to use recipient projection") from exc
        try:
            entity = self._projection.get_entity(self.partition_key(recipient_id), recipient_id)
        except ResourceNotFoundError:
            return None
        return _projected_from_entity(entity)

    def get_authoritative(self, recipient_id: str) -> ProjectedRecipient | None:
        """Resolve the current canonical profile by stable UUID for delivery.

        ``alertrecipients`` is an efficient sharded fan-out read model, but its
        cross-table update can briefly lag a committed email/subscription
        change. The UUID-keyed canonical profile is therefore the final send
        authority. Legacy email-keyed profiles are point-read through the
        source row recorded by reconciliation; the bounded ``userId`` query is
        retained only for projections created before that source pointer.
        """
        try:
            from azure.core.exceptions import ResourceNotFoundError
        except ImportError as exc:
            raise RuntimeError("Install the 'azure' extra to resolve recipients") from exc

        try:
            entity = self._users.get_entity(
                "user",
                f"id:{recipient_id}",
                select=_DELIVERY_AUTHORITY_FIELDS,
            )
        except ResourceNotFoundError:
            source_row = self._legacy_source_row(recipient_id)
            if source_row is None:
                return None
            if source_row:
                try:
                    legacy = self._users.get_entity(
                        "user",
                        source_row,
                        select=_DELIVERY_AUTHORITY_FIELDS,
                    )
                except (ResourceNotFoundError, ValueError):
                    return None
                return self._authoritative_from_entity(legacy, recipient_id)

            safe_id = recipient_id.replace("'", "''")
            for legacy in self._users.query_entities(
                f"PartitionKey eq 'user' and userId eq '{safe_id}'",
                results_per_page=8,
                select=_DELIVERY_AUTHORITY_FIELDS,
            ):
                projected = self._authoritative_from_entity(legacy, recipient_id)
                if projected is not None:
                    return projected
            return None

        return self._authoritative_from_entity(entity, recipient_id)

    def _legacy_source_row(self, recipient_id: str) -> str | None:
        """Return the private canonical-row pointer from the fan-out projection."""
        try:
            from azure.core.exceptions import ResourceNotFoundError
        except ImportError as exc:
            raise RuntimeError("Install the 'azure' extra to resolve recipients") from exc
        try:
            entity = self._projection.get_entity(
                self.partition_key(recipient_id),
                recipient_id,
                select=_PROJECTION_AUTHORITY_FIELDS,
            )
        except ResourceNotFoundError:
            return ""
        source_row = str(entity.get("sourceUserRowKey") or "").strip()
        if not source_row:
            return ""
        return source_row if _valid_table_row_key(source_row) else None

    def _authoritative_from_entity(
        self,
        entity: dict[str, Any],
        recipient_id: str,
    ) -> ProjectedRecipient | None:
        projected = _projection_entity(entity, self.shard_count, sync_cycle="delivery")
        if projected is None or str(projected.get("recipientId") or "") != recipient_id:
            return None
        return _projected_from_entity(projected)

    def iter_shard(self, shard: int) -> Iterator[ProjectedRecipient]:
        if shard < 0 or shard >= self.shard_count:
            raise ValueError("Invalid recipient shard")
        partition = f"r-{shard:02x}"
        pages = self._projection.query_entities(
            f"PartitionKey eq '{partition}'",
            results_per_page=self.page_size,
            select=[
                "RowKey", "email", "language", "deliveryCountry", "subscriptionPlan",
                "plan", "status", "currentPeriodEnd", "entitlementEnd", "enabled",
                "unsubscribeTokenVersion",
            ],
        ).by_page()
        for page in pages:
            for entity in page:
                yield _projected_from_entity(entity)

    def find_by_email(self, email: str) -> ProjectedRecipient | None:
        escaped = email.strip().lower().replace("'", "''")
        for entity in self._projection.query_entities(f"email eq '{escaped}'"):
            return _projected_from_entity(entity)
        return None

    def reconcile(self) -> tuple[int, int]:
        """Repair the alert read model from the canonical users table.

        The web service writes this projection synchronously. This periodic full
        reconciliation is deliberately only a safety net for partial cross-table
        failures and for migrating pre-pipeline users.
        """
        try:
            from azure.core import MatchConditions
            from azure.core.exceptions import ResourceModifiedError
        except ImportError as exc:
            raise RuntimeError("Install the 'azure' extra to reconcile recipients") from exc
        cycle = str(uuid.uuid4())
        cycle_started_at = utc_now_iso()
        updated = 0
        live_keys: set[tuple[str, str]] = set()
        for user in self._users.query_entities("PartitionKey eq 'user'"):
            entity = _projection_entity(user, self.shard_count, sync_cycle=cycle)
            if entity is None:
                continue
            live_keys.add((str(entity["PartitionKey"]), str(entity["RowKey"])))
            updated += int(self._upsert_if_not_newer(entity))

        removed = 0
        # Only delete after the source scan completed successfully. This makes a
        # transient users-table failure fail closed instead of erasing recipients.
        for shard in range(self.shard_count):
            partition = f"r-{shard:02x}"
            for entity in self._projection.query_entities(
                f"PartitionKey eq '{partition}'",
                select=["PartitionKey", "RowKey", "syncCycle", "updatedAt", "sourceRevision"],
            ):
                key = (str(entity["PartitionKey"]), str(entity["RowKey"]))
                updated_at = str(entity.get("updatedAt") or "")
                if (
                    key not in live_keys
                    and entity.get("syncCycle") != cycle
                    and _is_before(updated_at, cycle_started_at)
                ):
                    etag = _etag(entity)
                    if not etag:
                        LOG.warning("Refusing to delete stale recipient without an ETag")
                        continue
                    try:
                        self._projection.delete_entity(
                            *key,
                            etag=etag,
                            match_condition=MatchConditions.IfNotModified,
                        )
                        removed += 1
                    except ResourceModifiedError:
                        # A synchronous web write won the race; its projection is current.
                        continue
        return updated, removed

    def _upsert_if_not_newer(self, entity: dict[str, Any]) -> bool:
        """Repair one projection without overwriting a concurrent web update."""
        try:
            from azure.core import MatchConditions
            from azure.core.exceptions import (
                ResourceExistsError,
                ResourceModifiedError,
                ResourceNotFoundError,
            )
            from azure.data.tables import UpdateMode
        except ImportError as exc:
            raise RuntimeError("Install the 'azure' extra to reconcile recipients") from exc

        partition = str(entity["PartitionKey"])
        row = str(entity["RowKey"])
        source_updated_at = str(entity.get("updatedAt") or "")
        source_revision = _nonnegative_revision(entity.get("sourceRevision"))
        for _attempt in range(4):
            try:
                current = self._projection.get_entity(partition, row)
            except ResourceNotFoundError:
                try:
                    self._projection.create_entity(entity)
                    return True
                except ResourceExistsError:
                    continue

            current_updated_at = str(current.get("updatedAt") or "")
            current_revision = _nonnegative_revision(current.get("sourceRevision"))
            # Revision is the concurrency authority. Timestamp is only a
            # legacy/tie-breaker within one revision; clock skew must never let
            # an older canonical snapshot overwrite a newer synchronous write.
            missing_source_pointer = bool(entity.get("sourceUserRowKey")) and not bool(
                current.get("sourceUserRowKey")
            )
            current_is_newer = current_revision > source_revision or (
                current_revision == source_revision
                and _is_after(current_updated_at, source_updated_at)
            )
            # The canonical row pointer is identity metadata, not delivery
            # state. Backfill it with MERGE even when a newer synchronous web
            # projection must otherwise win over this repair snapshot.
            pointer_only = current_is_newer and missing_source_pointer
            if current_is_newer and not missing_source_pointer:
                return False
            etag = _etag(current)
            if not etag:
                raise RuntimeError("Recipient projection entity is missing its ETag")
            try:
                update = entity
                mode = UpdateMode.REPLACE
                if pointer_only:
                    update = {
                        "PartitionKey": partition,
                        "RowKey": row,
                        "sourceUserRowKey": entity["sourceUserRowKey"],
                    }
                    mode = UpdateMode.MERGE
                self._projection.update_entity(
                    update,
                    mode=mode,
                    etag=etag,
                    match_condition=MatchConditions.IfNotModified,
                )
                return True
            except ResourceModifiedError:
                continue
        raise RuntimeError("Recipient projection changed repeatedly during reconciliation")


def _projection_entity(
    user: dict[str, Any], shard_count: int, *, sync_cycle: str
) -> dict[str, Any] | None:
    # Email migration atomically replaces the old canonical row with a
    # minimal superseded tombstone. A crash may leave that tombstone until a
    # later cleanup, so the repair scan must never project it as a recipient.
    # Missing state is the supported legacy active-row representation.
    record_type = str(user.get("recordType") or "").strip().lower()
    if record_type and record_type != "profile":
        return None
    record_state = str(user.get("recordState") or "").strip().lower()
    if record_state and record_state != "active":
        return None
    email = str(user.get("email") or "").strip().lower()
    if "@" not in email:
        return None
    recipient_id = str(user.get("userId") or "").strip()
    if not recipient_id:
        recipient_id = legacy_user_id(email)
    language = str(user.get("languagePreference") or "zh").strip().lower()
    if not supported_lang(language):
        language = "zh"
    enabled = has_email_alert_entitlement(user)
    plan = str(user.get("subscriptionPlan") or "none")
    status = str(user.get("subscriptionStatus") or "none")
    entitlement_end = str(user.get("subscriptionCurrentPeriodEnd") or "")
    shard = recipient_shard(recipient_id, shard_count)
    source_updated_at = str(user.get("updatedAt") or "").strip() or utc_now_iso()
    source_revision = _nonnegative_revision(user.get("profileRevision"))
    projected = {
        "PartitionKey": f"r-{shard:02x}",
        "RowKey": recipient_id,
        "recipientId": recipient_id,
        "email": email,
        "language": language,
        "deliveryCountry": str(user.get("deliveryCountry") or "").strip().lower(),
        "subscriptionPlan": plan,
        "status": status,
        "currentPeriodEnd": entitlement_end,
        "enabled": enabled,
        "unsubscribeTokenVersion": max(
            1, _nonnegative_revision(user.get("emailAlertsTokenVersion"))
        ),
        "sourceRevision": source_revision,
        # Preserve the canonical user's timestamp so the repair job can never
        # overwrite a newer synchronous web projection with an older snapshot.
        "updatedAt": source_updated_at,
        "syncCycle": sync_cycle,
    }
    source_user_row_key = str(user.get("RowKey") or "").strip()
    if _valid_table_row_key(source_user_row_key):
        projected["sourceUserRowKey"] = source_user_row_key
    return projected


def _projected_from_entity(entity: dict[str, Any]) -> ProjectedRecipient:
    language = str(entity.get("language") or "zh").strip().lower()
    if not supported_lang(language):
        language = "zh"
    return ProjectedRecipient(
        recipient_id=str(entity.get("recipientId") or entity.get("RowKey") or ""),
        email=str(entity.get("email") or "").strip().lower(),
        language=language,
        delivery_country=str(entity.get("deliveryCountry") or "").strip().lower() or None,
        plan=str(entity.get("subscriptionPlan") or entity.get("plan") or "none"),
        status=str(entity.get("status") or "none"),
        entitlement_end=str(entity.get("currentPeriodEnd") or entity.get("entitlementEnd") or ""),
        enabled=bool(entity.get("enabled", False)),
        unsubscribe_token_version=max(
            1, _nonnegative_revision(entity.get("unsubscribeTokenVersion"))
        ),
    )


def legacy_user_id(email: str) -> str:
    """Match the web service's deterministic UUID-v5-style legacy backfill."""
    digest = hashlib.sha256(
        f"airco-tracker-user\n{email.strip().lower()}".encode("utf-8")
    ).digest()
    return str(uuid.UUID(bytes=digest[:16], version=5))


def recipient_partition_key(recipient_id: str, shard_count: int = 32) -> str:
    return f"r-{recipient_shard(recipient_id, shard_count):02x}"


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_after(left: str, right: str) -> bool:
    left_value = _parse_datetime(left)
    right_value = _parse_datetime(right)
    return left_value is not None and (right_value is None or left_value >= right_value)


def _is_before(left: str, right: str) -> bool:
    left_value = _parse_datetime(left)
    right_value = _parse_datetime(right)
    # Invalid legacy timestamps are stale and may be removed; the ETag guard
    # still prevents deleting a concurrent synchronous update.
    return right_value is not None and (left_value is None or left_value < right_value)


def _etag(entity: Any) -> str | None:
    metadata = getattr(entity, "metadata", None)
    if isinstance(metadata, dict) and metadata.get("etag"):
        return str(metadata["etag"])
    if isinstance(entity, dict):
        for key in ("etag", "odata.etag", "@odata.etag"):
            if entity.get(key):
                return str(entity[key])
    return None


def _valid_email(value: str) -> bool:
    return (
        3 <= len(value) <= 254
        and "@" in value
        and not any(character.isspace() for character in value)
    )


def _valid_table_row_key(value: str) -> bool:
    return (
        bool(value)
        and len(value.encode("utf-8")) <= 1024
        and not any(character in value for character in ("/", "\\", "#", "?"))
        and not any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in value)
    )


def _nonnegative_revision(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        revision = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, revision)
