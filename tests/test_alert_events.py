from __future__ import annotations

import json
import unittest
import uuid

from airco_tracker.alert_events import (
    EVENT_SCHEMA_VERSION,
    EmailJob,
    StockAvailableEvent,
    recipient_shard,
    stock_event_id,
)
from airco_tracker.models import Product
from airco_tracker.state import updated_state
from airco_tracker.recipient_projection import legacy_user_id, recipient_partition_key


class AlertEventTests(unittest.TestCase):
    def setUp(self) -> None:
        self.available = Product(
            site="Example shop",
            name="Portable airco",
            url="https://shop.test/airco-1",
            available=True,
            price_eur=399.0,
            delivery="Tomorrow",
            btu=9000,
            country="fr",
        )

    def test_availability_generation_and_event_id_change_only_on_a_new_stock_cycle(self) -> None:
        first = updated_state({"version": 1, "products": {}}, [self.available])
        first_record = first["products"]["fr:https://shop.test/airco-1"]
        self.assertEqual(first_record["availability_generation"], 1)

        continuously_available = updated_state(first, [self.available])
        continuous_record = continuously_available["products"]["fr:https://shop.test/airco-1"]
        self.assertEqual(continuous_record["availability_generation"], 1)

        sold_out = Product(**{**self.available.__dict__, "available": False})
        unavailable_state = updated_state(continuously_available, [sold_out])
        unavailable_record = unavailable_state["products"]["fr:https://shop.test/airco-1"]
        self.assertEqual(unavailable_record["availability_generation"], 1)

        restocked = updated_state(unavailable_state, [self.available])
        restocked_record = restocked["products"]["fr:https://shop.test/airco-1"]
        self.assertEqual(restocked_record["availability_generation"], 2)

        first_id = stock_event_id(self.available, 1)
        self.assertEqual(first_id, stock_event_id(self.available, 1))
        self.assertNotEqual(first_id, stock_event_id(self.available, 2))

    def test_stock_event_round_trip_preserves_delivery_and_product_contract(self) -> None:
        product = Product(
            **{
                **self.available.__dict__,
                "affiliate_url": "https://www.awin1.com/cread.php?ued=airco-1",
            }
        )
        event = StockAvailableEvent.for_product(
            product,
            availability_generation=2,
            delivery_coverage={"FR", "be", "fr"},
        )

        decoded = StockAvailableEvent.from_json(event.to_json())

        self.assertEqual(decoded, event)
        self.assertEqual(decoded.delivery_coverage, ("be", "fr"))
        self.assertEqual(decoded.product.country, "fr")
        self.assertEqual(decoded.product.price_eur, 399.0)
        self.assertEqual(decoded.product.affiliate_url, product.affiliate_url)

    def test_affiliate_link_does_not_change_event_or_state_identity(self) -> None:
        linked = Product(
            **{
                **self.available.__dict__,
                "affiliate_url": "https://www.awin1.com/cread.php?ued=airco-1",
            }
        )
        self.assertEqual(stock_event_id(linked, 1), stock_event_id(self.available, 1))
        state = updated_state({"version": 1, "products": {}}, [linked])
        self.assertIn("fr:https://shop.test/airco-1", state["products"])
        self.assertEqual(
            state["products"]["fr:https://shop.test/airco-1"]["affiliate_url"],
            linked.affiliate_url,
        )

    def test_stock_event_rejects_invalid_json_schema_type_and_target_invariants(self) -> None:
        event = StockAvailableEvent.for_product(
            self.available,
            availability_generation=1,
            delivery_coverage={"fr"},
        )
        payload = event.to_dict()

        invalid_payloads = [
            "not-json",
            json.dumps([]),
            json.dumps({**payload, "schemaVersion": EVENT_SCHEMA_VERSION + 1}),
            json.dumps({**payload, "schemaVersion": True}),
            json.dumps({**payload, "eventType": "stock.available.v999"}),
            json.dumps({**payload, "product": None}),
            json.dumps({**payload, "availabilityGeneration": 0}),
            json.dumps({**payload, "availabilityGeneration": "1"}),
            json.dumps({**payload, "targetRecipientIds": ["recipient-1"]}),
            json.dumps({**payload, "testOnly": True, "targetRecipientIds": []}),
            json.dumps({**payload, "testOnly": "false"}),
            json.dumps({**payload, "deliveryCoverage": "fr"}),
            json.dumps(
                {
                    **payload,
                    "product": {**payload["product"], "available": "false"},
                }
            ),
            json.dumps(
                {
                    **payload,
                    "product": {**payload["product"], "url": "https://shop.test/tampered"},
                }
            ),
            json.dumps(
                {
                    **payload,
                    "product": {
                        **payload["product"],
                        "affiliate_url": "https://evil.example/redirect",
                    },
                }
            ),
            json.dumps(
                {
                    **payload,
                    "product": {
                        **payload["product"],
                        "affiliate_url": "https://www.awin1.com@evil.example/redirect",
                    },
                }
            ),
        ]

        for invalid in invalid_payloads:
            with self.subTest(payload=invalid):
                with self.assertRaises(ValueError):
                    StockAvailableEvent.from_json(invalid)

    def test_email_job_rejects_a_tampered_delivery_id(self) -> None:
        event_id = "a" * 64
        recipient_id = str(uuid.uuid4())
        job = EmailJob.create(event_id, recipient_id)
        payload = json.loads(job.to_json())
        payload["deliveryId"] = "attacker-controlled-id"

        with self.assertRaisesRegex(ValueError, "deliveryId"):
            EmailJob.from_json(json.dumps(payload))

    def test_32_way_recipient_shard_is_stable_and_validated(self) -> None:
        expected = {
            "user-a": 10,
            "user-b": 25,
            "00000000-0000-0000-0000-000000000000": 8,
            "sample-recipient": 11,
        }
        for recipient_id, shard in expected.items():
            with self.subTest(recipient_id=recipient_id):
                self.assertEqual(recipient_shard(recipient_id, 32), shard)
                self.assertGreaterEqual(shard, 0)
                self.assertLess(shard, 32)

        with self.assertRaises(ValueError):
            recipient_shard("recipient", 0)

    def test_projection_partition_and_legacy_uuid_match_web_contract(self) -> None:
        self.assertEqual(
            legacy_user_id("User@Example.com"),
            "e4b71dde-633b-57ec-a885-0a900b0087e2",
        )
        self.assertEqual(recipient_partition_key("user-a"), "r-0a")
        self.assertEqual(recipient_partition_key("user-b"), "r-19")


if __name__ == "__main__":
    unittest.main()
