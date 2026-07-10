from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.data.tables import UpdateMode

from airco_tracker.alert_events import EmailJob, StockAvailableEvent
from airco_tracker.deliveries import DeliveryLedger
from airco_tracker.mailer import _send_azure_communication, message_fingerprint
from airco_tracker.models import Product
from airco_tracker.outbox import AzureTableOutbox
from airco_tracker.recipient_projection import (
    ProjectedRecipient,
    RecipientProjection,
    _projected_from_entity,
    _projection_entity,
    legacy_user_id,
)
from airco_tracker.retention import cleanup_alert_data


class _Entity(dict):
    def __init__(self, values, etag: str | None = "etag-1") -> None:
        super().__init__(values)
        self.metadata = {"etag": etag} if etag else {}


class _LedgerTable:
    def __init__(self, entity: dict, *, include_etag: bool = True) -> None:
        self.entity = dict(entity)
        self.etag_number = 1
        self.include_etag = include_etag

    def create_entity(self, _entity) -> None:
        raise ResourceExistsError("row already exists")

    def get_entity(self, _partition, _row):
        etag = f"etag-{self.etag_number}" if self.include_etag else None
        return _Entity(self.entity, etag)

    def update_entity(self, update, **_kwargs) -> None:
        self.entity.update(update)
        self.etag_number += 1


class _OutboxTable:
    def __init__(self, entity: dict) -> None:
        self.entity = dict(entity)
        self.update_calls: list[tuple[dict, dict]] = []

    def get_entity(self, _partition, _row):
        return _Entity(self.entity, "etag-outbox-1")

    def update_entity(self, update, **kwargs) -> None:
        self.update_calls.append((dict(update), dict(kwargs)))
        self.entity.update(update)


def _job() -> EmailJob:
    return EmailJob.create("a" * 64, str(uuid.uuid4()))


class DeliveryLedgerTests(unittest.TestCase):
    def _ledger(self, table: _LedgerTable) -> DeliveryLedger:
        ledger = DeliveryLedger.__new__(DeliveryLedger)
        ledger._table = table
        return ledger

    def test_first_sent_timestamp_is_created_at_first_claim_not_queue_time(self) -> None:
        job = _job()
        old_created = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        table = _LedgerTable(
            {
                "PartitionKey": DeliveryLedger.partition_key(job.recipient_id),
                "RowKey": job.event_id,
                "status": "pending",
                "attempts": 0,
                "createdAt": old_created,
                "acsOperationId": DeliveryLedger.operation_id(job.delivery_id),
            }
        )

        claim = self._ledger(table).claim(job)

        self.assertTrue(claim.claimed)
        self.assertNotEqual(claim.first_sent_at, old_created)
        first_sent = datetime.fromisoformat(claim.first_sent_at)
        self.assertLess(abs((datetime.now(timezone.utc) - first_sent).total_seconds()), 5)
        self.assertEqual(table.entity["firstSentAt"], claim.first_sent_at)

    def test_terminal_sent_state_cannot_be_moved_back_to_pending(self) -> None:
        job = _job()
        table = _LedgerTable(
            {
                "PartitionKey": DeliveryLedger.partition_key(job.recipient_id),
                "RowKey": job.event_id,
                "status": "sending",
                "attempts": 1,
                "leaseOwner": "owner-1",
                "acsOperationId": DeliveryLedger.operation_id(job.delivery_id),
            }
        )
        ledger = self._ledger(table)

        ledger.mark_sent(job, claim_owner="owner-1")
        ledger.mark_retryable(job, "late_timeout", claim_owner="owner-1")

        self.assertEqual(table.entity["status"], "sent")

    def test_claim_fails_closed_when_table_entity_has_no_etag(self) -> None:
        job = _job()
        table = _LedgerTable(
            {
                "PartitionKey": DeliveryLedger.partition_key(job.recipient_id),
                "RowKey": job.event_id,
                "status": "pending",
                "attempts": 0,
                "acsOperationId": DeliveryLedger.operation_id(job.delivery_id),
            },
            include_etag=False,
        )

        with self.assertRaisesRegex(RuntimeError, "missing its ETag"):
            self._ledger(table).claim(job)

    def test_retry_with_changed_payload_is_rejected_after_first_binding(self) -> None:
        job = _job()
        table = _LedgerTable(
            {
                "PartitionKey": DeliveryLedger.partition_key(job.recipient_id),
                "RowKey": job.event_id,
                "status": "pending",
                "attempts": 0,
                "acsOperationId": DeliveryLedger.operation_id(job.delivery_id),
            }
        )
        ledger = self._ledger(table)

        first = ledger.claim(job)
        self.assertTrue(
            ledger.bind_payload(
                job,
                claim_owner=first.lease_owner,
                payload_fingerprint="a" * 64,
            )
        )
        self.assertFalse(
            ledger.bind_payload(
                job,
                claim_owner=first.lease_owner,
                payload_fingerprint="b" * 64,
            )
        )

        self.assertEqual(table.entity["payloadFingerprint"], "a" * 64)
        ledger.mark_suppressed(
            job,
            "recipient_payload_changed_after_attempt",
            claim_owner=first.lease_owner,
        )
        self.assertEqual(table.entity["status"], "suppressed")

    def test_expired_sending_row_can_be_reclaimed_without_counting_a_send_attempt(self) -> None:
        job = _job()
        first_sent_at = "2026-07-09T12:00:00+00:00"
        table = _LedgerTable(
            {
                "PartitionKey": DeliveryLedger.partition_key(job.recipient_id),
                "RowKey": job.event_id,
                "status": "sending",
                "attempts": 1,
                "leaseOwner": "crashed-owner",
                "leaseUntil": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
                "firstSentAt": first_sent_at,
                "acsOperationId": DeliveryLedger.operation_id(job.delivery_id),
            }
        )
        ledger = self._ledger(table)

        claim = ledger.claim(job, count_attempt=False)

        self.assertTrue(claim.claimed)
        self.assertEqual(claim.attempts, 1)
        self.assertEqual(claim.first_sent_at, first_sent_at)
        self.assertNotEqual(claim.lease_owner, "crashed-owner")
        ledger.mark_suppressed(
            job,
            "recipient_not_found",
            claim_owner=claim.lease_owner,
        )
        self.assertEqual(table.entity["status"], "suppressed")


class OutboxConditionalUpdateTests(unittest.TestCase):
    def _outbox(self, *, status: str = "pending", attempts: int = 0):
        product = Product(
            site="Example shop",
            name="Portable airco",
            url="https://shop.test/airco-1",
            available=True,
            price_eur=399.0,
            delivery="Tomorrow",
            btu=9000,
            country="fr",
        )
        event = StockAvailableEvent.for_product(
            product,
            availability_generation=1,
            delivery_coverage={"fr"},
        )
        table = _OutboxTable(
            {
                "PartitionKey": AzureTableOutbox.partition_key(event.event_id),
                "RowKey": event.event_id,
                "payloadJson": event.to_json(),
                "status": status,
                "attempts": attempts,
            }
        )
        outbox = AzureTableOutbox.__new__(AzureTableOutbox)
        outbox._table = table
        return outbox, table, event

    def test_mark_published_uses_the_current_entity_etag(self) -> None:
        from azure.core import MatchConditions

        outbox, table, event = self._outbox()

        outbox.mark_published([event.event_id])

        self.assertEqual(len(table.update_calls), 1)
        update, kwargs = table.update_calls[0]
        self.assertEqual(update["status"], "published")
        self.assertEqual(update["attempts"], 1)
        self.assertEqual(kwargs["etag"], "etag-outbox-1")
        self.assertEqual(kwargs["match_condition"], MatchConditions.IfNotModified)

    def test_mark_attempt_failed_uses_the_current_entity_etag(self) -> None:
        from azure.core import MatchConditions

        outbox, table, event = self._outbox(attempts=2)

        outbox.mark_attempt_failed([event.event_id], "ServiceBusError")

        self.assertEqual(len(table.update_calls), 1)
        update, kwargs = table.update_calls[0]
        self.assertEqual(update["status"], "pending")
        self.assertEqual(update["attempts"], 3)
        self.assertEqual(update["lastErrorCode"], "ServiceBusError")
        self.assertEqual(kwargs["etag"], "etag-outbox-1")
        self.assertEqual(kwargs["match_condition"], MatchConditions.IfNotModified)


class RecipientProjectionTests(unittest.TestCase):
    def test_delivery_resolves_the_uuid_keyed_canonical_profile(self) -> None:
        recipient_id = str(uuid.uuid4())
        projection = RecipientProjection.__new__(RecipientProjection)
        projection.shard_count = 32
        projection._users = MagicMock()
        projection._users.get_entity.return_value = {
            "PartitionKey": "user",
            "RowKey": f"id:{recipient_id}",
            "recordType": "profile",
            "recordState": "active",
            "userId": recipient_id,
            "profileRevision": 8,
            "email": "latest@example.com",
            "languagePreference": "en",
            "deliveryCountry": "fr",
            "subscriptionPlan": "monthly_priority",
            "subscriptionStatus": "active",
            "subscriptionCurrentPeriodEnd": (
                datetime.now(timezone.utc) + timedelta(days=1)
            ).isoformat(),
            "updatedAt": "2026-07-09T12:34:56Z",
        }

        recipient = projection.get_authoritative(recipient_id)

        self.assertIsNotNone(recipient)
        self.assertEqual(recipient.email, "latest@example.com")
        self.assertTrue(recipient.enabled)
        projection._users.get_entity.assert_called_once_with(
            "user",
            f"id:{recipient_id}",
            select=[
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
                "updatedAt",
            ],
        )

    def test_delivery_legacy_fallback_skips_lookup_index_rows(self) -> None:
        recipient_id = str(uuid.uuid4())
        projection = RecipientProjection.__new__(RecipientProjection)
        projection.shard_count = 32
        projection._users = MagicMock()
        projection._projection = MagicMock()
        projection._projection.get_entity.return_value = {
            "PartitionKey": projection.partition_key(recipient_id),
            "RowKey": recipient_id,
        }
        projection._users.get_entity.side_effect = ResourceNotFoundError("not found")
        projection._users.query_entities.return_value = iter(
            [
                {
                    "recordType": "email_index",
                    "recordState": "active",
                    "userId": recipient_id,
                    "email": "latest@example.com",
                },
                {
                    "userId": recipient_id,
                    "email": "latest@example.com",
                    "languagePreference": "en",
                    "deliveryCountry": "fr",
                    "subscriptionPlan": "monthly_priority",
                    "subscriptionStatus": "active",
                    "subscriptionCurrentPeriodEnd": (
                        datetime.now(timezone.utc) + timedelta(days=1)
                    ).isoformat(),
                    "updatedAt": "2026-07-09T12:34:56Z",
                },
            ]
        )

        recipient = projection.get_authoritative(recipient_id)

        self.assertIsNotNone(recipient)
        self.assertEqual(recipient.email, "latest@example.com")

    def test_delivery_resolves_legacy_profile_without_user_id_by_source_row(self) -> None:
        email = "legacy@example.com"
        recipient_id = legacy_user_id(email)
        source_row = "bGVnYWN5QGV4YW1wbGUuY29t"
        projection = RecipientProjection.__new__(RecipientProjection)
        projection.shard_count = 32
        projection._projection = MagicMock()
        projection._projection.get_entity.return_value = {
            "PartitionKey": projection.partition_key(recipient_id),
            "RowKey": recipient_id,
            "sourceUserRowKey": source_row,
        }
        projection._users = MagicMock()
        projection._users.get_entity.side_effect = [
            ResourceNotFoundError("canonical profile not found"),
            {
                "PartitionKey": "user",
                "RowKey": source_row,
                "email": email,
                "languagePreference": "en",
                "deliveryCountry": "fr",
                "subscriptionPlan": "monthly_priority",
                "subscriptionStatus": "active",
                "subscriptionCurrentPeriodEnd": (
                    datetime.now(timezone.utc) + timedelta(days=1)
                ).isoformat(),
                "updatedAt": "2026-07-09T12:34:56Z",
            },
        ]

        recipient = projection.get_authoritative(recipient_id)

        self.assertIsNotNone(recipient)
        self.assertEqual(recipient.recipient_id, recipient_id)
        self.assertEqual(recipient.email, email)
        projection._users.query_entities.assert_not_called()

    def test_delivery_rejects_source_row_whose_derived_uuid_does_not_match(self) -> None:
        requested_id = str(uuid.uuid4())
        source_row = "b3RoZXJAZXhhbXBsZS5jb20"
        projection = RecipientProjection.__new__(RecipientProjection)
        projection.shard_count = 32
        projection._projection = MagicMock()
        projection._projection.get_entity.return_value = {
            "PartitionKey": projection.partition_key(requested_id),
            "RowKey": requested_id,
            "sourceUserRowKey": source_row,
        }
        projection._users = MagicMock()
        projection._users.get_entity.side_effect = [
            ResourceNotFoundError("canonical profile not found"),
            {
                "PartitionKey": "user",
                "RowKey": source_row,
                "email": "other@example.com",
                "subscriptionPlan": "monthly_priority",
                "subscriptionStatus": "active",
                "subscriptionCurrentPeriodEnd": (
                    datetime.now(timezone.utc) + timedelta(days=1)
                ).isoformat(),
            },
        ]

        self.assertIsNone(projection.get_authoritative(requested_id))
        projection._users.query_entities.assert_not_called()

    def test_reconciler_never_projects_an_email_migration_tombstone(self) -> None:
        entity = _projection_entity(
            {
                "userId": str(uuid.uuid4()),
                "recordState": "superseded",
                "email": "old-address@example.com",
                "profileRevision": 4,
                "updatedAt": "2026-07-09T12:34:56Z",
            },
            32,
            sync_cycle="cycle",
        )

        self.assertIsNone(entity)

    def test_reconciler_ignores_canonical_lookup_index_rows(self) -> None:
        entity = _projection_entity(
            {
                "recordType": "email_index",
                "recordState": "active",
                "userId": str(uuid.uuid4()),
                "email": "user@example.com",
                "profileRevision": 4,
                "updatedAt": "2026-07-09T12:34:56Z",
            },
            32,
            sync_cycle="cycle",
        )

        self.assertIsNone(entity)

    def test_naive_legacy_entitlement_timestamp_is_treated_as_utc(self) -> None:
        recipient = ProjectedRecipient(
            recipient_id=str(uuid.uuid4()),
            email="user@example.com",
            language="en",
            delivery_country="fr",
            plan="monthly_basic",
            status="active",
            entitlement_end=(datetime.now(timezone.utc) + timedelta(days=1)).replace(
                tzinfo=None
            ).isoformat(),
            enabled=True,
        )

        self.assertTrue(recipient.entitled())

    def test_projection_reader_normalizes_an_unsupported_language(self) -> None:
        recipient = _projected_from_entity(
            {
                "RowKey": str(uuid.uuid4()),
                "email": "user@example.com",
                "language": "invalid",
                "deliveryCountry": "fr",
                "subscriptionPlan": "monthly_basic",
                "status": "active",
                "currentPeriodEnd": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
                "enabled": True,
            }
        )

        self.assertEqual(recipient.language, "zh")

    def test_reconciler_preserves_canonical_updated_timestamp(self) -> None:
        timestamp = "2026-07-09T12:34:56.789Z"
        entity = _projection_entity(
            {
                "RowKey": "legacy-source-row",
                "userId": str(uuid.uuid4()),
                "email": "user@example.com",
                "languagePreference": "en",
                "deliveryCountry": "fr",
                "subscriptionPlan": "monthly_basic",
                "subscriptionStatus": "active",
                "subscriptionCurrentPeriodEnd": "2026-08-09T12:34:56.789Z",
                "profileRevision": 7,
                "updatedAt": timestamp,
            },
            32,
            sync_cycle="cycle",
        )

        self.assertIsNotNone(entity)
        self.assertEqual(entity["updatedAt"], timestamp)
        self.assertEqual(entity["sourceRevision"], 7)
        self.assertEqual(entity["sourceUserRowKey"], "legacy-source-row")

    def test_reconciler_backfills_source_row_without_replacing_newer_payload(self) -> None:
        projection = RecipientProjection.__new__(RecipientProjection)
        projection._projection = MagicMock()
        row = str(uuid.uuid4())
        projection._projection.get_entity.return_value = _Entity(
            {
                "PartitionKey": "r-00",
                "RowKey": row,
                "email": "newer@example.com",
                "updatedAt": "2026-07-09T12:00:01Z",
                "sourceRevision": 4,
            }
        )
        source = {
            "PartitionKey": "r-00",
            "RowKey": row,
            "email": "older@example.com",
            "updatedAt": "2026-07-09T12:00:00Z",
            "sourceRevision": 3,
            "sourceUserRowKey": "legacy-source-row",
        }

        self.assertTrue(projection._upsert_if_not_newer(source))
        update = projection._projection.update_entity.call_args.args[0]
        self.assertEqual(
            update,
            {
                "PartitionKey": "r-00",
                "RowKey": row,
                "sourceUserRowKey": "legacy-source-row",
            },
        )
        self.assertEqual(projection._projection.update_entity.call_args.kwargs["mode"], UpdateMode.MERGE)

    def test_reconciler_does_not_overwrite_a_newer_web_projection(self) -> None:
        projection = RecipientProjection.__new__(RecipientProjection)
        projection._projection = MagicMock()
        projection._projection.get_entity.return_value = _Entity(
            {"updatedAt": "2026-07-09T12:00:01Z", "sourceRevision": 3}
        )
        source = {
            "PartitionKey": "r-00",
            "RowKey": str(uuid.uuid4()),
            "updatedAt": "2026-07-09T12:00:00Z",
            "sourceRevision": 2,
        }

        self.assertFalse(projection._upsert_if_not_newer(source))
        projection._projection.update_entity.assert_not_called()

    def test_reconciler_prefers_higher_revision_over_a_future_timestamp(self) -> None:
        projection = RecipientProjection.__new__(RecipientProjection)
        projection._projection = MagicMock()
        projection._projection.get_entity.return_value = _Entity(
            {"updatedAt": "2099-01-01T00:00:00Z", "sourceRevision": 2}
        )
        source = {
            "PartitionKey": "r-00",
            "RowKey": str(uuid.uuid4()),
            "updatedAt": "2026-07-09T12:00:00Z",
            "sourceRevision": 3,
        }

        self.assertTrue(projection._upsert_if_not_newer(source))
        projection._projection.update_entity.assert_called_once()


class RetentionTests(unittest.TestCase):
    def test_cleanup_deletes_only_expired_terminal_metadata(self) -> None:
        outbox = MagicMock()
        deliveries = MagicMock()
        outbox.query_entities.return_value = [
            {"PartitionKey": "o-aa", "RowKey": "a" * 64}
        ]
        deliveries.query_entities.return_value = [
            {"PartitionKey": "u-1", "RowKey": "e-1", "status": "sent"},
            {"PartitionKey": "u-2", "RowKey": "e-2", "status": "sending"},
        ]
        config = SimpleNamespace(
            azure_storage_account_url="https://example.blob.core.windows.net",
            alert_outbox_table="alertoutbox",
            alert_deliveries_table="alertdeliveries",
            alert_outbox_retention_days=30,
            alert_delivery_retention_days=90,
        )

        with (
            patch("azure.data.tables.TableClient", side_effect=[outbox, deliveries]),
            patch("airco_tracker.retention.default_azure_credential", return_value="credential"),
        ):
            removed = cleanup_alert_data(config)

        self.assertEqual(removed, (1, 1))
        outbox.delete_entity.assert_called_once_with("o-aa", "a" * 64)
        deliveries.delete_entity.assert_called_once_with("u-1", "e-1")


class MailerIdempotencyTests(unittest.TestCase):
    def test_message_fingerprint_covers_the_exact_acs_payload(self) -> None:
        config = SimpleNamespace(
            email_from="sender@example.com",
            email_to="recipient@example.com",
        )
        message = EmailMessage()
        message["From"] = config.email_from
        message["To"] = config.email_to
        message["Subject"] = "Available"
        message.set_content("First body")
        delivery_id = "d" * 64

        original = message_fingerprint(config, message, delivery_id=delivery_id)
        config.email_to = "new-recipient@example.com"
        changed_recipient = message_fingerprint(config, message, delivery_id=delivery_id)
        config.email_to = "recipient@example.com"
        config.email_from = "new-sender@example.com"
        changed_sender = message_fingerprint(config, message, delivery_id=delivery_id)
        config.email_from = "sender@example.com"
        changed_message = EmailMessage()
        changed_message["From"] = config.email_from
        changed_message["To"] = config.email_to
        changed_message["Subject"] = "Available"
        changed_message.set_content("Second body")
        changed_body = message_fingerprint(config, changed_message, delivery_id=delivery_id)

        self.assertRegex(original, r"^[0-9a-f]{64}$")
        self.assertEqual(len({original, changed_recipient, changed_sender, changed_body}), 4)
        self.assertNotIn("recipient", original)

    def test_acs_send_uses_operation_and_repeatability_headers(self) -> None:
        poller = MagicMock()
        poller.result.return_value = {"id": "operation-1", "status": "Succeeded"}
        client = MagicMock()
        client.begin_send.return_value = poller
        message = EmailMessage()
        message["From"] = "sender@example.com"
        message["To"] = "recipient@example.com"
        message["Subject"] = "test"
        message.set_content("body")
        config = SimpleNamespace(
            acs_endpoint="https://example.communication.azure.com",
            email_from="sender@example.com",
            email_to="recipient@example.com",
        )

        with patch("airco_tracker.mailer._cached_email_client", return_value=client):
            result = _send_azure_communication(
                config,
                message,
                operation_id="53af4271-b407-45f2-9ca0-2117d97965a4",
                repeatability_first_sent="2026-07-09T12:00:00+00:00",
            )

        self.assertEqual(result.status, "Succeeded")
        kwargs = client.begin_send.call_args.kwargs
        self.assertEqual(kwargs["operation_id"], "53af4271-b407-45f2-9ca0-2117d97965a4")
        self.assertEqual(
            kwargs["headers"],
            {
                "Repeatability-Request-ID": "53af4271-b407-45f2-9ca0-2117d97965a4",
                "Repeatability-First-Sent": "Thu, 09 Jul 2026 12:00:00 GMT",
            },
        )
        poller.result.assert_called_once_with(timeout=180)

    def test_acs_failed_final_status_is_not_reported_as_sent(self) -> None:
        poller = MagicMock()
        poller.result.return_value = {
            "id": "operation-1",
            "status": "Failed",
            "error": {"code": "MailboxUnavailable", "message": "private detail"},
        }
        client = MagicMock()
        client.begin_send.return_value = poller
        message = EmailMessage()
        message["From"] = "sender@example.com"
        message["To"] = "recipient@example.com"
        message["Subject"] = "test"
        message.set_content("body")
        config = SimpleNamespace(
            acs_endpoint="https://example.communication.azure.com",
            email_from="sender@example.com",
            email_to="recipient@example.com",
        )

        with (
            patch("airco_tracker.mailer._cached_email_client", return_value=client),
            self.assertRaisesRegex(RuntimeError, "MailboxUnavailable"),
        ):
            _send_azure_communication(
                config,
                message,
                operation_id="53af4271-b407-45f2-9ca0-2117d97965a4",
                repeatability_first_sent="2026-07-09T12:00:00+00:00",
            )


if __name__ == "__main__":
    unittest.main()
