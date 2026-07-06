from __future__ import annotations

import unittest
from datetime import datetime, timezone

from airco_tracker.inventory import empty_inventory, updated_inventory
from airco_tracker.models import Product


NOW = datetime(2026, 7, 3, 10, 0, tzinfo=timezone.utc)


class InventoryTests(unittest.TestCase):
    def test_snapshot_keeps_all_available_products_without_alert_filters(self) -> None:
        products = [
            Product("Shop", "Low BTU", "https://shop.test/low", True, 2000.0, None, 5000),
            Product("Shop", "Unknown details", "https://shop.test/unknown", True),
            Product("Shop", "Sold out", "https://shop.test/out", False, 399.0, None, 9000),
        ]

        snapshot = updated_inventory(
            empty_inventory(),
            products,
            all_sites={"Shop"},
            checked_sites={"Shop"},
            now=NOW,
        )

        saved = snapshot["sites"]["nl:Shop"]["products"]
        self.assertEqual([item["name"] for item in saved], ["Low BTU", "Unknown details"])
        self.assertEqual(snapshot["available_product_count"], 2)
        self.assertEqual(snapshot["immediate_product_count"], 2)
        self.assertEqual(snapshot["presale_product_count"], 0)
        self.assertFalse(snapshot["sites"]["nl:Shop"]["stale"])

    def test_successful_empty_result_clears_previous_inventory(self) -> None:
        old = self._old_inventory()

        snapshot = updated_inventory(
            old,
            [],
            all_sites={"Shop"},
            checked_sites={"Shop"},
            now=NOW,
        )

        site = snapshot["sites"]["nl:Shop"]
        self.assertEqual(site["products"], [])
        self.assertEqual(site["available_product_count"], 0)
        self.assertEqual(site["status"], "ok")
        self.assertFalse(site["stale"])

    def test_failed_site_retains_previous_inventory_and_becomes_stale(self) -> None:
        old = self._old_inventory()

        snapshot = updated_inventory(
            old,
            [],
            all_sites={"Shop", "Never succeeded"},
            checked_sites=set(),
            now=NOW,
        )

        retained = snapshot["sites"]["nl:Shop"]
        self.assertEqual(retained["products"], old["sites"]["Shop"]["products"])
        self.assertEqual(retained["last_success_at"], "2026-07-03T09:00:00+00:00")
        self.assertEqual(retained["status"], "error")
        self.assertTrue(retained["stale"])
        never_succeeded = snapshot["sites"]["nl:Never succeeded"]
        self.assertEqual(never_succeeded["products"], [])
        self.assertIsNone(never_succeeded["last_success_at"])
        self.assertEqual(snapshot["stale_site_count"], 2)

    def test_presale_products_are_counted_separately_from_immediate_stock(self) -> None:
        products = [
            Product("Shop", "Immediate", "https://shop.test/now", True, 499.0, "Morgen", 9000),
            Product("Shop", "Presale", "https://shop.test/later", True, 599.0, "Leverbaar vanaf 1 augustus", 12000),
        ]

        snapshot = updated_inventory(
            empty_inventory(),
            products,
            all_sites={"Shop"},
            checked_sites={"Shop"},
            now=NOW,
        )

        site = snapshot["sites"]["nl:Shop"]
        self.assertEqual(snapshot["available_product_count"], 2)
        self.assertEqual(snapshot["immediate_product_count"], 1)
        self.assertEqual(snapshot["presale_product_count"], 1)
        self.assertEqual(site["available_product_count"], 2)
        self.assertEqual(site["immediate_product_count"], 1)
        self.assertEqual(site["presale_product_count"], 1)
        self.assertTrue(site["products"][1]["presale"])

    def test_failed_new_site_id_retains_legacy_site_name_inventory(self) -> None:
        old = self._old_inventory()

        snapshot = updated_inventory(
            old,
            [],
            all_sites={
                "nl:Shop": {
                    "country": "nl",
                    "site": "Shop",
                }
            },
            checked_sites=set(),
            now=NOW,
        )

        retained = snapshot["sites"]["nl:Shop"]
        self.assertEqual(retained["products"][0]["site_id"], "nl:Shop")
        self.assertEqual(retained["products"][0]["country"], "nl")
        self.assertEqual(retained["last_success_at"], "2026-07-03T09:00:00+00:00")

    @staticmethod
    def _old_inventory():
        return {
            "version": 1,
            "sites": {
                "Shop": {
                    "status": "ok",
                    "stale": False,
                    "last_success_at": "2026-07-03T09:00:00+00:00",
                    "products": [
                        Product(
                            "Shop",
                            "Existing airco",
                            "https://shop.test/existing",
                            True,
                        ).to_dict()
                    ],
                }
            },
        }


if __name__ == "__main__":
    unittest.main()
