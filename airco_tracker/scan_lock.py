from __future__ import annotations

import logging
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from .azure_auth import default_azure_credential, table_endpoint_from_storage_url
from .config import Config


LOG = logging.getLogger(__name__)
_PARTITION = "control"
_ROW = "scanner-lease"


@contextmanager
def scanner_lease(config: Config):
    """Prevent scheduled, deploy-verification, and manual scans from racing."""
    try:
        from azure.core import MatchConditions
        from azure.core.exceptions import ResourceExistsError, ResourceModifiedError, ResourceNotFoundError
        from azure.data.tables import TableClient, UpdateMode
    except ImportError as exc:
        raise RuntimeError("Install the 'azure' extra to use the scanner lease") from exc

    table = TableClient(
        endpoint=table_endpoint_from_storage_url(config.azure_storage_account_url),
        table_name=config.alert_outbox_table,
        credential=default_azure_credential(),
    )
    owner = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    entity = {
        "PartitionKey": _PARTITION,
        "RowKey": _ROW,
        "owner": owner,
        "expiresAt": (now + timedelta(seconds=config.scanner_lease_seconds)).isoformat(),
        "updatedAt": now.isoformat(),
    }
    acquired = False
    try:
        table.create_entity(entity)
        acquired = True
    except ResourceExistsError:
        try:
            current = table.get_entity(_PARTITION, _ROW)
            expires_at = _parse_datetime(current.get("expiresAt"))
            if expires_at is None or expires_at <= now:
                kwargs = {"mode": UpdateMode.REPLACE}
                etag = _etag(current)
                if etag:
                    kwargs.update(etag=etag, match_condition=MatchConditions.IfNotModified)
                table.update_entity(entity, **kwargs)
                acquired = True
        except (ResourceModifiedError, ResourceNotFoundError):
            acquired = False

    try:
        yield acquired
    finally:
        if acquired:
            try:
                current = table.get_entity(_PARTITION, _ROW)
                if current.get("owner") == owner:
                    kwargs = {}
                    etag = _etag(current)
                    if etag:
                        kwargs.update(etag=etag, match_condition=MatchConditions.IfNotModified)
                    table.delete_entity(_PARTITION, _ROW, **kwargs)
            except Exception:
                LOG.warning("Could not release scanner lease; it will expire automatically", exc_info=True)


def _parse_datetime(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _etag(entity) -> str | None:
    metadata = getattr(entity, "metadata", None)
    if isinstance(metadata, dict) and metadata.get("etag"):
        return str(metadata["etag"])
    return None
