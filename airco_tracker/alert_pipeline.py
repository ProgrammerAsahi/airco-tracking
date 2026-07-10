from __future__ import annotations

import logging
import threading
import time
from copy import copy
from dataclasses import is_dataclass, replace
from datetime import datetime, timedelta, timezone

from .alert_events import EmailJob, FanoutShardJob, StockAvailableEvent, recipient_shard
from .config import Config
from .deliveries import DeliveryLedger
from .delivery_coverage import coverage_reaches_country
from .mailer import PermanentEmailError, build_message, message_fingerprint, send_message
from .outbox import AzureTableOutbox, build_outbox
from .recipient_projection import ProjectedRecipient, RecipientProjection
from .service_bus import PermanentMessageError, process_receiver, send_json_messages, service_bus_client


LOG = logging.getLogger(__name__)
_EMAIL_RATE_LOCK = threading.Lock()
_LAST_EMAIL_SEND_MONOTONIC = 0.0


class OutboxPublisher:
    def __init__(self, config: Config, *, outbox: AzureTableOutbox | None = None) -> None:
        self.config = config
        self.outbox = outbox or build_outbox(config)

    def publish_pending(self, *, limit: int = 100) -> int:
        records = self.outbox.pending(limit=limit)
        if not records:
            LOG.info("Alert outbox is empty")
            return 0
        event_ids = [record.event.event_id for record in records]
        # Group the bounded oldest-first page by its deterministic partition
        # bucket so partition-safe Service Bus batching still reduces network
        # round trips. This does not change business ordering: stock events are
        # generation-addressed and consumers are idempotent.
        partition_ordered = sorted(
            records,
            key=lambda record: (record.event.event_id[:2], record.event.created_at),
        )
        try:
            with service_bus_client(self.config) as client:
                with client.get_topic_sender(self.config.stock_events_topic) as sender:
                    count = send_json_messages(
                        sender,
                        (
                            (
                                record.event.event_id,
                                record.event.event_type,
                                record.event.to_json(),
                                f"stock-{record.event.event_id[:2]}",
                            )
                            for record in partition_ordered
                        ),
                    )
            self.outbox.mark_published(event_ids)
            LOG.info("Published %d stock event(s) from the outbox", count)
            return count
        except Exception as exc:
            try:
                self.outbox.mark_attempt_failed(event_ids, type(exc).__name__)
            except Exception:
                LOG.exception("Could not record failed outbox publish attempt")
            raise


class FanoutCoordinator:
    def __init__(self, config: Config) -> None:
        self.config = config

    def handle(self, event: StockAvailableEvent) -> int:
        if event.test_only:
            targets_by_shard: dict[int, list[str]] = {}
            for recipient_id in event.target_recipient_ids:
                shard = recipient_shard(recipient_id, self.config.recipient_shard_count)
                targets_by_shard.setdefault(shard, []).append(recipient_id)
            jobs = [
                FanoutShardJob(event.event_id, shard, tuple(targets))
                for shard, targets in sorted(targets_by_shard.items())
            ]
        else:
            jobs = [
                FanoutShardJob(event.event_id, shard)
                for shard in range(self.config.recipient_shard_count)
            ]

        with service_bus_client(self.config) as client:
            with client.get_queue_sender(self.config.fanout_jobs_queue) as sender:
                count = send_json_messages(
                    sender,
                    (
                        (
                            f"{event.event_id}:shard:{job.shard:02d}",
                            "email.fanout.shard.v1",
                            job.to_json(),
                            f"event-{event.event_id[:32]}",
                        )
                        for job in jobs
                    ),
                )
        LOG.info("Created %d fan-out shard job(s) for event %s", count, event.event_id[:12])
        return count


class FanoutWorker:
    def __init__(
        self,
        config: Config,
        *,
        outbox: AzureTableOutbox | None = None,
        recipients: RecipientProjection | None = None,
    ) -> None:
        self.config = config
        self.outbox = outbox or build_outbox(config)
        self.recipients = recipients or RecipientProjection(config)

    def handle(self, job: FanoutShardJob) -> int:
        if job.shard < 0 or job.shard >= self.config.recipient_shard_count:
            raise PermanentMessageError("Fan-out shard is outside configured range")
        try:
            event = self.outbox.get(job.event_id).event
        except Exception as exc:
            if _is_not_found(exc):
                raise PermanentMessageError("Stock event is missing from outbox") from exc
            raise

        if _event_expired(event, getattr(self.config, "alert_event_max_age_seconds", 21600)):
            LOG.warning("Suppressing stale stock event %s before fan-out", event.event_id[:12])
            return 0

        if event.test_only != bool(job.target_recipient_ids):
            raise PermanentMessageError("Test fan-out job target mismatch")

        if job.target_recipient_ids:
            candidates = []
            for recipient_id in job.target_recipient_ids:
                if recipient_shard(recipient_id, self.config.recipient_shard_count) != job.shard:
                    raise PermanentMessageError("Target recipient is in the wrong shard")
                recipient = self.recipients.get(recipient_id)
                if recipient is not None:
                    candidates.append(recipient)
        else:
            candidates = self.recipients.iter_shard(job.shard)

        def matching_jobs():
            for recipient in candidates:
                if _event_matches_recipient(event, recipient):
                    yield EmailJob.create(event.event_id, recipient.recipient_id)

        with service_bus_client(self.config) as client:
            with client.get_queue_sender(self.config.email_jobs_queue) as sender:
                count = send_json_messages(
                    sender,
                    (
                        (
                            email_job.delivery_id,
                            "email.delivery.v1",
                            email_job.to_json(),
                            f"r-{job.shard:02x}",
                        )
                        for email_job in matching_jobs()
                    ),
                )
        if count:
            LOG.info("Fan-out shard %02d queued %d email job(s)", job.shard, count)
        else:
            LOG.info("Fan-out shard %02d produced no email jobs", job.shard)
        return count


class EmailWorker:
    def __init__(
        self,
        config: Config,
        *,
        outbox: AzureTableOutbox | None = None,
        recipients: RecipientProjection | None = None,
        ledger: DeliveryLedger | None = None,
    ) -> None:
        self.config = config
        self.outbox = outbox or build_outbox(config)
        self.recipients = recipients or RecipientProjection(config)
        self.ledger = ledger or DeliveryLedger(config)

    def handle(self, job: EmailJob) -> None:
        self.ledger.create_if_absent(job)
        try:
            event = self.outbox.get(job.event_id).event
        except Exception as exc:
            if _is_not_found(exc):
                self._mark_terminal(job, "failed", "event_not_found")
                raise PermanentMessageError("Stock event is missing from outbox") from exc
            raise

        recipient = self.recipients.get_authoritative(job.recipient_id)
        if recipient is None:
            self._mark_terminal(job, "suppressed", "recipient_not_found")
            return
        if _event_expired(event, getattr(self.config, "alert_event_max_age_seconds", 21600)):
            self._mark_terminal(job, "suppressed", "event_expired")
            return
        if event.test_only:
            if job.recipient_id not in event.target_recipient_ids:
                self._mark_terminal(job, "failed", "test_target_mismatch")
                raise PermanentMessageError("Test event target mismatch")
        elif not _event_matches_recipient(event, recipient):
            self._mark_terminal(job, "suppressed", "entitlement_or_country_changed")
            return

        # The rate limiter can intentionally pause a delivery for several
        # seconds. Re-resolve the projection after that wait so an account
        # deletion, email change, cancellation, expiry, or country change that
        # happens while the message is queued can never use the stale address
        # read above. The first read avoids spending scarce sender quota slots
        # on obviously ineligible deliveries; this second read is the
        # authoritative send-time check.
        _wait_for_email_rate_limit(
            getattr(self.config, "email_min_send_interval_seconds", 0)
        )
        recipient = self.recipients.get_authoritative(job.recipient_id)
        if recipient is None:
            self._mark_terminal(job, "suppressed", "recipient_not_found_before_send")
            return
        if _event_expired(event, getattr(self.config, "alert_event_max_age_seconds", 21600)):
            self._mark_terminal(job, "suppressed", "event_expired_before_send")
            return
        if event.test_only:
            if job.recipient_id not in event.target_recipient_ids:
                self._mark_terminal(job, "failed", "test_target_mismatch_before_send")
                raise PermanentMessageError("Test event target mismatch before send")
        elif not _event_matches_recipient(event, recipient):
            self._mark_terminal(
                job,
                "suppressed",
                "entitlement_or_country_changed_before_send",
            )
            return

        claim = self.ledger.claim(job)
        if not self._claim_is_ready(job, claim):
            return

        if claim.attempts > 5:
            self.ledger.mark_failed(
                job,
                "retry_budget_already_exhausted",
                claim_owner=claim.lease_owner,
            )
            raise PermanentMessageError("Email retry budget exhausted")

        recipient_config = _config_for_projected_recipient(self.config, recipient)
        try:
            message = build_message(
                recipient_config,
                [] if event.test_only else [event.product],
                test=event.test_only,
            )
            fingerprint = message_fingerprint(
                recipient_config,
                message,
                delivery_id=job.delivery_id,
            )
            if not self.ledger.bind_payload(
                job,
                claim_owner=claim.lease_owner,
                payload_fingerprint=fingerprint,
            ):
                # Reusing one ACS repeatability/operation ID with a different
                # exact payload is ambiguous. Prefer one missed alert over a
                # possible retry to a stale address; a later stock event gets
                # a fresh delivery ID.
                self.ledger.mark_suppressed(
                    job,
                    "recipient_payload_changed_after_attempt",
                    claim_owner=claim.lease_owner,
                )
                return
            result = send_message(
                recipient_config,
                message,
                operation_id=claim.operation_id,
                repeatability_first_sent=claim.first_sent_at,
            )
        except Exception as exc:
            code = _error_code(exc)
            if _is_permanent_email_error(exc):
                self.ledger.mark_failed(job, code, claim_owner=claim.lease_owner)
                raise PermanentMessageError(f"Permanent email delivery error: {code}") from exc
            if claim.attempts >= 5:
                self.ledger.mark_failed(
                    job,
                    f"retry_exhausted_{code}",
                    claim_owner=claim.lease_owner,
                )
                raise PermanentMessageError(f"Email retry budget exhausted: {code}") from exc
            self.ledger.mark_retryable(job, code, claim_owner=claim.lease_owner)
            try:
                _schedule_email_retry(
                    self.config,
                    job,
                    attempt=claim.attempts,
                    delay_override=_retry_after_seconds(exc),
                )
            except Exception:
                # Scheduling is part of the hand-off. If it fails, abandon the
                # original Service Bus message so no delivery can be lost.
                raise exc
            LOG.warning(
                "Scheduled retry %d for email delivery %s after %s",
                claim.attempts,
                job.delivery_id[:12],
                code,
            )
            return

        self.ledger.mark_sent(
            job,
            acs_status=result.status,
            claim_owner=claim.lease_owner,
        )
        LOG.info(
            "Email delivery %s accepted",
            job.delivery_id[:12],
        )

    def _claim_is_ready(self, job: EmailJob, claim) -> bool:
        if claim.claimed:
            return True
        if claim.status in {"sent", "suppressed", "failed"}:
            return False
        # Do not hot-abandon a duplicate while another worker owns the ledger
        # lease: repeated immediate abandons can exhaust maxDeliveryCount
        # before the lease expires. Schedule one deterministic recovery copy
        # and complete this duplicate instead.
        _schedule_email_retry(
            self.config,
            job,
            attempt=max(1, claim.attempts),
            delay_override=max(5, claim.retry_after_seconds),
            retry_kind="claim",
        )
        LOG.info(
            "Deferred contended email delivery %s (%s)",
            job.delivery_id[:12],
            claim.status,
        )
        return False

    def _mark_terminal(self, job: EmailJob, status: str, reason: str) -> None:
        # A prior worker may have crashed after claiming the row. Claiming a
        # pending or expired-sending row before suppression/failure ensures the
        # ledger reaches a terminal state; an active owner is left alone and a
        # deterministic recovery is scheduled for after its lease.
        claim = self.ledger.claim(job, count_attempt=False)
        if not self._claim_is_ready(job, claim):
            return
        if status == "suppressed":
            self.ledger.mark_suppressed(
                job,
                reason,
                claim_owner=claim.lease_owner,
            )
        elif status == "failed":
            self.ledger.mark_failed(
                job,
                reason,
                claim_owner=claim.lease_owner,
            )
        else:
            raise ValueError("Unsupported terminal delivery status")


def run_fanout_coordinator(config: Config, *, once: bool) -> int:
    worker = FanoutCoordinator(config)

    def handler(payload: bytes, _message) -> None:
        try:
            event = StockAvailableEvent.from_json(payload)
        except ValueError as exc:
            raise PermanentMessageError(str(exc)) from exc
        worker.handle(event)

    return _run_receiver(
        config,
        once=once,
        receiver_factory=lambda client, renewer: client.get_subscription_receiver(
            topic_name=config.stock_events_topic,
            subscription_name=config.stock_events_subscription,
            max_wait_time=20,
            prefetch_count=1,
            auto_lock_renewer=renewer,
        ),
        handler=handler,
        max_messages=1,
    )


def run_fanout_worker(config: Config, *, once: bool) -> int:
    worker = FanoutWorker(config)

    def handler(payload: bytes, _message) -> None:
        try:
            job = FanoutShardJob.from_json(payload)
        except ValueError as exc:
            raise PermanentMessageError(str(exc)) from exc
        worker.handle(job)

    return _run_receiver(
        config,
        once=once,
        receiver_factory=lambda client, renewer: client.get_queue_receiver(
            queue_name=config.fanout_jobs_queue,
            max_wait_time=20,
            prefetch_count=1,
            auto_lock_renewer=renewer,
        ),
        handler=handler,
        max_messages=1,
    )


def run_email_worker(config: Config, *, once: bool) -> int:
    worker = EmailWorker(config)

    def handler(payload: bytes, _message) -> None:
        try:
            job = EmailJob.from_json(payload)
        except ValueError as exc:
            raise PermanentMessageError(str(exc)) from exc
        worker.handle(job)

    return _run_receiver(
        config,
        once=once,
        receiver_factory=lambda client, renewer: client.get_queue_receiver(
            queue_name=config.email_jobs_queue,
            max_wait_time=20,
            prefetch_count=4,
            auto_lock_renewer=renewer,
        ),
        handler=handler,
        max_messages=4,
    )


def _run_receiver(
    config: Config,
    *,
    once: bool,
    receiver_factory,
    handler,
    max_messages: int,
) -> int:
    processed_total = 0
    try:
        from azure.servicebus import AutoLockRenewer
    except ImportError as exc:
        raise RuntimeError("Install the 'azure' extra to renew Service Bus locks") from exc
    with AutoLockRenewer(max_lock_renewal_duration=900) as renewer:
        with service_bus_client(config) as client:
            with receiver_factory(client, renewer) as receiver:
                while True:
                    processed = process_receiver(
                        receiver,
                        handler,
                        max_messages=max_messages,
                        max_wait_time=20,
                    )
                    processed_total += processed
                    if once:
                        return processed_total
                    if not processed:
                        time.sleep(1)


def _event_matches_recipient(event: StockAvailableEvent, recipient: ProjectedRecipient) -> bool:
    if event.test_only:
        return recipient.recipient_id in event.target_recipient_ids
    if not recipient.entitled():
        return False
    if not recipient.delivery_country:
        # A missing/corrupt destination must never broaden a production alert
        # to every country. Targeted pipeline tests are handled above.
        return False
    coverage = event.delivery_coverage or (event.product.country,)
    return coverage_reaches_country(coverage, recipient.delivery_country)


def _event_expired(event: StockAvailableEvent, max_age_seconds: int) -> bool:
    if max_age_seconds <= 0:
        return False
    try:
        created = datetime.fromisoformat(event.created_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - created > timedelta(seconds=max_age_seconds)


def _config_for_projected_recipient(config: Config, recipient: ProjectedRecipient) -> Config:
    if is_dataclass(config):
        return replace(config, email_to=recipient.email, email_lang=recipient.language)
    test_config = copy(config)
    setattr(test_config, "email_to", recipient.email)
    setattr(test_config, "email_lang", recipient.language)
    return test_config


def _is_not_found(exc: Exception) -> bool:
    if type(exc).__name__ == "ResourceNotFoundError":
        return True
    return getattr(exc, "status_code", None) == 404


def _error_code(exc: Exception) -> str:
    status = getattr(exc, "status_code", None)
    if status:
        return f"{type(exc).__name__}_{status}"
    return type(exc).__name__


def _is_permanent_email_error(exc: Exception) -> bool:
    if isinstance(exc, PermanentEmailError):
        return True
    status = getattr(exc, "status_code", None)
    if status is None:
        return False
    try:
        status = int(status)
    except (TypeError, ValueError):
        return False
    return 400 <= status < 500 and status not in {408, 409, 425, 429}


def _schedule_email_retry(
    config: Config,
    job: EmailJob,
    *,
    attempt: int,
    delay_override: int | None = None,
    retry_kind: str = "delivery",
) -> None:
    try:
        from azure.servicebus import ServiceBusMessage
    except ImportError as exc:
        raise RuntimeError("Install the 'azure' extra to schedule email retries") from exc
    delays = (30, 120, 600, 1800, 3600)
    delay = delay_override or delays[min(max(attempt - 1, 0), len(delays) - 1)]
    scheduled_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
    message = ServiceBusMessage(
        job.to_json(),
        message_id=f"{job.delivery_id}:retry:{retry_kind}:{attempt}",
        subject=f"email.delivery.retry.{retry_kind}.v1",
        content_type="application/json",
        partition_key=(
            f"r-{recipient_shard(job.recipient_id, config.recipient_shard_count):02x}"
        ),
    )
    with service_bus_client(config) as client:
        with client.get_queue_sender(config.email_jobs_queue) as sender:
            sender.schedule_messages(message, scheduled_at)


def _retry_after_seconds(exc: Exception) -> int | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers:
        value = headers.get("Retry-After") or headers.get("retry-after")
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            pass
    if getattr(exc, "status_code", None) == 429:
        # The current Azure-managed sender domain has an hourly hard quota.
        return 3600
    return None


def _wait_for_email_rate_limit(interval_seconds: float) -> None:
    if interval_seconds <= 0:
        return
    global _LAST_EMAIL_SEND_MONOTONIC
    with _EMAIL_RATE_LOCK:
        now = time.monotonic()
        remaining = interval_seconds - (now - _LAST_EMAIL_SEND_MONOTONIC)
        if remaining > 0:
            time.sleep(remaining)
        _LAST_EMAIL_SEND_MONOTONIC = time.monotonic()
