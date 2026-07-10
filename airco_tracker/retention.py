from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .azure_auth import default_azure_credential, table_endpoint_from_storage_url
from .config import Config


_TERMINAL_DELIVERY_STATUSES = {"sent", "suppressed", "failed"}


def cleanup_alert_data(config: Config, *, limit: int = 5000) -> tuple[int, int]:
    """Delete terminal pipeline metadata after its documented retention period."""
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
    now = datetime.now(timezone.utc)
    outbox_cutoff = (now - timedelta(days=config.alert_outbox_retention_days)).isoformat()
    delivery_cutoff = (now - timedelta(days=config.alert_delivery_retention_days)).isoformat()

    removed_outbox = 0
    entities = outbox.query_entities(
        "status eq @status and createdAt lt @cutoff",
        parameters={"status": "published", "cutoff": outbox_cutoff},
        select=["PartitionKey", "RowKey"],
    )
    for entity in entities:
        outbox.delete_entity(str(entity["PartitionKey"]), str(entity["RowKey"]))
        removed_outbox += 1
        if removed_outbox >= limit:
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
        if removed_deliveries >= limit:
            break
    return removed_outbox, removed_deliveries
