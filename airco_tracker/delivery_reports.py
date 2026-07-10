from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .alert_events import EmailJob, utc_now_iso
from .azure_auth import default_azure_credential, table_endpoint_from_storage_url
from .config import Config
from .deliveries import DeliveryLedger
from .recipient_projection import RecipientProjection
from .service_bus import PermanentMessageError


LOG = logging.getLogger(__name__)
EVENT_TYPE = "Microsoft.Communication.EmailDeliveryReportReceived"
EVENT_DATA_VERSION = "1.0"
_STATUS_MAP = {
    "delivered": "delivered",
    "expanded": "expanded",
    "bounced": "bounced",
    "suppressed": "provider_suppressed",
    "quarantined": "quarantined",
    "filteredspam": "filtered_spam",
    "failed": "provider_failed",
}
_SYSTEM_SUPPRESSION_STATUSES = {"bounced", "provider_suppressed"}


@dataclass(frozen=True)
class DeliveryReport:
    event_id: str
    message_id: str
    recipient: str
    status: str
    reported_at: str

    @classmethod
    def from_json(cls, payload: bytes | str) -> "DeliveryReport":
        if isinstance(payload, bytes):
            try:
                payload = payload.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError("Invalid Event Grid delivery-report encoding") from exc
        try:
            decoded = json.loads(payload)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("Invalid Event Grid delivery-report JSON") from exc
        # Webhook deliveries are arrays. Service Bus destinations normally
        # receive one Event Grid event object, but accepting one-item arrays
        # keeps replay tooling schema-compatible without accepting batches that
        # cannot be settled independently.
        if isinstance(decoded, list):
            if len(decoded) != 1:
                raise ValueError("Delivery-report batches must contain exactly one event")
            decoded = decoded[0]
        if not isinstance(decoded, dict):
            raise ValueError("Invalid Event Grid delivery-report event")
        if decoded.get("eventType") != EVENT_TYPE:
            raise ValueError("Unsupported Event Grid event type")
        if str(decoded.get("dataVersion") or "") != EVENT_DATA_VERSION:
            raise ValueError("Unsupported delivery-report dataVersion")
        data = decoded.get("data")
        if not isinstance(data, dict):
            raise ValueError("Delivery report is missing data")

        event_id = _bounded_string(decoded.get("id"), "event id", 160)
        message_id = _bounded_string(data.get("messageId"), "message id", 160)
        try:
            message_id = str(uuid.UUID(message_id))
        except ValueError as exc:
            raise ValueError("Delivery report has an invalid message id") from exc
        recipient = _normalise_email(
            _bounded_string(data.get("recipient"), "recipient", 320)
        )
        raw_status = _bounded_string(data.get("status"), "status", 40)
        status = _STATUS_MAP.get(raw_status.casefold().replace("_", ""))
        if status is None:
            raise ValueError("Delivery report has an unsupported status")
        timestamp = (
            data.get("deliveryAttemptTimeStamp")
            or data.get("deliveryAttemptTimestamp")
            or decoded.get("eventTime")
        )
        reported_at = _normalise_timestamp(timestamp)
        return cls(event_id, message_id, recipient, status, reported_at)


@dataclass(frozen=True)
class DeliveryMessageBinding:
    message_id: str
    event_id: str
    recipient_id: str
    delivery_id: str
    address_fingerprint: str

    def email_job(self) -> EmailJob:
        job = EmailJob.create(self.event_id, self.recipient_id)
        if job.delivery_id != self.delivery_id:
            raise ValueError("Delivery-message binding has an invalid delivery id")
        return job


def address_fingerprint(recipient_id: str, email: str) -> str:
    """Return a recipient-scoped pseudonymous address binding.

    The opaque random user UUID makes identical addresses unlinkable between
    accounts. The fingerprint lets an old-address bounce be recorded without
    suppressing a newly verified address and avoids persisting the plaintext
    address in delivery metadata.
    """
    try:
        recipient_id = str(uuid.UUID(recipient_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("A valid opaque recipient UUID is required") from exc
    normalized = _normalise_email(email)
    value = f"airco-alert-address-v1\0{recipient_id}\0{normalized}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class DeliveryMessageIndex:
    def __init__(self, config: Config) -> None:
        try:
            from azure.data.tables import TableClient
        except ImportError as exc:
            raise RuntimeError("Install the 'azure' extra to use delivery reports") from exc
        self._table = TableClient(
            endpoint=table_endpoint_from_storage_url(config.azure_storage_account_url),
            table_name=config.alert_delivery_index_table,
            credential=default_azure_credential(),
        )

    @staticmethod
    def partition_key(message_id: str) -> str:
        return "m-" + hashlib.sha256(message_id.encode("utf-8")).hexdigest()[:2]

    def bind(self, job: EmailJob, message_id: str, address_token: str) -> None:
        try:
            from azure.core.exceptions import ResourceExistsError
        except ImportError as exc:
            raise RuntimeError("Install the 'azure' extra to use delivery reports") from exc
        message_id = str(uuid.UUID(message_id))
        if len(address_token) != 64:
            raise ValueError("A SHA-256 address fingerprint is required")
        entity = {
            "PartitionKey": self.partition_key(message_id),
            "RowKey": message_id,
            "recordType": "acsMessageBinding",
            "eventId": job.event_id,
            "recipientId": job.recipient_id,
            "deliveryId": job.delivery_id,
            "addressFingerprint": address_token,
            "createdAt": utc_now_iso(),
        }
        try:
            self._table.create_entity(entity)
            return
        except ResourceExistsError:
            current = self._table.get_entity(entity["PartitionKey"], entity["RowKey"])
        immutable = ("eventId", "recipientId", "deliveryId", "addressFingerprint")
        if any(str(current.get(key) or "") != str(entity[key]) for key in immutable):
            raise RuntimeError("ACS message id is already bound to another delivery")

    def get(self, message_id: str) -> DeliveryMessageBinding | None:
        try:
            from azure.core.exceptions import ResourceNotFoundError
        except ImportError as exc:
            raise RuntimeError("Install the 'azure' extra to use delivery reports") from exc
        try:
            entity = self._table.get_entity(self.partition_key(message_id), message_id)
        except ResourceNotFoundError:
            return None
        return DeliveryMessageBinding(
            message_id=message_id,
            event_id=str(entity.get("eventId") or ""),
            recipient_id=str(entity.get("recipientId") or ""),
            delivery_id=str(entity.get("deliveryId") or ""),
            address_fingerprint=str(entity.get("addressFingerprint") or ""),
        )


class SystemSuppressionStore:
    def __init__(self, config: Config) -> None:
        try:
            from azure.data.tables import TableClient
        except ImportError as exc:
            raise RuntimeError("Install the 'azure' extra to use delivery suppression") from exc
        self._table = TableClient(
            endpoint=table_endpoint_from_storage_url(config.azure_storage_account_url),
            table_name=config.alert_suppressions_table,
            credential=default_azure_credential(),
        )

    @staticmethod
    def partition_key(recipient_id: str) -> str:
        return "u-" + hashlib.sha256(recipient_id.encode("utf-8")).hexdigest()[:24]

    def is_suppressed(self, recipient_id: str, email: str) -> bool:
        try:
            from azure.core.exceptions import ResourceNotFoundError
        except ImportError as exc:
            raise RuntimeError("Install the 'azure' extra to use delivery suppression") from exc
        try:
            entity = self._table.get_entity(self.partition_key(recipient_id), recipient_id)
        except ResourceNotFoundError:
            return False
        return (
            bool(entity.get("active", True))
            and str(entity.get("addressFingerprint") or "")
            == address_fingerprint(recipient_id, email)
        )

    def suppress(
        self,
        recipient_id: str,
        address_token: str,
        *,
        reason: str,
        reported_at: str,
        event_id: str,
    ) -> bool:
        if reason not in _SYSTEM_SUPPRESSION_STATUSES:
            raise ValueError("Only hard-bounce provider statuses may suppress an address")
        return self._upsert_if_newer(
            recipient_id,
            address_token,
            active=True,
            reason=reason,
            reported_at=reported_at,
            event_id=event_id,
        )

    def clear_if_newer(
        self,
        recipient_id: str,
        address_token: str,
        *,
        reported_at: str,
        event_id: str,
    ) -> bool:
        return self._upsert_if_newer(
            recipient_id,
            address_token,
            active=False,
            reason="delivered",
            reported_at=reported_at,
            event_id=event_id,
            create=False,
        )

    def _upsert_if_newer(
        self,
        recipient_id: str,
        address_token: str,
        *,
        active: bool,
        reason: str,
        reported_at: str,
        event_id: str,
        create: bool = True,
    ) -> bool:
        try:
            from azure.core import MatchConditions
            from azure.core.exceptions import (
                ResourceExistsError,
                ResourceModifiedError,
                ResourceNotFoundError,
            )
            from azure.data.tables import UpdateMode
        except ImportError as exc:
            raise RuntimeError("Install the 'azure' extra to use delivery suppression") from exc
        if len(address_token) != 64:
            raise ValueError("A SHA-256 address fingerprint is required")
        partition = self.partition_key(recipient_id)
        now = utc_now_iso()
        for _attempt in range(4):
            try:
                current = self._table.get_entity(partition, recipient_id)
            except ResourceNotFoundError:
                if not create:
                    return False
                try:
                    self._table.create_entity(
                        {
                            "PartitionKey": partition,
                            "RowKey": recipient_id,
                            "recipientId": recipient_id,
                            "addressFingerprint": address_token,
                            "active": active,
                            "reason": reason,
                            "reportAt": reported_at,
                            "eventGridEventId": event_id,
                            "createdAt": now,
                            "updatedAt": now,
                        }
                    )
                    return True
                except ResourceExistsError:
                    continue
            if _timestamp(current.get("reportAt")) > _timestamp(reported_at):
                return False
            if (
                str(current.get("eventGridEventId") or "") == event_id
                and str(current.get("addressFingerprint") or "") == address_token
                and bool(current.get("active", True)) == active
            ):
                return False
            etag = _etag(current)
            if not etag:
                raise RuntimeError("Suppression entity is missing its ETag")
            try:
                self._table.update_entity(
                    {
                        "PartitionKey": partition,
                        "RowKey": recipient_id,
                        "recipientId": recipient_id,
                        "addressFingerprint": address_token,
                        "active": active,
                        "reason": reason,
                        "reportAt": reported_at,
                        "eventGridEventId": event_id,
                        "updatedAt": now,
                    },
                    mode=UpdateMode.MERGE,
                    etag=etag,
                    match_condition=MatchConditions.IfNotModified,
                )
                return True
            except ResourceModifiedError:
                continue
        raise RuntimeError("Suppression state changed repeatedly; retry the report")


class DeliveryReportWorker:
    def __init__(
        self,
        config: Config,
        *,
        index: DeliveryMessageIndex | None = None,
        suppressions: SystemSuppressionStore | None = None,
        ledger: DeliveryLedger | None = None,
        recipients: RecipientProjection | None = None,
    ) -> None:
        self.index = index or DeliveryMessageIndex(config)
        self.suppressions = suppressions or SystemSuppressionStore(config)
        self.ledger = ledger or DeliveryLedger(config)
        self.recipients = recipients or RecipientProjection(config)

    def handle(self, report: DeliveryReport) -> str:
        binding = self.index.get(report.message_id)
        if binding is None:
            # The same ACS resource also sends login codes from the web app.
            # Those messages deliberately have no stock-alert binding.
            LOG.info("Ignoring unbound ACS delivery report %s", report.message_id[:12])
            return "ignored"
        try:
            job = binding.email_job()
        except ValueError as exc:
            raise PermanentMessageError(str(exc)) from exc
        event_address_token = address_fingerprint(binding.recipient_id, report.recipient)
        if event_address_token != binding.address_fingerprint:
            raise PermanentMessageError("Delivery report recipient does not match its binding")

        self.ledger.record_delivery_report(
            job,
            report_status=report.status,
            report_at=report.reported_at,
            event_grid_event_id=report.event_id,
            acs_message_id=report.message_id,
        )

        current = self.recipients.get_authoritative(binding.recipient_id)
        current_matches = current is not None and (
            address_fingerprint(binding.recipient_id, current.email) == event_address_token
        )
        if report.status in _SYSTEM_SUPPRESSION_STATUSES and current_matches:
            self.suppressions.suppress(
                binding.recipient_id,
                event_address_token,
                reason=report.status,
                reported_at=report.reported_at,
                event_id=report.event_id,
            )
        elif report.status == "delivered" and current_matches:
            self.suppressions.clear_if_newer(
                binding.recipient_id,
                event_address_token,
                reported_at=report.reported_at,
                event_id=report.event_id,
            )
        LOG.info(
            "Recorded ACS final delivery report %s status=%s",
            job.delivery_id[:12],
            report.status,
        )
        return report.status


def parse_delivery_report(payload: bytes | str) -> DeliveryReport:
    try:
        return DeliveryReport.from_json(payload)
    except ValueError as exc:
        raise PermanentMessageError(str(exc)) from exc


def _bounded_string(value: Any, name: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"Delivery report is missing {name}")
    value = value.strip()
    if not value or len(value) > maximum:
        raise ValueError(f"Delivery report has an invalid {name}")
    return value


def _normalise_email(value: str) -> str:
    value = value.strip().casefold()
    if value.count("@") != 1 or value.startswith("@") or value.endswith("@"):
        raise ValueError("Delivery report has an invalid recipient")
    return value


def _normalise_timestamp(value: Any) -> str:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError("Delivery report has an invalid timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _timestamp(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _etag(entity: Any) -> str | None:
    metadata = getattr(entity, "metadata", None)
    if isinstance(metadata, dict) and metadata.get("etag"):
        return str(metadata["etag"])
    if isinstance(entity, dict):
        for key in ("etag", "odata.etag", "@odata.etag"):
            if entity.get(key):
                return str(entity[key])
    return None
