from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

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


class _MemoryTable:
    """Small deterministic Azure Table model with boundary fault hooks."""

    def __init__(self) -> None:
        self.entities: dict[tuple[str, str], dict] = {}
        self.etags: dict[tuple[str, str], int] = {}
        self.before_create = None
        self.after_create = None
        self.before_update = None
        self.before_delete = None
        self.delete_calls: list[tuple[str, str]] = []

    def create_entity(self, entity) -> None:
        value = dict(entity)
        if self.before_create is not None:
            self.before_create(value)
        key = (str(value["PartitionKey"]), str(value["RowKey"]))
        if key in self.entities:
            raise ResourceExistsError("row already exists")
        self.entities[key] = value
        self.etags[key] = 1
        if self.after_create is not None:
            self.after_create(value)

    def get_entity(self, partition, row):
        key = (str(partition), str(row))
        if key not in self.entities:
            raise ResourceNotFoundError("row not found")
        return _Entity(self.entities[key], f"etag-{self.etags[key]}")

    def query_entities(self, query=None, **_kwargs):
        values = list(self.entities.values())
        if query == "PartitionKey eq 'pending'":
            values = [item for item in values if item["PartitionKey"] == "pending"]
        elif query == "status eq 'pending'":
            values = [item for item in values if item.get("status") == "pending"]
        return [
            _Entity(
                item,
                f"etag-{self.etags[(item['PartitionKey'], item['RowKey'])]}",
            )
            for item in values
        ]

    def update_entity(self, update, **kwargs) -> None:
        value = dict(update)
        if self.before_update is not None:
            self.before_update(value)
        key = (str(value["PartitionKey"]), str(value["RowKey"]))
        if key not in self.entities:
            raise ResourceNotFoundError("row not found")
        expected = f"etag-{self.etags[key]}"
        if kwargs.get("etag") != expected:
            from azure.core.exceptions import ResourceModifiedError

            raise ResourceModifiedError("etag changed")
        self.entities[key].update(value)
        self.etags[key] += 1

    def delete_entity(self, partition, row) -> None:
        key = (str(partition), str(row))
        self.delete_calls.append(key)
        if self.before_delete is not None:
            self.before_delete(key)
        if key not in self.entities:
            raise ResourceNotFoundError("row not found")
        del self.entities[key]
        self.etags.pop(key, None)


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

    def test_terminal_accepted_state_cannot_be_moved_back_to_pending(self) -> None:
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

        self.assertEqual(table.entity["status"], "accepted")

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

    def _memory_outbox(self):
        _outbox, _source, event = self._outbox()
        table = _MemoryTable()
        pending = _MemoryTable()
        pending.create_entity(
            {
                "PartitionKey": "_meta",
                "RowKey": "journal-v2",
                "completedAt": "2026-07-22T00:00:00Z",
            }
        )
        outbox = AzureTableOutbox.__new__(AzureTableOutbox)
        outbox._table = table
        outbox._pending = pending
        return outbox, table, pending, event

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

    def test_pending_reads_hot_partition_and_cleans_published_index(self) -> None:
        pending_outbox, _table, pending_event = self._outbox()
        published_outbox, published_table, published_event = self._outbox(status="published")
        # Give the second event a distinct row while preserving a valid payload.
        pending_index = MagicMock()
        pending_index.get_entity.return_value = {"completedAt": "2026-07-22T00:00:00Z"}
        pending_index.query_entities.return_value = [
            _Entity({
                "PartitionKey": "pending",
                "RowKey": AzureTableOutbox.pending_row_key(pending_event),
                "eventId": pending_event.event_id,
            }, "etag-pending"),
            _Entity({
                "PartitionKey": "pending",
                "RowKey": AzureTableOutbox.pending_row_key(published_event) + "-published",
                "eventId": published_event.event_id,
            }, "etag-published"),
        ]
        table = MagicMock()
        table.query_entities.return_value = []
        table.get_entity.side_effect = [
            _Entity(pending_outbox._table.entity, "etag-1"),
            _Entity(published_table.entity, "etag-2"),
        ]
        outbox = AzureTableOutbox.__new__(AzureTableOutbox)
        outbox._table = table
        outbox._pending = pending_index

        records = outbox.pending(limit=10)

        self.assertEqual([record.status for record in records], ["pending"])
        pending_index.query_entities.assert_called_once_with("PartitionKey eq 'pending'")
        pending_index.delete_entity.assert_called_once()

    def test_legacy_backfill_drains_every_page_before_marking_complete(self) -> None:
        outbox, source_table, event = self._outbox()
        table = MagicMock()
        table.query_entities.return_value = iter(
            [_Entity(source_table.entity, f"etag-{index}") for index in range(1_205)]
        )
        pending_index = MagicMock()
        pending_index.get_entity.side_effect = ResourceNotFoundError("not migrated")
        outbox._table = table
        outbox._pending = pending_index

        outbox._backfill_legacy_pending()

        self.assertEqual(pending_index.create_entity.call_count, 1_206)
        marker = pending_index.create_entity.call_args_list[-1].args[0]
        self.assertEqual(marker["PartitionKey"], "_meta")
        self.assertEqual(marker["RowKey"], "journal-v2")

    def test_concurrent_orphan_cleanup_is_idempotent(self) -> None:
        outbox, _table, pending_event = self._outbox(status="published")
        pending_index = MagicMock()
        pending_index.get_entity.return_value = {"completedAt": "2026-07-22T00:00:00Z"}
        pending_index.query_entities.return_value = [
            {
                "PartitionKey": "pending",
                "RowKey": AzureTableOutbox.pending_row_key(pending_event),
                "eventId": pending_event.event_id,
            }
        ]
        pending_index.delete_entity.side_effect = ResourceNotFoundError(
            "another publisher already cleaned it"
        )
        outbox._pending = pending_index

        self.assertEqual(outbox.pending(limit=10), [])

    def test_journal_create_failure_never_commits_an_archive(self) -> None:
        outbox, table, pending, event = self._memory_outbox()
        pending.before_create = lambda _entity: (_ for _ in ()).throw(
            RuntimeError("journal unavailable")
        )

        with self.assertRaisesRegex(RuntimeError, "journal unavailable"):
            outbox.create_if_absent(event)

        self.assertNotIn(
            (AzureTableOutbox.partition_key(event.event_id), event.event_id),
            table.entities,
        )

    def test_archive_failure_after_journal_commit_remains_publishable(self) -> None:
        outbox, table, pending, event = self._memory_outbox()
        table.before_create = lambda _entity: (_ for _ in ()).throw(
            RuntimeError("ambiguous archive timeout")
        )

        self.assertTrue(outbox.create_if_absent(event))

        journal_key = ("pending", event.event_id)
        self.assertIn(journal_key, pending.entities)
        self.assertEqual(
            [record.event.event_id for record in outbox.pending(limit=10)],
            [event.event_id],
        )
        self.assertIn(journal_key, pending.entities)

    def test_publisher_can_read_the_journal_during_archive_write_gap(self) -> None:
        outbox, _table, pending, event = self._memory_outbox()
        observed: list[str] = []

        def publish_between_writes(entity) -> None:
            if entity.get("PartitionKey") == "pending":
                observed.extend(
                    record.event.event_id for record in outbox.pending(limit=10)
                )

        pending.after_create = publish_between_writes

        self.assertTrue(outbox.create_if_absent(event))
        self.assertEqual(observed, [event.event_id])

    def test_overlapping_publishers_cannot_hold_the_same_unexpired_claim(self) -> None:
        first, _table, pending, event = self._memory_outbox()
        self.assertTrue(first.create_if_absent(event))
        second = AzureTableOutbox.__new__(AzureTableOutbox)
        second._table = first._table
        second._pending = pending

        first_page = first.pending(limit=10)
        second_page = second.pending(limit=10)

        self.assertEqual([item.event.event_id for item in first_page], [event.event_id])
        self.assertTrue(first_page[0].claim_owner)
        self.assertEqual(second_page, [])

    def test_expired_publisher_claim_is_recovered(self) -> None:
        first, _table, pending, event = self._memory_outbox()
        self.assertTrue(first.create_if_absent(event))
        first_claim = first.pending(limit=10)[0]
        pending.entities[("pending", event.event_id)]["leaseExpiresAt"] = (
            datetime.now(timezone.utc) - timedelta(seconds=1)
        ).isoformat()
        pending.etags[("pending", event.event_id)] += 1
        second = AzureTableOutbox.__new__(AzureTableOutbox)
        second._table = first._table
        second._pending = pending

        recovered = second.pending(limit=10)

        self.assertEqual([item.event.event_id for item in recovered], [event.event_id])
        self.assertNotEqual(recovered[0].claim_owner, first_claim.claim_owner)

    def test_conditional_claim_conflict_loses_without_duplicate_publish(self) -> None:
        outbox, _table, pending, event = self._memory_outbox()
        self.assertTrue(outbox.create_if_absent(event))
        conflict_once = True

        def conflict(_update) -> None:
            nonlocal conflict_once
            if conflict_once:
                conflict_once = False
                from azure.core.exceptions import ResourceModifiedError

                raise ResourceModifiedError("another publisher won")

        pending.before_update = conflict

        self.assertEqual(outbox.pending(limit=10), [])
        self.assertEqual(
            [item.event.event_id for item in outbox.pending(limit=10)],
            [event.event_id],
        )

    def test_mark_published_repairs_missing_archive_before_acknowledging_journal(self) -> None:
        outbox, table, pending, event = self._memory_outbox()
        outbox._create_pending_journal(event)

        outbox.mark_published([event.event_id])

        archive = table.entities[
            (AzureTableOutbox.partition_key(event.event_id), event.event_id)
        ]
        self.assertEqual(archive["status"], "published")
        self.assertEqual(archive["attempts"], 1)
        self.assertNotIn(("pending", event.event_id), pending.entities)

    def test_mark_published_handles_concurrent_pending_archive_creator(self) -> None:
        outbox, table, pending, event = self._memory_outbox()
        outbox._create_pending_journal(event)
        injected = False

        def inject_pending_archive(entity) -> None:
            nonlocal injected
            if injected or entity.get("status") != "published":
                return
            injected = True
            value = outbox._main_entity(event, status="pending")
            key = (str(value["PartitionKey"]), str(value["RowKey"]))
            table.entities[key] = value
            table.etags[key] = 1

        table.before_create = inject_pending_archive

        outbox.mark_published([event.event_id])

        archive = table.entities[
            (AzureTableOutbox.partition_key(event.event_id), event.event_id)
        ]
        self.assertEqual(archive["status"], "published")
        self.assertNotIn(("pending", event.event_id), pending.entities)

    def test_journal_payload_wins_a_rolling_writer_archive_race(self) -> None:
        outbox, table, pending, event = self._memory_outbox()
        stale_archive_event = StockAvailableEvent(
            event_id=event.event_id,
            product=event.product,
            delivery_coverage=event.delivery_coverage,
            availability_generation=event.availability_generation,
            created_at="2026-07-22T12:34:56+00:00",
        )
        outbox._create_pending_journal(event)
        table.create_entity(outbox._main_entity(stale_archive_event, status="pending"))

        outbox.mark_published([event.event_id])

        archive = table.entities[
            (AzureTableOutbox.partition_key(event.event_id), event.event_id)
        ]
        archived_event = StockAvailableEvent.from_json(archive["payloadJson"])
        self.assertEqual(archived_event.created_at, event.created_at)
        self.assertEqual(archive["createdAt"], event.created_at)

    def test_crash_after_archive_publish_before_journal_delete_is_recoverable(self) -> None:
        outbox, table, pending, event = self._memory_outbox()
        self.assertTrue(outbox.create_if_absent(event))
        fail_once = True

        def fail_first_delete(_key) -> None:
            nonlocal fail_once
            if fail_once:
                fail_once = False
                raise RuntimeError("process stopped before journal acknowledgement")

        pending.before_delete = fail_first_delete
        with self.assertRaisesRegex(RuntimeError, "before journal acknowledgement"):
            outbox.mark_published([event.event_id])

        archive = table.entities[
            (AzureTableOutbox.partition_key(event.event_id), event.event_id)
        ]
        self.assertEqual(archive["status"], "published")
        self.assertIn(("pending", event.event_id), pending.entities)

        self.assertEqual(outbox.pending(limit=10), [])
        self.assertNotIn(("pending", event.event_id), pending.entities)

    def test_failed_publish_repairs_missing_archive_and_keeps_journal(self) -> None:
        outbox, table, pending, event = self._memory_outbox()
        outbox._create_pending_journal(event)

        outbox.mark_attempt_failed([event.event_id], "ServiceBusError")

        archive = table.entities[
            (AzureTableOutbox.partition_key(event.event_id), event.event_id)
        ]
        self.assertEqual(archive["status"], "pending")
        self.assertEqual(archive["attempts"], 1)
        self.assertEqual(archive["lastErrorCode"], "ServiceBusError")
        self.assertIn(("pending", event.event_id), pending.entities)

    def test_producer_tolerates_publisher_ack_between_journal_create_and_read(self) -> None:
        _seed, _seed_table, _seed_pending, event = self._memory_outbox()
        published = _seed._main_entity(event, status="published", attempts=1)
        table = MagicMock()
        table.get_entity.side_effect = [
            ResourceNotFoundError("not archived yet"),
            _Entity(published, "etag-published"),
        ]
        pending = MagicMock()
        pending.create_entity.side_effect = ResourceExistsError("journal won elsewhere")
        pending.get_entity.side_effect = ResourceNotFoundError(
            "publisher already acknowledged journal"
        )
        outbox = AzureTableOutbox.__new__(AzureTableOutbox)
        outbox._table = table
        outbox._pending = pending

        self.assertFalse(outbox.create_if_absent(event))
        table.create_entity.assert_not_called()

    def test_pointer_only_legacy_orphan_is_not_deleted_during_writer_race(self) -> None:
        outbox, _table, pending, event = self._memory_outbox()
        legacy_key = f"20260722T000000000000Z-{event.event_id}"
        pending.create_entity(
            {
                "PartitionKey": "pending",
                "RowKey": legacy_key,
                "eventId": event.event_id,
                "createdAt": event.created_at,
            }
        )

        self.assertEqual(outbox.pending(limit=10), [])
        self.assertIn(("pending", legacy_key), pending.entities)

    def test_duplicate_event_keeps_one_canonical_journal(self) -> None:
        outbox, table, pending, canonical_event = self._memory_outbox()
        duplicate = StockAvailableEvent(
            event_id=canonical_event.event_id,
            product=canonical_event.product,
            delivery_coverage=canonical_event.delivery_coverage,
            availability_generation=canonical_event.availability_generation,
            created_at="2026-07-22T12:34:56+00:00",
        )
        self.assertTrue(outbox.create_if_absent(canonical_event))
        self.assertFalse(outbox.create_if_absent(duplicate))
        journal = pending.entities[("pending", canonical_event.event_id)]
        self.assertEqual(
            StockAvailableEvent.from_json(journal["payloadJson"]).created_at,
            canonical_event.created_at,
        )
        self.assertEqual(len([key for key in pending.entities if key[0] == "pending"]), 1)
        self.assertEqual(len(table.entities), 1)

    def test_existing_pending_archive_repairs_a_missing_journal(self) -> None:
        outbox, table, pending, event = self._memory_outbox()
        table.create_entity(outbox._main_entity(event, status="pending"))

        self.assertFalse(outbox.create_if_absent(event))

        self.assertIn(("pending", event.event_id), pending.entities)

    def test_existing_published_archive_cannot_be_reenqueued(self) -> None:
        outbox, table, pending, event = self._memory_outbox()
        table.create_entity(outbox._main_entity(event, status="published"))

        self.assertFalse(outbox.create_if_absent(event))

        self.assertNotIn(("pending", event.event_id), pending.entities)

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
            "entitlementTier": "radar",
            "entitlementStatus": "active",
            "entitlementExpiresAt": (
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
                "entitlementTier",
                "entitlementStatus",
                "entitlementExpiresAt",
                "subscriptionPlan",
                "subscriptionStatus",
                "subscriptionCurrentPeriodEnd",
                "emailAlertsEnabled",
                "emailAlertsTokenVersion",
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

    def test_naive_pass_entitlement_timestamp_is_treated_as_utc(self) -> None:
        recipient = ProjectedRecipient(
            recipient_id=str(uuid.uuid4()),
            email="user@example.com",
            language="en",
            delivery_country="fr",
            entitlement_tier="alerts",
            entitlement_status="active",
            entitlement_expires_at=(datetime.now(timezone.utc) + timedelta(days=1)).replace(
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

    def test_projection_reader_preserves_french_language(self) -> None:
        recipient = _projected_from_entity(
            {
                "RowKey": str(uuid.uuid4()),
                "email": "user@example.com",
                "language": "fr",
                "deliveryCountry": "fr",
                "subscriptionPlan": "monthly_basic",
                "status": "active",
                "currentPeriodEnd": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
                "enabled": True,
            }
        )

        self.assertEqual(recipient.language, "fr")

    def test_projection_reader_uses_pass_entitlement_fields(self) -> None:
        expires_at = (datetime.now(timezone.utc) + timedelta(days=90)).isoformat()
        recipient = _projected_from_entity(
            {
                "RowKey": str(uuid.uuid4()),
                "email": "user@example.com",
                "language": "en",
                "deliveryCountry": "fr",
                "entitlementTier": "radar",
                "entitlementStatus": "active",
                "entitlementExpiresAt": expires_at,
                "enabled": True,
            }
        )

        self.assertEqual(recipient.entitlement_tier, "radar")
        self.assertEqual(recipient.entitlement_status, "active")
        self.assertEqual(recipient.entitlement_expires_at, expires_at)
        self.assertTrue(recipient.entitled())

    def test_reconciler_projects_french_profile_preference(self) -> None:
        entity = _projection_entity(
            {
                "RowKey": "legacy-source-row",
                "userId": str(uuid.uuid4()),
                "email": "user@example.com",
                "languagePreference": "fr",
                "deliveryCountry": "fr",
                "entitlementTier": "alerts",
                "entitlementStatus": "active",
                "entitlementExpiresAt": (
                    datetime.now(timezone.utc) + timedelta(days=30)
                ).isoformat(),
                "updatedAt": "2026-07-11T12:00:00Z",
            },
            32,
            sync_cycle="cycle",
        )

        self.assertIsNotNone(entity)
        self.assertEqual(entity["language"], "fr")
        self.assertEqual(entity["deliveryCountry"], "fr")
        self.assertEqual(entity["entitlementTier"], "alerts")
        self.assertEqual(entity["entitlementStatus"], "active")
        self.assertNotIn("subscriptionPlan", entity)

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
    @staticmethod
    def _config() -> SimpleNamespace:
        return SimpleNamespace(
            azure_storage_account_url="https://example.blob.core.windows.net",
            alert_outbox_table="alertoutbox",
            alert_deliveries_table="alertdeliveries",
            alert_delivery_index_table="alertdeliveryindex",
            alert_suppressions_table="alertsuppression",
            auth_users_table="users",
            alert_recipients_table="alertrecipients",
            recipient_shard_count=32,
            alert_outbox_retention_days=30,
            alert_delivery_retention_days=90,
        )

    def test_cleanup_deletes_only_expired_terminal_metadata(self) -> None:
        outbox = MagicMock()
        deliveries = MagicMock()
        delivery_index = MagicMock()
        suppressions = MagicMock()
        users = MagicMock()
        recipients = MagicMock()
        outbox.query_entities.return_value = [
            {"PartitionKey": "o-aa", "RowKey": "a" * 64}
        ]
        deliveries.query_entities.side_effect = [
            [],
            [
                {"PartitionKey": "u-1", "RowKey": "e-1", "status": "delivered"},
                {"PartitionKey": "u-2", "RowKey": "e-2", "status": "sending"},
            ],
        ]
        delivery_index.query_entities.return_value = [
            {"PartitionKey": "m-aa", "RowKey": "message-1"},
        ]
        suppressions.query_entities.return_value = []
        config = self._config()

        with (
            patch(
                "azure.data.tables.TableClient",
                side_effect=[
                    outbox,
                    deliveries,
                    delivery_index,
                    suppressions,
                    users,
                    recipients,
                ],
            ),
            patch("airco_tracker.retention.default_azure_credential", return_value="credential"),
        ):
            removed = cleanup_alert_data(config)

        self.assertEqual(removed, (1, 1, 1, 0))
        outbox.delete_entity.assert_called_once_with("o-aa", "a" * 64)
        deliveries.delete_entity.assert_called_once_with("u-1", "e-1")
        delivery_index.delete_entity.assert_called_once_with("m-aa", "message-1")
        suppressions.query_entities.assert_called_once_with(
            "",
            select=["PartitionKey", "RowKey", "recipientId"],
        )

    def test_cleanup_removes_suppressions_for_missing_or_inactive_accounts(self) -> None:
        outbox = MagicMock()
        deliveries = MagicMock()
        delivery_index = MagicMock()
        suppressions = MagicMock()
        users = MagicMock()
        recipients = MagicMock()
        outbox.query_entities.return_value = []
        deliveries.query_entities.side_effect = [[], []]
        delivery_index.query_entities.return_value = []
        inactive_id = str(uuid.uuid4())
        active_id = str(uuid.uuid4())
        missing_id = str(uuid.uuid4())
        suppressions.query_entities.return_value = [
            {"PartitionKey": "s-1", "RowKey": inactive_id},
            {"PartitionKey": "s-2", "RowKey": active_id},
            {"PartitionKey": "s-3", "RowKey": missing_id},
        ]
        users.get_entity.side_effect = [
            {
                "recordType": "profile",
                "recordState": "deleted",
                "userId": inactive_id,
            },
            {
                "recordType": "profile",
                "recordState": "active",
                "userId": active_id,
            },
            ResourceNotFoundError("missing canonical profile"),
        ]
        recipients.get_entity.side_effect = ResourceNotFoundError(
            "missing recipient projection"
        )
        config = self._config()

        with (
            patch(
                "azure.data.tables.TableClient",
                side_effect=[
                    outbox,
                    deliveries,
                    delivery_index,
                    suppressions,
                    users,
                    recipients,
                ],
            ),
            patch("airco_tracker.retention.default_azure_credential", return_value="credential"),
        ):
            removed = cleanup_alert_data(config)

        self.assertEqual(removed, (0, 0, 0, 2))
        self.assertEqual(
            suppressions.delete_entity.call_args_list,
            [call("s-1", inactive_id), call("s-3", missing_id)],
        )

    def test_cleanup_default_drains_more_than_the_legacy_5000_row_cap(self) -> None:
        outbox = MagicMock()
        deliveries = MagicMock()
        delivery_index = MagicMock()
        suppressions = MagicMock()
        users = MagicMock()
        recipients = MagicMock()
        outbox.query_entities.return_value = (
            {
                "PartitionKey": f"o-{index % 256:02x}",
                "RowKey": f"{index:064x}",
            }
            for index in range(5_101)
        )
        deliveries.query_entities.side_effect = [[], []]
        delivery_index.query_entities.return_value = []
        suppressions.query_entities.return_value = []

        with (
            patch(
                "azure.data.tables.TableClient",
                side_effect=[
                    outbox,
                    deliveries,
                    delivery_index,
                    suppressions,
                    users,
                    recipients,
                ],
            ),
            patch("airco_tracker.retention.default_azure_credential", return_value="credential"),
        ):
            removed = cleanup_alert_data(self._config())

        self.assertEqual(removed, (5_101, 0, 0, 0))
        self.assertEqual(outbox.delete_entity.call_count, 5_101)

    def test_explicit_row_cap_reports_that_backlog_may_remain(self) -> None:
        outbox = MagicMock()
        deliveries = MagicMock()
        delivery_index = MagicMock()
        suppressions = MagicMock()
        users = MagicMock()
        recipients = MagicMock()
        outbox.query_entities.return_value = [
            {"PartitionKey": "o-aa", "RowKey": "a" * 64},
            {"PartitionKey": "o-bb", "RowKey": "b" * 64},
            {"PartitionKey": "o-cc", "RowKey": "c" * 64},
        ]
        deliveries.query_entities.side_effect = [[], []]
        delivery_index.query_entities.return_value = []
        suppressions.query_entities.return_value = []

        with (
            patch(
                "azure.data.tables.TableClient",
                side_effect=[
                    outbox,
                    deliveries,
                    delivery_index,
                    suppressions,
                    users,
                    recipients,
                ],
            ),
            patch("airco_tracker.retention.default_azure_credential", return_value="credential"),
            self.assertLogs("airco_tracker.retention", level="WARNING") as logs,
        ):
            removed = cleanup_alert_data(self._config(), limit=2)

        self.assertEqual(removed, (2, 0, 0, 0))
        self.assertIn("backlog may remain", "\n".join(logs.output))


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
