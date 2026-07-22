from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

from .alert_events import recipient_shard
from .azure_auth import default_azure_credential, table_endpoint_from_storage_url
from .config import Config
from .recipient_projection import legacy_user_id


_TERMINAL_DELIVERY_STATUSES = {
    "accepted",
    "sent",
    "delivered",
    "expanded",
    "bounced",
    "provider_suppressed",
    "quarantined",
    "filtered_spam",
    "provider_failed",
    "suppressed",
    "failed",
}
LOG = logging.getLogger(__name__)


def cleanup_alert_data(
    config: Config,
    *,
    limit: int = 0,
    max_runtime_seconds: int = 0,
) -> tuple[int, int, int, int]:
    """Delete terminal pipeline metadata after its retention period.

    Azure Table iterators page transparently. ``limit=0`` drains every page,
    which avoids a permanent 5,000-row ceiling; operators may still impose a
    one-off cap or runtime budget, both of which emit an explicit backlog
    warning so the next scheduled run can continue safely.
    """
    if limit < 0 or max_runtime_seconds < 0:
        raise ValueError("Retention limit and runtime must be non-negative")
    deadline = time.monotonic() + max_runtime_seconds if max_runtime_seconds else None
    try:
        from azure.data.tables import TableClient
    except ImportError as exc:
        raise RuntimeError("Install the 'azure' extra to clean alert data") from exc
    endpoint = table_endpoint_from_storage_url(config.azure_storage_account_url)
    credential = default_azure_credential()
    outbox = TableClient(
        endpoint=endpoint,
        table_name=config.alert_outbox_table,
        credential=credential,
    )
    deliveries = TableClient(
        endpoint=endpoint,
        table_name=config.alert_deliveries_table,
        credential=credential,
    )
    delivery_index = TableClient(
        endpoint=endpoint,
        table_name=config.alert_delivery_index_table,
        credential=credential,
    )
    suppressions = TableClient(
        endpoint=endpoint,
        table_name=config.alert_suppressions_table,
        credential=credential,
    )
    users = TableClient(
        endpoint=endpoint,
        table_name=config.auth_users_table,
        credential=credential,
    )
    recipients = TableClient(
        endpoint=endpoint,
        table_name=config.alert_recipients_table,
        credential=credential,
    )
    now = datetime.now(timezone.utc)
    outbox_cutoff = (now - timedelta(days=config.alert_outbox_retention_days)).isoformat()
    delivery_cutoff = (now - timedelta(days=config.alert_delivery_retention_days)).isoformat()

    final_report_cutoff = (now - timedelta(hours=2)).isoformat()
    overdue_final_reports = sum(
        1
        for _entity in deliveries.query_entities(
            "status eq @status and updatedAt lt @cutoff",
            parameters={"status": "accepted", "cutoff": final_report_cutoff},
            select=["PartitionKey", "RowKey"],
        )
    )
    if overdue_final_reports:
        LOG.warning(
            "ACS final delivery reports overdue: %d accepted delivery record(s) are older than 2 hours",
            overdue_final_reports,
        )

    removed_outbox = 0
    entities = outbox.query_entities(
        "status eq @status and createdAt lt @cutoff",
        parameters={"status": "published", "cutoff": outbox_cutoff},
        select=["PartitionKey", "RowKey"],
    )
    for entity in entities:
        outbox.delete_entity(str(entity["PartitionKey"]), str(entity["RowKey"]))
        removed_outbox += 1
        if _retention_budget_exhausted(
            table="alert outbox", count=removed_outbox, limit=limit, deadline=deadline
        ):
            break

    removed_deliveries = 0
    entities = deliveries.query_entities(
        "updatedAt lt @cutoff",
        parameters={"cutoff": delivery_cutoff},
        select=["PartitionKey", "RowKey", "status"],
    )
    for entity in entities:
        if str(entity.get("status") or "") not in _TERMINAL_DELIVERY_STATUSES:
            continue
        deliveries.delete_entity(str(entity["PartitionKey"]), str(entity["RowKey"]))
        removed_deliveries += 1
        if _retention_budget_exhausted(
            table="alert deliveries", count=removed_deliveries, limit=limit, deadline=deadline
        ):
            break
    removed_index = 0
    entities = delivery_index.query_entities(
        "createdAt lt @cutoff",
        parameters={"cutoff": delivery_cutoff},
        select=["PartitionKey", "RowKey"],
    )
    for entity in entities:
        delivery_index.delete_entity(str(entity["PartitionKey"]), str(entity["RowKey"]))
        removed_index += 1
        if _retention_budget_exhausted(
            table="delivery index", count=removed_index, limit=limit, deadline=deadline
        ):
            break
    removed_suppressions = 0
    for entity in suppressions.query_entities(
        "",
        select=["PartitionKey", "RowKey", "recipientId"],
    ):
        recipient_id = str(entity.get("recipientId") or entity.get("RowKey") or "")
        if recipient_id and _canonical_profile_is_active(
            users,
            recipients,
            recipient_id,
            shard_count=config.recipient_shard_count,
        ):
            continue
        suppressions.delete_entity(
            str(entity["PartitionKey"]),
            str(entity["RowKey"]),
        )
        removed_suppressions += 1
        if _retention_budget_exhausted(
            table="suppressions", count=removed_suppressions, limit=limit, deadline=deadline
        ):
            break
    return removed_outbox, removed_deliveries, removed_index, removed_suppressions


def _retention_budget_exhausted(
    *, table: str, count: int, limit: int, deadline: float | None
) -> bool:
    reason = ""
    if limit and count >= limit:
        reason = f"configured row cap {limit}"
    elif deadline is not None and time.monotonic() >= deadline:
        reason = "runtime budget"
    if not reason:
        return False
    LOG.warning(
        "Retention stopped %s after %d %s row(s); backlog may remain and will be retried",
        reason,
        count,
        table,
    )
    return True


def _canonical_profile_is_active(users, recipients, recipient_id: str, *, shard_count: int) -> bool:
    try:
        from azure.core.exceptions import ResourceNotFoundError
    except ImportError as exc:
        raise RuntimeError("Install the 'azure' extra to clean alert data") from exc

    try:
        profile = users.get_entity(
            "user",
            f"id:{recipient_id}",
            select=["recordType", "recordState", "userId", "email"],
        )
    except ResourceNotFoundError:
        profile = None
    if profile is not None:
        return _profile_entity_matches(profile, recipient_id)

    partition = f"r-{recipient_shard(recipient_id, shard_count):02x}"
    try:
        projection = recipients.get_entity(
            partition,
            recipient_id,
            select=["sourceUserRowKey"],
        )
    except ResourceNotFoundError:
        return False
    source_row = str(projection.get("sourceUserRowKey") or "").strip()
    if not source_row:
        # A legacy projection created before source-row pointers were added is
        # not enough evidence to erase a hard-bounce safety record. The daily
        # reconciler will backfill the pointer or remove the stale projection,
        # allowing a later cleanup run to make a definitive decision.
        return True
    try:
        profile = users.get_entity(
            "user",
            source_row,
            select=["recordType", "recordState", "userId", "email"],
        )
    except ResourceNotFoundError:
        return False
    return _profile_entity_matches(profile, recipient_id)


def _profile_entity_matches(profile, recipient_id: str) -> bool:
    record_type = str(profile.get("recordType") or "").strip().lower()
    if record_type and record_type != "profile":
        return False
    record_state = str(profile.get("recordState") or "").strip().lower()
    if record_state and record_state != "active":
        return False
    profile_id = str(profile.get("userId") or "").strip()
    if not profile_id:
        email = str(profile.get("email") or "").strip().lower()
        if "@" not in email:
            return False
        profile_id = legacy_user_id(email)
    return profile_id == recipient_id
