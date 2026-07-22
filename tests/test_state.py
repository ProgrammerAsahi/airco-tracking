from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from airco_tracker.models import Product
from airco_tracker.state import select_alerts, updated_state


class StateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.product = Product("Shop", "Airco", "https://shop.test/1", True, 300.0, "Morgen", 7000)

    def test_first_seen_can_alert(self) -> None:
        alerts = select_alerts([self.product], {"products": {}}, alert_on_first_seen=True, max_price_eur=None, min_btu=None)
        self.assertEqual(alerts, [self.product])

    def test_deduplicates_existing_available_product(self) -> None:
        state = updated_state({"products": {}}, [self.product])
        alerts = select_alerts([self.product], state, alert_on_first_seen=True, max_price_eur=None, min_btu=None)
        self.assertEqual(alerts, [])
        self.assertIn("nl:https://shop.test/1", state["products"])
        self.assertNotIn("https://shop.test/1", state["products"])

    def test_alerts_on_out_to_in_transition(self) -> None:
        old = {"products": {self.product.url: {"available": False}}}
        alerts = select_alerts([self.product], old, alert_on_first_seen=False, max_price_eur=400, min_btu=5000)
        self.assertEqual(alerts, [self.product])

    def test_alerts_when_presale_becomes_immediate_stock(self) -> None:
        old = {"products": {self.product.url: {"available": True, "presale": True}}}
        alerts = select_alerts([self.product], old, alert_on_first_seen=False, max_price_eur=400, min_btu=5000)
        self.assertEqual(alerts, [self.product])

    def test_price_limit_keeps_unknown_price_but_rejects_expensive_product(self) -> None:
        unknown_price = Product("Shop", "Unknown price", "https://shop.test/unknown", True, None, "Morgen", 7000)
        too_expensive = Product("Shop", "Expensive", "https://shop.test/expensive", True, 1500.01, "Morgen", 7000)
        alerts = select_alerts(
            [unknown_price, too_expensive],
            {"products": {}},
            alert_on_first_seen=True,
            max_price_eur=1500,
            min_btu=5000,
        )
        self.assertEqual(alerts, [unknown_price])

    def test_minimum_btu_rejects_known_low_capacity_product(self) -> None:
        low_capacity = Product(
            "Obelink",
            "Tweedekans Obelink ArcticMove 1500 tentairco",
            "https://shop.test/arcticmove-1500",
            True,
            319.0,
            "Online op voorraad",
            5118,
        )
        alerts = select_alerts(
            [low_capacity],
            {"products": {}},
            alert_on_first_seen=True,
            max_price_eur=1500,
            min_btu=7000,
        )
        self.assertEqual(alerts, [])

    def test_successful_empty_seasonal_site_marks_old_product_unavailable(self) -> None:
        old = {
            "products": {
                "https://alternate.test/airco": {
                    "site": "Alternate.nl",
                    "available": True,
                },
                "https://failed.test/airco": {
                    "site": "Failed shop",
                    "available": True,
                },
            }
        }
        state = updated_state(old, [], checked_sites={"nl:Alternate.nl"})
        self.assertFalse(state["products"]["https://alternate.test/airco"]["available"])
        self.assertTrue(state["products"]["https://failed.test/airco"]["available"])

    def test_successful_empty_seasonal_site_still_accepts_legacy_checked_site_names(self) -> None:
        old = {
            "products": {
                "https://alternate.test/airco": {
                    "site": "Alternate.nl",
                    "available": True,
                },
            }
        }
        state = updated_state(old, [], checked_sites={"Alternate.nl"})
        self.assertFalse(state["products"]["https://alternate.test/airco"]["available"])

    def test_long_unavailable_records_become_small_tombstones_then_expire(self) -> None:
        now = datetime(2026, 7, 22, tzinfo=timezone.utc)
        key = "nl:https://shop.test/retired"
        old = {
            "products": {
                key: {
                    "site": "Shop",
                    "site_id": "nl:Shop",
                    "country": "nl",
                    "name": "Retired model",
                    "url": "https://shop.test/retired",
                    "available": False,
                    "presale": False,
                    "price_eur": 499,
                    "delivery": "sold out",
                    "last_seen": (now - timedelta(days=100)).isoformat(),
                    "unavailable_since": (now - timedelta(days=100)).isoformat(),
                    "availability_generation": 2,
                }
            }
        }

        state = updated_state(old, [], now=now, compact_after_days=90, tombstone_retention_days=365)

        record = state["products"][key]
        self.assertTrue(record["tombstone"])
        self.assertNotIn("price_eur", record)
        self.assertEqual(record["availability_generation"], 2)

        expired = updated_state(
            old,
            [],
            now=now + timedelta(days=300),
            compact_after_days=90,
            tombstone_retention_days=365,
        )
        self.assertNotIn(key, expired["products"])

    def test_repeated_missing_scan_preserves_unavailable_since(self) -> None:
        now = datetime(2026, 7, 22, tzinfo=timezone.utc)
        unavailable_since = (now - timedelta(days=5)).isoformat()
        old = {
            "products": {
                "nl:https://shop.test/1": {
                    **self.product.to_dict(),
                    "available": False,
                    "unavailable_since": unavailable_since,
                    "last_seen": unavailable_since,
                }
            }
        }

        state = updated_state(old, [], checked_sites={"nl:Shop"}, now=now)

        self.assertEqual(state["products"]["nl:https://shop.test/1"]["unavailable_since"], unavailable_since)


if __name__ == "__main__":
    unittest.main()
