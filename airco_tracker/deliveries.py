from __future__ import annotations

import hashlib
import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .alert_events import EmailJob, utc_now_iso
from .azure_auth import default_azure_credential, table_endpoint_from_storage_url
from .config import Config


_ACS_OPERATION_NAMESPACE = uuid.UUID("4bd7be21-28ef-42e9-85c6-2cd647af8421")
# These states must never cause another provider send. ``accepted`` means the
# ACS long-running send operation succeeded; Event Grid later replaces it with
# a recipient-level delivery result. ``sent`` is retained as a no-resend
# legacy state for rows written before final-delivery tracking was introduced.
NO_RESEND_STATUSES = {
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
FINAL_DELIVERY_STATUSES = {
    "delivered",
    "expanded",
    "bounced",
    "provider_suppressed",
    "quarantined",
    "filtered_spam",
    "provider_failed",
}


@dataclass(frozen=True)
class DeliveryClaim:
    claimed: bool
    status: str
    operation_id: str
    attempts: int
    lease_owner: str = ""
    first_sent_at: str = ""
    retry_after_seconds: int = 0


class DeliveryLedger:
    def __init__(self, config: Config) -> None:
        try:
            from azure.data.tables import TableClient
        except ImportError as exc:
            raise RuntimeError("Install the 'azure' extra to use delivery ledger") from exc
        self._table = TableClient(
            endpoint=table_endpoint_from_storage_url(config.azure_storage_account_url),
            table_name=config.alert_deliveries_table,
            credential=default_azure_credential(),
        )

    @staticmethod
    def partition_key(recipient_id: str) -> str:
        return "u-" + hashlib.sha256(recipient_id.encode("utf-8")).hexdigest()[:24]

    @staticmethod
    def operation_id(delivery_id: str) -> str:
        return str(uuid.uuid5(_ACS_OPERATION_NAMESPACE, delivery_id))

    def create_if_absent(self, job: EmailJob) -> None:
        try:
            from azure.core.exceptions import ResourceExistsError
        except ImportError as exc:
            raise RuntimeError("Install the 'azure' extra to use delivery ledger") from exc
        now = utc_now_iso()
        try:
            self._table.create_entity(
                {
                    "PartitionKey": self.partition_key(job.recipient_id),
                    "RowKey": job.event_id,
                    "deliveryId": job.delivery_id,
                    "recipientId": job.recipient_id,
                    "status": "pending",
                    "attempts": 0,
                    "acsOperationId": self.operation_id(job.delivery_id),
                    "createdAt": now,
                    "updatedAt": now,
                }
            )
        except ResourceExistsError:
            return

    def get(self, job: EmailJob) -> dict[str, Any]:
        return self._table.get_entity(self.partition_key(job.recipient_id), job.event_id)

    def claim(
        self,
        job: EmailJob,
        *,
        lease_seconds: int = 240,
        count_attempt: bool = True,
    ) -> DeliveryClaim:
        try:
            from azure.core import MatchConditions
            from azure.core.exceptions import ResourceModifiedError
            from azure.data.tables import UpdateMode
        except ImportError as exc:
            raise RuntimeError("Install the 'azure' extra to use delivery ledger") from exc

        self.create_if_absent(job)
        entity = self.get(job)
        status = str(entity.get("status") or "pending")
        operation_id = str(entity.get("acsOperationId") or self.operation_id(job.delivery_id))
        attempts = int(entity.get("attempts") or 0)
        first_sent_at = str(entity.get("firstSentAt") or "")
        if status in NO_RESEND_STATUSES:
            return DeliveryClaim(False, status, operation_id, attempts, "", first_sent_at)

        if status == "sending":
            lease_until = _parse_datetime(entity.get("leaseUntil"))
            if lease_until is not None and lease_until > datetime.now(timezone.utc):
                retry_after = max(
                    1,
                    math.ceil((lease_until - datetime.now(timezone.utc)).total_seconds()) + 1,
                )
                return DeliveryClaim(
                    False,
                    status,
                    operation_id,
                    attempts,
                    str(entity.get("leaseOwner") or ""),
                    first_sent_at,
                    retry_after,
                )

        now = datetime.now(timezone.utc)
        if count_attempt and not first_sent_at:
            # Repeatability-First-Sent must describe the first ACS request, not
            # when the delivery row happened to be queued.
            first_sent_at = now.isoformat()
        lease_owner = str(uuid.uuid4())
        next_attempts = attempts + 1 if count_attempt else attempts
        update = {
            "PartitionKey": self.partition_key(job.recipient_id),
            "RowKey": job.event_id,
            "status": "sending",
            "attempts": next_attempts,
            "acsOperationId": operation_id,
            "leaseOwner": lease_owner,
            "leaseUntil": (now + timedelta(seconds=lease_seconds)).isoformat(),
            "updatedAt": now.isoformat(),
        }
        if first_sent_at:
            update["firstSentAt"] = first_sent_at
        etag = _etag(entity)
        if not etag:
            raise RuntimeError("Delivery ledger entity is missing its ETag")
        try:
            self._table.update_entity(
                update,
                mode=UpdateMode.MERGE,
                etag=etag,
                match_condition=MatchConditions.IfNotModified,
            )
        except ResourceModifiedError:
            return DeliveryClaim(
                False,
                "contended",
                operation_id,
                attempts,
                "",
                first_sent_at,
                5,
            )
        return DeliveryClaim(
            True,
            "sending",
            operation_id,
            next_attempts,
            lease_owner,
            first_sent_at,
            0,
        )

    def bind_payload(
        self,
        job: EmailJob,
        *,
        claim_owner: str,
        payload_fingerprint: str,
    ) -> bool:
        """Bind an exact outbound payload to the first ACS attempt.

        Returns false when a retry would reuse the deterministic ACS operation
        ID with a different payload. Only the non-reversible, delivery-scoped
        fingerprint is stored.
        """
        try:
            from azure.core import MatchConditions
            from azure.core.exceptions import ResourceModifiedError
            from azure.data.tables import UpdateMode
        except ImportError as exc:
            raise RuntimeError("Install the 'azure' extra to use delivery ledger") from exc
        if not claim_owner or len(payload_fingerprint) != 64:
            raise ValueError("A claimed delivery and SHA-256 payload fingerprint are required")
        for _attempt in range(3):
            current = self.get(job)
            if str(current.get("status") or "") != "sending":
                return False
            if str(current.get("leaseOwner") or "") != claim_owner:
                return False
            existing = str(current.get("payloadFingerprint") or "")
            if existing:
                return existing == payload_fingerprint
            etag = _etag(current)
            if not etag:
                raise RuntimeError("Delivery ledger entity is missing its ETag")
            try:
                self._table.update_entity(
                    {
                        "PartitionKey": self.partition_key(job.recipient_id),
                        "RowKey": job.event_id,
                        "payloadFingerprint": payload_fingerprint,
                        "updatedAt": utc_now_iso(),
                    },
                    mode=UpdateMode.MERGE,
                    etag=etag,
                    match_condition=MatchConditions.IfNotModified,
                )
                return True
            except ResourceModifiedError:
                continue
        raise RuntimeError("Delivery payload binding changed concurrently; retry the message")

    def mark_accepted(
        self,
        job: EmailJob,
        *,
        acs_status: str = "accepted",
        acs_message_id: str = "",
        claim_owner: str = "",
    ) -> None:
        self._merge(
            job,
            expected_owner=claim_owner,
            status="accepted",
            acceptedAt=utc_now_iso(),
            acsStatus=acs_status,
            acsMessageId=acs_message_id,
            leaseUntil="",
            leaseOwner="",
        )

    def mark_sent(
        self,
        job: EmailJob,
        *,
        acs_status: str = "accepted",
        claim_owner: str = "",
    ) -> None:
        """Compatibility alias for callers deployed before final reports."""
        self.mark_accepted(
            job,
            acs_status=acs_status,
            acs_message_id=self.operation_id(job.delivery_id),
            claim_owner=claim_owner,
        )

    def record_delivery_report(
        self,
        job: EmailJob,
        *,
        report_status: str,
        report_at: str,
        event_grid_event_id: str,
        acs_message_id: str,
    ) -> bool:
        """Apply one recipient-level ACS report with ETag/id/time guards.

        A later asynchronous bounce is allowed to replace an earlier delivered
        result. An older out-of-order event can never undo newer evidence.
        Event Grid retries are idempotent by event ID.
        """
        try:
            from azure.core import MatchConditions
            from azure.core.exceptions import ResourceModifiedError
            from azure.data.tables import UpdateMode
        except ImportError as exc:
            raise RuntimeError("Install the 'azure' extra to use delivery ledger") from exc
        if report_status not in FINAL_DELIVERY_STATUSES:
            raise ValueError("Unsupported final delivery status")
        report_time = _parse_datetime(report_at)
        if report_time is None:
            raise ValueError("A valid delivery report timestamp is required")
        try:
            message_id = str(uuid.UUID(acs_message_id))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValueError("A valid ACS message id is required") from exc
        if not event_grid_event_id or len(event_grid_event_id) > 160:
            raise ValueError("A bounded Event Grid event id is required")

        for _attempt in range(4):
            current = self.get(job)
            if str(current.get("deliveryReportEventId") or "") == event_grid_event_id:
                return False
            existing_time = _parse_datetime(current.get("deliveryReportAt"))
            if existing_time is not None and existing_time > report_time:
                return False
            # A report is useful for legacy ``sent`` and current accepted rows,
            # and can supersede a previous final report by timestamp. It must
            # not overwrite a business-rule suppression or synchronous failure.
            current_status = str(current.get("status") or "pending")
            if current_status in {"suppressed", "failed"}:
                return False
            expected_message_id = str(
                current.get("acsMessageId")
                or current.get("acsOperationId")
                or self.operation_id(job.delivery_id)
            )
            if expected_message_id != message_id:
                raise RuntimeError("Delivery report message id does not match the ledger")
            etag = _etag(current)
            if not etag:
                raise RuntimeError("Delivery ledger entity is missing its ETag")
            try:
                self._table.update_entity(
                    {
                        "PartitionKey": self.partition_key(job.recipient_id),
                        "RowKey": job.event_id,
                        "status": report_status,
                        "deliveryStatus": report_status,
                        "deliveryReportAt": report_time.isoformat(),
                        "deliveryReportEventId": event_grid_event_id,
                        "acsMessageId": message_id,
                        "finalizedAt": utc_now_iso(),
                        "leaseUntil": "",
                        "leaseOwner": "",
                        "updatedAt": utc_now_iso(),
                    },
                    mode=UpdateMode.MERGE,
                    etag=etag,
                    match_condition=MatchConditions.IfNotModified,
                )
                return True
            except ResourceModifiedError:
                continue
        raise RuntimeError("Delivery report changed concurrently; retry the event")

    def mark_suppressed(
        self,
        job: EmailJob,
        reason: str,
        *,
        claim_owner: str = "",
    ) -> None:
        self._merge(
            job,
            expected_owner=claim_owner,
            status="suppressed",
            suppressedAt=utc_now_iso(),
            lastErrorCode=reason[:120],
            leaseUntil="",
            leaseOwner="",
        )

    def mark_retryable(self, job: EmailJob, reason: str, *, claim_owner: str = "") -> None:
        self._merge(
            job,
            expected_owner=claim_owner,
            status="pending",
            lastErrorCode=reason[:120],
            leaseUntil="",
            leaseOwner="",
        )

    def mark_failed(self, job: EmailJob, reason: str, *, claim_owner: str = "") -> None:
        self._merge(
            job,
            expected_owner=claim_owner,
            status="failed",
            failedAt=utc_now_iso(),
            lastErrorCode=reason[:120],
            leaseUntil="",
            leaseOwner="",
        )

    def _merge(self, job: EmailJob, *, expected_owner: str = "", **values: Any) -> None:
        try:
            from azure.core import MatchConditions
            from azure.core.exceptions import ResourceModifiedError
            from azure.data.tables import UpdateMode
        except ImportError as exc:
            raise RuntimeError("Install the 'azure' extra to use delivery ledger") from exc
        target_status = str(values.get("status") or "")
        if expected_owner:
            allowed_from = {
                "accepted": {"sending"},
                "sent": {"sending"},
                "failed": {"pending", "sending"},
                "pending": {"sending"},
            }.get(target_status, {"sending"})
        else:
            allowed_from = {
                "suppressed": {"pending"},
                "failed": {"pending"},
            }.get(target_status, {"pending"})
        for _attempt in range(3):
            current = self.get(job)
            current_status = str(current.get("status") or "pending")
            if expected_owner and str(current.get("leaseOwner") or "") != expected_owner:
                return
            if current_status == target_status:
                return
            # Delivery state is monotonic. In particular, a late transient
            # failure can never move an already-sent delivery back to pending.
            if current_status not in allowed_from:
                return
            update = dict(values)
            update.update(
                PartitionKey=self.partition_key(job.recipient_id),
                RowKey=job.event_id,
                updatedAt=utc_now_iso(),
            )
            kwargs = {"mode": UpdateMode.MERGE}
            etag = _etag(current)
            if not etag:
                raise RuntimeError("Delivery ledger entity is missing its ETag")
            kwargs.update(etag=etag, match_condition=MatchConditions.IfNotModified)
            try:
                self._table.update_entity(update, **kwargs)
                return
            except ResourceModifiedError:
                continue
        raise RuntimeError("Delivery state changed concurrently; retry the message")


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _etag(entity: Any) -> str | None:
    metadata = getattr(entity, "metadata", None)
    if isinstance(metadata, dict) and metadata.get("etag"):
        return str(metadata["etag"])
    if isinstance(entity, dict):
        for key in ("etag", "odata.etag", "@odata.etag"):
            if entity.get(key):
                return str(entity[key])
    return None
