from __future__ import annotations

import json
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from azure.core.exceptions import ResourceModifiedError

from airco_tracker.alert_events import EmailJob
from airco_tracker.deliveries import DeliveryLedger
from airco_tracker.delivery_reports import (
    DeliveryMessageBinding,
    DeliveryReport,
    DeliveryReportWorker,
    address_fingerprint,
)


class _Entity(dict):
    def __init__(self, values, etag: str = "etag-1") -> None:
        super().__init__(values)
        self.metadata = {"etag": etag}


class _LedgerTable:
    def __init__(self, entity: dict) -> None:
        self.entity = dict(entity)
        self.etag_number = 1

    def get_entity(self, _partition, _row):
        return _Entity(self.entity, f"etag-{self.etag_number}")

    def update_entity(self, values, **_kwargs) -> None:
        self.entity.update(values)
        self.etag_number += 1


def _event_payload(
    *,
    event_id: str = "event-grid-1",
    message_id: str | None = None,
    recipient: str = "reader@example.com",
    status: str = "Delivered",
    timestamp: str = "2026-07-10T12:00:00Z",
):
    return {
        "id": event_id,
        "eventType": "Microsoft.Communication.EmailDeliveryReportReceived",
        "dataVersion": "1.0",
        "eventTime": timestamp,
        "data": {
            "messageId": message_id or str(uuid.uuid4()),
            "recipient": recipient,
            "status": status,
            "deliveryAttemptTimeStamp": timestamp,
            "deliveryStatusDetails": {"statusMessage": "must never be persisted"},
        },
    }


class DeliveryReportParsingTests(unittest.TestCase):
    def test_parses_official_event_grid_schema_without_retaining_status_message(self) -> None:
        payload = _event_payload(status="FilteredSpam")

        report = DeliveryReport.from_json(json.dumps(payload))

        self.assertEqual(report.status, "filtered_spam")
        self.assertEqual(report.recipient, "reader@example.com")
        self.assertEqual(report.reported_at, "2026-07-10T12:00:00+00:00")
        self.assertFalse(hasattr(report, "status_message"))

    def test_accepts_one_item_webhook_array_but_rejects_multi_event_batch(self) -> None:
        report = DeliveryReport.from_json(json.dumps([_event_payload()]))
        self.assertEqual(report.status, "delivered")

        with self.assertRaisesRegex(ValueError, "exactly one"):
            DeliveryReport.from_json(json.dumps([_event_payload(), _event_payload()]))

    def test_rejects_an_unknown_status_or_event_type(self) -> None:
        unknown = _event_payload(status="Deferred")
        with self.assertRaisesRegex(ValueError, "unsupported status"):
            DeliveryReport.from_json(json.dumps(unknown))

        wrong = _event_payload()
        wrong["eventType"] = "Microsoft.Communication.EmailEngagementTrackingReportReceived"
        with self.assertRaisesRegex(ValueError, "event type"):
            DeliveryReport.from_json(json.dumps(wrong))


class DeliveryLedgerReportTests(unittest.TestCase):
    def _ledger(self, entity: dict) -> tuple[DeliveryLedger, _LedgerTable]:
        table = _LedgerTable(entity)
        ledger = DeliveryLedger.__new__(DeliveryLedger)
        ledger._table = table
        return ledger, table

    def _job_and_entity(self):
        recipient_id = str(uuid.uuid4())
        job = EmailJob.create("a" * 64, recipient_id)
        message_id = DeliveryLedger.operation_id(job.delivery_id)
        entity = {
            "PartitionKey": DeliveryLedger.partition_key(recipient_id),
            "RowKey": job.event_id,
            "recipientId": recipient_id,
            "deliveryId": job.delivery_id,
            "status": "accepted",
            "acsOperationId": message_id,
            "acsMessageId": message_id,
        }
        return job, message_id, entity

    def test_duplicate_event_is_idempotent(self) -> None:
        job, message_id, entity = self._job_and_entity()
        ledger, table = self._ledger(entity)

        self.assertTrue(
            ledger.record_delivery_report(
                job,
                report_status="delivered",
                report_at="2026-07-10T12:00:00+00:00",
                event_grid_event_id="eg-1",
                acs_message_id=message_id,
            )
        )
        self.assertFalse(
            ledger.record_delivery_report(
                job,
                report_status="delivered",
                report_at="2026-07-10T12:00:00+00:00",
                event_grid_event_id="eg-1",
                acs_message_id=message_id,
            )
        )
        self.assertEqual(table.entity["status"], "delivered")

    def test_newer_async_bounce_overrides_delivered_and_older_replay_does_not(self) -> None:
        job, message_id, entity = self._job_and_entity()
        ledger, table = self._ledger(entity)
        ledger.record_delivery_report(
            job,
            report_status="delivered",
            report_at="2026-07-10T12:00:00+00:00",
            event_grid_event_id="eg-delivered",
            acs_message_id=message_id,
        )

        self.assertTrue(
            ledger.record_delivery_report(
                job,
                report_status="bounced",
                report_at="2026-07-10T12:05:00+00:00",
                event_grid_event_id="eg-bounced",
                acs_message_id=message_id,
            )
        )
        self.assertFalse(
            ledger.record_delivery_report(
                job,
                report_status="delivered",
                report_at="2026-07-10T12:01:00+00:00",
                event_grid_event_id="eg-old",
                acs_message_id=message_id,
            )
        )
        self.assertEqual(table.entity["status"], "bounced")
        self.assertEqual(table.entity["deliveryReportEventId"], "eg-bounced")

    def test_business_suppression_is_not_overwritten_by_provider_event(self) -> None:
        job, message_id, entity = self._job_and_entity()
        entity["status"] = "suppressed"
        ledger, table = self._ledger(entity)

        self.assertFalse(
            ledger.record_delivery_report(
                job,
                report_status="delivered",
                report_at="2026-07-10T12:00:00+00:00",
                event_grid_event_id="eg-1",
                acs_message_id=message_id,
            )
        )
        self.assertEqual(table.entity["status"], "suppressed")


class DeliveryReportWorkerTests(unittest.TestCase):
    def _worker(self, *, current_email: str = "reader@example.com"):
        recipient_id = str(uuid.uuid4())
        event_id = "a" * 64
        job = EmailJob.create(event_id, recipient_id)
        message_id = DeliveryLedger.operation_id(job.delivery_id)
        binding = DeliveryMessageBinding(
            message_id,
            event_id,
            recipient_id,
            job.delivery_id,
            address_fingerprint(recipient_id, "reader@example.com"),
        )
        index = MagicMock()
        index.get.return_value = binding
        suppressions = MagicMock()
        ledger = MagicMock()
        recipients = MagicMock()
        recipients.get_authoritative.return_value = SimpleNamespace(email=current_email)
        worker = DeliveryReportWorker(
            SimpleNamespace(),
            index=index,
            suppressions=suppressions,
            ledger=ledger,
            recipients=recipients,
        )
        return worker, job, message_id, suppressions, ledger

    def test_bounced_and_provider_suppressed_create_system_suppression(self) -> None:
        for provider_status, normalized in (
            ("Bounced", "bounced"),
            ("Suppressed", "provider_suppressed"),
        ):
            with self.subTest(status=provider_status):
                worker, job, message_id, suppressions, ledger = self._worker()
                report = DeliveryReport.from_json(
                    json.dumps(
                        _event_payload(
                            event_id=f"eg-{provider_status}",
                            message_id=message_id,
                            status=provider_status,
                        )
                    )
                )

                self.assertEqual(worker.handle(report), normalized)

                ledger.record_delivery_report.assert_called_once()
                suppressions.suppress.assert_called_once()
                suppressions.clear_if_newer.assert_not_called()

    def test_reputation_failures_do_not_permanently_suppress_recipient(self) -> None:
        for provider_status in ("Quarantined", "FilteredSpam", "Failed"):
            with self.subTest(status=provider_status):
                worker, _job, message_id, suppressions, _ledger = self._worker()
                report = DeliveryReport.from_json(
                    json.dumps(_event_payload(message_id=message_id, status=provider_status))
                )

                worker.handle(report)

                suppressions.suppress.assert_not_called()
                suppressions.clear_if_newer.assert_not_called()

    def test_old_address_bounce_is_recorded_but_does_not_suppress_new_email(self) -> None:
        worker, _job, message_id, suppressions, ledger = self._worker(
            current_email="new-reader@example.com"
        )
        report = DeliveryReport.from_json(
            json.dumps(_event_payload(message_id=message_id, status="Bounced"))
        )

        worker.handle(report)

        ledger.record_delivery_report.assert_called_once()
        suppressions.suppress.assert_not_called()

    def test_delivered_clears_only_the_matching_address_suppression(self) -> None:
        worker, _job, message_id, suppressions, _ledger = self._worker()
        report = DeliveryReport.from_json(
            json.dumps(_event_payload(message_id=message_id, status="Delivered"))
        )

        worker.handle(report)

        suppressions.clear_if_newer.assert_called_once()

    def test_unbound_web_login_message_is_ignored(self) -> None:
        worker, _job, message_id, suppressions, ledger = self._worker()
        worker.index.get.return_value = None
        report = DeliveryReport.from_json(
            json.dumps(_event_payload(message_id=message_id, status="Delivered"))
        )

        self.assertEqual(worker.handle(report), "ignored")

        ledger.record_delivery_report.assert_not_called()
        suppressions.suppress.assert_not_called()


if __name__ == "__main__":
    unittest.main()
