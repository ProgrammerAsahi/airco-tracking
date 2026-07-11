from __future__ import annotations

import io
import unittest
import uuid
from contextlib import contextmanager, redirect_stdout
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from azure.servicebus.exceptions import MessageSizeExceededError

from airco_tracker.adapters.registry import AdapterSpec
from airco_tracker.alert_events import EmailJob, FanoutShardJob, StockAvailableEvent, recipient_shard
from airco_tracker.alert_pipeline import (
    EmailWorker,
    FanoutWorker,
    PermanentMessageError,
    _validate_email_worker_runtime,
    purge_delivery_report_dead_letters,
    run_delivery_report_worker,
)
from airco_tracker.cli import check
from airco_tracker.deliveries import DeliveryClaim
from airco_tracker.mailer import SendResult
from airco_tracker.models import Product
from airco_tracker.outbox import OutboxRecord
from airco_tracker.recipient_projection import ProjectedRecipient
from airco_tracker.service_bus import process_receiver, send_json_messages


_RECIPIENT_NAMESPACE = uuid.UUID("8b9a92ef-8917-46f9-a195-85472621d89b")


def _recipient_id(label: str) -> str:
    return str(uuid.uuid5(_RECIPIENT_NAMESPACE, label))


class _AvailableAdapter:
    site = "Pipeline shop"
    delivery_coverage = frozenset({"fr"})

    def __init__(self, _fetcher, *args, **kwargs) -> None:
        pass

    def fetch_products(self):
        return [
            Product(
                self.site,
                "Available airco",
                "https://shop.test/available",
                True,
                399.0,
                "Tomorrow",
                9000,
                country="fr",
            )
        ]


class _TwoAvailableAdapter(_AvailableAdapter):
    def fetch_products(self):
        products = super().fetch_products()
        return products + [
            Product(
                self.site,
                "Second airco",
                "https://shop.test/available-2",
                True,
                499.0,
                "Tomorrow",
                10000,
                country="fr",
            )
        ]


class _InventoryStore:
    def load(self):
        return {"version": 1, "sites": {}}

    def save(self, _snapshot) -> None:
        return None


def _scanner_config() -> SimpleNamespace:
    return SimpleNamespace(
        request_timeout_seconds=1,
        alert_on_first_seen=True,
        max_price_eur=None,
        min_btu=None,
        countries=["fr"],
        alert_dispatch_backend="service_bus",
        validate_alert_pipeline=lambda: None,
    )


def _event(*, country: str = "fr", coverage: set[str] | None = None) -> StockAvailableEvent:
    product = Product(
        "Pipeline shop",
        "Available airco",
        "https://shop.test/available",
        True,
        399.0,
        "Tomorrow",
        9000,
        country=country,
    )
    return StockAvailableEvent.for_product(
        product,
        availability_generation=1,
        delivery_coverage=coverage or {country},
    )


def _recipient(
    recipient_id: str,
    *,
    email: str | None = None,
    country: str = "fr",
    enabled: bool = True,
    expires_delta: timedelta = timedelta(days=30),
) -> ProjectedRecipient:
    recipient_id = _recipient_id(recipient_id)
    return ProjectedRecipient(
        recipient_id=recipient_id,
        email=email or f"{recipient_id}@example.com",
        language="en",
        delivery_country=country,
        plan="monthly_priority",
        status="active",
        entitlement_end=(datetime.now(timezone.utc) + expires_delta).isoformat(),
        enabled=enabled,
    )


def _test_email_message() -> EmailMessage:
    message = EmailMessage()
    message["From"] = "sender@example.com"
    message["To"] = "recipient@example.com"
    message["Subject"] = "Airco available"
    message.set_content("An air conditioner is available.")
    message.add_alternative("<p>An air conditioner is available.</p>", subtype="html")
    return message


@contextmanager
def _fake_service_bus(sender):
    client = MagicMock()
    client.get_queue_sender.return_value.__enter__.return_value = sender
    client.get_queue_sender.return_value.__exit__.return_value = False
    yield client


class ScannerOutboxTests(unittest.TestCase):
    def test_scanner_writes_outbox_before_committing_alert_state(self) -> None:
        events: list[str] = []
        state_store = MagicMock()
        state_store.load.return_value = {"version": 1, "products": {}}
        state_store.save.side_effect = lambda _state: events.append("state")
        outbox = MagicMock()
        outbox.create_if_absent.side_effect = lambda _event: events.append("outbox") or True

        with (
            patch(
                "airco_tracker.cli.load_adapter_specs",
                return_value=[AdapterSpec(country="fr", adapter_class=_AvailableAdapter)],
            ),
            patch("airco_tracker.cli.build_state_store", return_value=state_store),
            patch("airco_tracker.cli.build_inventory_store", return_value=_InventoryStore()),
            patch("airco_tracker.cli.build_outbox", return_value=outbox),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(check(_scanner_config(), dry_run=False, show_all=False), 0)

        self.assertEqual(events, ["outbox", "state"])
        queued_event = outbox.create_if_absent.call_args.args[0]
        self.assertEqual(queued_event.availability_generation, 1)
        self.assertEqual(queued_event.delivery_coverage, ("fr",))

    def test_scanner_does_not_commit_state_when_any_outbox_write_fails(self) -> None:
        state_store = MagicMock()
        state_store.load.return_value = {"version": 1, "products": {}}
        outbox = MagicMock()
        outbox.create_if_absent.side_effect = [True, RuntimeError("table unavailable")]

        with (
            patch(
                "airco_tracker.cli.load_adapter_specs",
                return_value=[AdapterSpec(country="fr", adapter_class=_TwoAvailableAdapter)],
            ),
            patch("airco_tracker.cli.build_state_store", return_value=state_store),
            patch("airco_tracker.cli.build_inventory_store", return_value=_InventoryStore()),
            patch("airco_tracker.cli.build_outbox", return_value=outbox),
            redirect_stdout(io.StringIO()),
        ):
            with self.assertRaisesRegex(RuntimeError, "table unavailable"):
                check(_scanner_config(), dry_run=False, show_all=False)

        self.assertEqual(outbox.create_if_absent.call_count, 2)
        state_store.save.assert_not_called()


class FanoutWorkerTests(unittest.TestCase):
    def _run_worker(self, event, candidates, job):
        outbox = MagicMock()
        outbox.get.return_value = OutboxRecord(event=event, status="published")
        recipients = MagicMock()
        recipients.iter_shard.return_value = iter(candidates)
        recipients.get.side_effect = lambda recipient_id: next(
            (recipient for recipient in candidates if recipient.recipient_id == recipient_id), None
        )
        sender = MagicMock()
        captured = []

        def capture(_sender, messages):
            captured.extend(list(messages))
            return len(captured)

        config = SimpleNamespace(
            recipient_shard_count=32,
            fanout_jobs_queue="fanout-jobs",
            email_jobs_queue="email-jobs",
        )
        worker = FanoutWorker(config, outbox=outbox, recipients=recipients)
        with (
            patch("airco_tracker.alert_pipeline.service_bus_client", side_effect=lambda _config: _fake_service_bus(sender)),
            patch("airco_tracker.alert_pipeline.send_json_messages", side_effect=capture),
        ):
            count = worker.handle(job)
        return count, captured

    def test_production_fanout_filters_by_entitlement_and_delivery_country(self) -> None:
        event = _event(coverage={"fr"})
        candidates = [
            _recipient("eligible-fr"),
            _recipient("wrong-country", country="nl"),
            _recipient("expired", expires_delta=timedelta(seconds=-1)),
            _recipient("disabled", enabled=False),
        ]

        count, captured = self._run_worker(event, candidates, FanoutShardJob(event.event_id, 7))

        self.assertEqual(count, 1)
        self.assertEqual(len(captured), 1)
        queued = EmailJob.from_json(captured[0][2])
        self.assertEqual(queued.recipient_id, candidates[0].recipient_id)

    def test_test_fanout_queues_only_explicit_targets_even_without_paid_entitlement(self) -> None:
        target = _recipient(
            "explicit-target",
            country="nl",
            enabled=False,
            expires_delta=timedelta(days=-1),
        )
        event = StockAvailableEvent.test_event(target_recipient_ids=[target.recipient_id])
        shard = recipient_shard(target.recipient_id, 32)

        count, captured = self._run_worker(
            event,
            [target, _recipient("not-targeted")],
            FanoutShardJob(event.event_id, shard, (target.recipient_id,)),
        )

        self.assertEqual(count, 1)
        queued = EmailJob.from_json(captured[0][2])
        self.assertEqual(queued.recipient_id, target.recipient_id)

    def test_test_fanout_rejects_a_target_assigned_to_the_wrong_shard(self) -> None:
        target_id = _recipient_id("explicit-target")
        event = StockAvailableEvent.test_event(target_recipient_ids=[target_id])
        correct = recipient_shard(target_id, 32)
        wrong = (correct + 1) % 32

        with self.assertRaisesRegex(PermanentMessageError, "wrong shard"):
            self._run_worker(
                event,
                [_recipient(target_id)],
                FanoutShardJob(event.event_id, wrong, (target_id,)),
            )

    def test_stale_test_event_is_suppressed_before_queueing_email_jobs(self) -> None:
        target = _recipient("explicit-target")
        event = StockAvailableEvent.test_event(target_recipient_ids=[target.recipient_id])
        event = replace(
            event,
            created_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
        )
        shard = recipient_shard(target.recipient_id, 32)

        count, captured = self._run_worker(
            event,
            [target],
            FanoutShardJob(event.event_id, shard, (target.recipient_id,)),
        )

        self.assertEqual(count, 0)
        self.assertEqual(captured, [])


class EmailWorkerTests(unittest.TestCase):
    def _worker(self, event, recipient, ledger=None):
        outbox = MagicMock()
        outbox.get.return_value = OutboxRecord(event=event, status="published")
        recipients = MagicMock()
        recipients.get.return_value = recipient
        recipients.get_authoritative.return_value = recipient
        if ledger is None:
            ledger = MagicMock()
            ledger.claim.return_value = DeliveryClaim(
                True,
                "sending",
                "operation-1",
                1,
                "claim-1",
                "2026-07-09T12:00:00+00:00",
            )
            ledger.bind_payload.return_value = True
        config = SimpleNamespace(
            email_from="sender@example.com",
            email_to="stale-address@example.com",
            email_lang="zh",
            app_base_url="https://airco-tracker.eu",
            email_unsubscribe_signing_key="test-signing-secret-that-is-at-least-32-bytes",
        )
        message_index = MagicMock()
        suppressions = MagicMock()
        suppressions.is_suppressed.return_value = False
        return (
            EmailWorker(
                config,
                outbox=outbox,
                recipients=recipients,
                ledger=ledger,
                message_index=message_index,
                suppressions=suppressions,
            ),
            ledger,
        )

    def test_email_worker_uses_latest_projected_address_and_marks_sent(self) -> None:
        event = _event(coverage={"fr"})
        recipient = _recipient("recipient-1", email="new-address@example.com")
        worker, ledger = self._worker(event, recipient)
        job = EmailJob.create(event.event_id, recipient.recipient_id)

        with (
            patch(
                "airco_tracker.alert_pipeline.build_message",
                return_value=_test_email_message(),
            ) as build,
            patch(
                "airco_tracker.alert_pipeline.send_message",
                return_value=SendResult("operation-1", "accepted"),
            ) as send,
        ):
            worker.handle(job)

        message_config = build.call_args.args[0]
        self.assertEqual(message_config.email_to, "new-address@example.com")
        self.assertEqual(message_config.email_lang, "en")
        self.assertEqual(send.call_args.kwargs["operation_id"], "operation-1")
        self.assertIsNotNone(build.call_args.kwargs["unsubscribe_token"])
        self.assertEqual(
            send.call_args.kwargs["repeatability_first_sent"],
            "2026-07-09T12:00:00+00:00",
        )
        ledger.mark_accepted.assert_called_once_with(
            job,
            acs_status="accepted",
            acs_message_id="operation-1",
            claim_owner="claim-1",
        )

    def test_targeted_pipeline_test_gets_unsubscribe_token(self) -> None:
        recipient = _recipient("recipient-1", email="target@example.com")
        event = StockAvailableEvent.test_event(
            target_recipient_ids=[recipient.recipient_id]
        )
        worker, _ledger = self._worker(event, recipient)
        job = EmailJob.create(event.event_id, recipient.recipient_id)

        with (
            patch(
                "airco_tracker.alert_pipeline.build_message",
                return_value=_test_email_message(),
            ) as build,
            patch(
                "airco_tracker.alert_pipeline.send_message",
                return_value=SendResult("operation-1", "accepted"),
            ),
        ):
            worker.handle(job)

        self.assertTrue(build.call_args.kwargs["test"])
        self.assertIsNotNone(build.call_args.kwargs["unsubscribe_token"])

    def test_email_worker_reloads_address_after_rate_limit_wait(self) -> None:
        event = _event(coverage={"fr"})
        before_wait = _recipient("recipient-1", email="old-address@example.com")
        after_wait = ProjectedRecipient(
            **{**before_wait.__dict__, "email": "latest-address@example.com"}
        )
        worker, _ledger = self._worker(event, before_wait)
        worker.recipients.get_authoritative.side_effect = [before_wait, after_wait]
        job = EmailJob.create(event.event_id, before_wait.recipient_id)

        with (
            patch("airco_tracker.alert_pipeline._wait_for_email_rate_limit") as wait,
            patch(
                "airco_tracker.alert_pipeline.build_message",
                return_value=_test_email_message(),
            ) as build,
            patch(
                "airco_tracker.alert_pipeline.send_message",
                return_value=SendResult("operation-1", "accepted"),
            ),
        ):
            worker.handle(job)

        wait.assert_called_once()
        self.assertEqual(build.call_args.args[0].email_to, "latest-address@example.com")

    def test_email_worker_suppresses_entitlement_lost_during_rate_limit_wait(self) -> None:
        event = _event(coverage={"fr"})
        before_wait = _recipient("recipient-1")
        after_wait = ProjectedRecipient(
            **{**before_wait.__dict__, "enabled": False}
        )
        worker, ledger = self._worker(event, before_wait)
        worker.recipients.get_authoritative.side_effect = [before_wait, after_wait]
        job = EmailJob.create(event.event_id, before_wait.recipient_id)

        with patch("airco_tracker.alert_pipeline.send_message") as send:
            worker.handle(job)

        ledger.mark_suppressed.assert_called_once_with(
            job,
            "entitlement_or_country_changed_before_send",
            claim_owner="claim-1",
        )
        ledger.claim.assert_called_once_with(job, count_attempt=False)
        send.assert_not_called()

    def test_email_worker_suppresses_when_entitlement_changes_before_delivery(self) -> None:
        event = _event(coverage={"fr"})
        recipient = _recipient("recipient-1", enabled=False)
        worker, ledger = self._worker(event, recipient)
        job = EmailJob.create(event.event_id, recipient.recipient_id)

        with patch("airco_tracker.alert_pipeline.send_message") as send:
            worker.handle(job)

        ledger.mark_suppressed.assert_called_once_with(
            job,
            "entitlement_or_country_changed",
            claim_owner="claim-1",
        )
        ledger.claim.assert_called_once_with(job, count_attempt=False)
        send.assert_not_called()

    def test_email_worker_recovers_an_expired_sending_claim_before_suppression(self) -> None:
        event = _event(coverage={"fr"})
        recipient = _recipient("recipient-1", enabled=False)
        ledger = MagicMock()
        ledger.claim.return_value = DeliveryClaim(
            True,
            "sending",
            "operation-1",
            1,
            "recovery-owner",
            "2026-07-09T12:00:00+00:00",
        )
        worker, _ledger = self._worker(event, recipient, ledger=ledger)
        job = EmailJob.create(event.event_id, recipient.recipient_id)

        with (
            patch("airco_tracker.alert_pipeline.send_message") as send,
            patch("airco_tracker.alert_pipeline._schedule_email_retry") as schedule,
        ):
            worker.handle(job)

        ledger.claim.assert_called_once_with(job, count_attempt=False)
        ledger.mark_suppressed.assert_called_once_with(
            job,
            "entitlement_or_country_changed",
            claim_owner="recovery-owner",
        )
        send.assert_not_called()
        schedule.assert_not_called()

    def test_email_worker_schedules_transient_failures_for_retry(self) -> None:
        event = _event(coverage={"fr"})
        recipient = _recipient("recipient-1")
        worker, ledger = self._worker(event, recipient)
        job = EmailJob.create(event.event_id, recipient.recipient_id)

        with (
            patch(
                "airco_tracker.alert_pipeline.build_message",
                return_value=_test_email_message(),
            ),
            patch(
                "airco_tracker.alert_pipeline.send_message",
                side_effect=RuntimeError("temporary ACS outage"),
            ),
            patch("airco_tracker.alert_pipeline._schedule_email_retry") as schedule,
        ):
            worker.handle(job)

        ledger.mark_retryable.assert_called_once_with(
            job, "RuntimeError", claim_owner="claim-1"
        )
        schedule.assert_called_once_with(
            worker.config,
            job,
            attempt=1,
            delay_override=None,
        )
        ledger.mark_accepted.assert_not_called()

    def test_email_worker_abandons_original_if_retry_scheduling_fails(self) -> None:
        event = _event(coverage={"fr"})
        recipient = _recipient("recipient-1")
        worker, ledger = self._worker(event, recipient)
        job = EmailJob.create(event.event_id, recipient.recipient_id)

        with (
            patch(
                "airco_tracker.alert_pipeline.build_message",
                return_value=_test_email_message(),
            ),
            patch(
                "airco_tracker.alert_pipeline.send_message",
                side_effect=RuntimeError("temporary ACS outage"),
            ),
            patch(
                "airco_tracker.alert_pipeline._schedule_email_retry",
                side_effect=RuntimeError("Service Bus unavailable"),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "temporary ACS outage"):
                worker.handle(job)

        ledger.mark_retryable.assert_called_once_with(
            job, "RuntimeError", claim_owner="claim-1"
        )

    def test_email_worker_schedules_recovery_instead_of_hot_abandoning_contention(self) -> None:
        event = _event(coverage={"fr"})
        recipient = _recipient("recipient-1")
        ledger = MagicMock()
        ledger.claim.return_value = DeliveryClaim(
            False,
            "sending",
            "operation-1",
            1,
            "other-owner",
            "2026-07-09T12:00:00+00:00",
            137,
        )
        worker, _ledger = self._worker(event, recipient, ledger=ledger)
        job = EmailJob.create(event.event_id, recipient.recipient_id)

        with (
            patch("airco_tracker.alert_pipeline._schedule_email_retry") as schedule,
            patch("airco_tracker.alert_pipeline.send_message") as send,
        ):
            worker.handle(job)

        schedule.assert_called_once_with(
            worker.config,
            job,
            attempt=1,
            delay_override=137,
            retry_kind="claim",
        )
        send.assert_not_called()

    def test_email_worker_never_calls_acs_when_delivery_is_already_sent(self) -> None:
        event = _event(coverage={"fr"})
        recipient = _recipient("recipient-1")
        ledger = MagicMock()
        ledger.claim.return_value = DeliveryClaim(
            False,
            "sent",
            "operation-1",
            1,
            "",
            "2026-07-09T12:00:00+00:00",
        )
        worker, _ledger = self._worker(event, recipient, ledger=ledger)
        job = EmailJob.create(event.event_id, recipient.recipient_id)

        with (
            patch("airco_tracker.alert_pipeline.build_message") as build,
            patch("airco_tracker.alert_pipeline.send_message") as send,
            patch("airco_tracker.alert_pipeline._schedule_email_retry") as schedule,
        ):
            worker.handle(job)

        ledger.claim.assert_called_once_with(job)
        build.assert_not_called()
        send.assert_not_called()
        schedule.assert_not_called()
        ledger.mark_accepted.assert_not_called()

    def test_production_email_suppresses_a_missing_delivery_country(self) -> None:
        event = _event(coverage={"fr"})
        recipient = _recipient("recipient-1")
        recipient = ProjectedRecipient(**{**recipient.__dict__, "delivery_country": None})
        worker, ledger = self._worker(event, recipient)
        job = EmailJob.create(event.event_id, recipient.recipient_id)

        with patch("airco_tracker.alert_pipeline.send_message") as send:
            worker.handle(job)

        ledger.mark_suppressed.assert_called_once_with(
            job,
            "entitlement_or_country_changed",
            claim_owner="claim-1",
        )
        ledger.claim.assert_called_once_with(job, count_attempt=False)
        send.assert_not_called()

    def test_email_worker_runtime_fails_fast_without_unsubscribe_configuration(self) -> None:
        with self.assertRaisesRegex(ValueError, "EMAIL_UNSUBSCRIBE_SIGNING_KEY"):
            _validate_email_worker_runtime(
                SimpleNamespace(
                    email_unsubscribe_signing_key="short",
                    app_base_url="https://airco-tracker.eu",
                )
            )
        with self.assertRaisesRegex(ValueError, "APP_BASE_URL"):
            _validate_email_worker_runtime(
                SimpleNamespace(
                    email_unsubscribe_signing_key="x" * 32,
                    app_base_url="http://airco-tracker.eu",
                )
            )


class DeliveryReportQueueTests(unittest.TestCase):
    def test_invalid_provider_event_is_completed_instead_of_entering_no_ttl_dlq(self) -> None:
        captured = {}

        def run_receiver(_config, *, handler, **_kwargs):
            captured["result"] = handler(b"not-json", None)
            return 1

        with (
            patch("airco_tracker.alert_pipeline.DeliveryReportWorker"),
            patch("airco_tracker.alert_pipeline._run_receiver", side_effect=run_receiver),
        ):
            self.assertEqual(run_delivery_report_worker(SimpleNamespace(), once=True), 1)

        self.assertIsNone(captured["result"])

    def test_delivery_report_dlq_cleanup_uses_receive_and_delete(self) -> None:
        receiver = MagicMock()
        receiver.receive_messages.side_effect = [[MagicMock(), MagicMock()], []]
        client = MagicMock()
        client.get_queue_receiver.return_value.__enter__.return_value = receiver
        client.get_queue_receiver.return_value.__exit__.return_value = False

        @contextmanager
        def fake_client(_config):
            yield client

        config = SimpleNamespace(delivery_events_queue="acs-email-delivery-events")
        with patch("airco_tracker.alert_pipeline.service_bus_client", side_effect=fake_client):
            self.assertEqual(purge_delivery_report_dead_letters(config, limit=10), 2)

        kwargs = client.get_queue_receiver.call_args.kwargs
        self.assertEqual(kwargs["queue_name"], "acs-email-delivery-events")
        self.assertEqual(str(kwargs["receive_mode"]), "ServiceBusReceiveMode.RECEIVE_AND_DELETE")


class _CapacityBatch:
    def __init__(self, capacity: int) -> None:
        self.capacity = capacity
        self.messages = []

    def add_message(self, message) -> None:
        if len(self.messages) >= self.capacity:
            raise MessageSizeExceededError()
        self.messages.append(message)


class _BatchSender:
    def __init__(self, capacity: int) -> None:
        self.capacity = capacity
        self.sent_batches: list[list[str]] = []
        self.sent_partition_keys: list[list[str | None]] = []

    def create_message_batch(self):
        return _CapacityBatch(self.capacity)

    def send_messages(self, batch) -> None:
        self.sent_batches.append([str(message.message_id) for message in batch.messages])
        self.sent_partition_keys.append(
            [message.partition_key for message in batch.messages]
        )


class ServiceBusBatchTests(unittest.TestCase):
    def test_partition_key_change_starts_a_new_batch(self) -> None:
        sender = _BatchSender(capacity=10)

        count = send_json_messages(
            sender,
            [
                ("a", "subject", "{}", "r-00"),
                ("b", "subject", "{}", "r-00"),
                ("c", "subject", "{}", "r-01"),
            ],
        )

        self.assertEqual(count, 3)
        self.assertEqual(sender.sent_batches, [["a", "b"], ["c"]])
        self.assertEqual(
            sender.sent_partition_keys,
            [["r-00", "r-00"], ["r-01"]],
        )

    def test_json_messages_are_split_into_size_aware_batches_without_reordering(self) -> None:
        sender = _BatchSender(capacity=2)
        messages = [(f"id-{index}", "subject", f'{{"index":{index}}}') for index in range(5)]

        count = send_json_messages(sender, messages)

        self.assertEqual(count, 5)
        self.assertEqual(
            sender.sent_batches,
            [["id-0", "id-1"], ["id-2", "id-3"], ["id-4"]],
        )

    def test_single_oversized_message_fails_without_sending_an_empty_batch(self) -> None:
        sender = _BatchSender(capacity=0)

        with self.assertRaisesRegex(ValueError, "exceeds the entity limit"):
            send_json_messages(sender, [("too-large", "subject", "{}")])

        self.assertEqual(sender.sent_batches, [])


class ServiceBusSettlementTests(unittest.TestCase):
    def _receiver_with_one_message(self):
        receiver = MagicMock()
        message = MagicMock()
        message.body = [b'{"ok":true}']
        receiver.receive_messages.return_value = [message]
        return receiver, message

    def test_successful_message_is_completed(self) -> None:
        receiver, message = self._receiver_with_one_message()
        handler = MagicMock()

        processed = process_receiver(
            receiver,
            handler,
            max_messages=1,
            max_wait_time=1,
        )

        self.assertEqual(processed, 1)
        handler.assert_called_once_with(b'{"ok":true}', message)
        receiver.complete_message.assert_called_once_with(message)
        receiver.abandon_message.assert_not_called()
        receiver.dead_letter_message.assert_not_called()

    def test_permanent_message_error_is_dead_lettered(self) -> None:
        receiver, message = self._receiver_with_one_message()
        handler = MagicMock(side_effect=PermanentMessageError("invalid payload"))

        processed = process_receiver(
            receiver,
            handler,
            max_messages=1,
            max_wait_time=1,
        )

        self.assertEqual(processed, 1)
        receiver.dead_letter_message.assert_called_once_with(
            message,
            reason="PermanentMessageError",
            error_description="invalid payload",
        )
        receiver.complete_message.assert_not_called()
        receiver.abandon_message.assert_not_called()

    def test_transient_message_error_is_abandoned(self) -> None:
        receiver, message = self._receiver_with_one_message()
        handler = MagicMock(side_effect=RuntimeError("temporary outage"))

        processed = process_receiver(
            receiver,
            handler,
            max_messages=1,
            max_wait_time=1,
        )

        self.assertEqual(processed, 1)
        receiver.abandon_message.assert_called_once_with(message)
        receiver.complete_message.assert_not_called()
        receiver.dead_letter_message.assert_not_called()


if __name__ == "__main__":
    unittest.main()
