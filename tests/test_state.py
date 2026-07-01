from __future__ import annotations

import unittest

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

    def test_alerts_on_out_to_in_transition(self) -> None:
        old = {"products": {self.product.url: {"available": False}}}
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


if __name__ == "__main__":
    unittest.main()
