from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from airco_tracker.mailer import _acs_payload, build_message
from airco_tracker.unsubscribe import sign_unsubscribe_token
from airco_tracker.inventory import empty_inventory
from airco_tracker.inventory_store import LocalInventoryStore
from airco_tracker.models import Product
from airco_tracker.state_store import LocalStateStore


class CloudBackendTests(unittest.TestCase):
    def test_local_inventory_store_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = LocalInventoryStore(Path(directory) / "inventory.json")
            self.assertEqual(store.load(), empty_inventory())
            expected = {
                "version": 1,
                "available_product_count": 0,
                "sites": {"Shop": {"products": []}},
            }
            store.save(expected)
            self.assertEqual(store.load(), expected)

    def test_local_state_store_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = LocalStateStore(Path(directory) / "state.json")
            self.assertEqual(store.load(), {"version": 1, "products": {}})
            expected = {"version": 1, "products": {"x": {"available": True}}}
            store.save(expected)
            self.assertEqual(store.load(), expected)

    def test_acs_payload_contains_both_bodies_and_recipient(self) -> None:
        config = SimpleNamespace(
            email_from="DoNotReply@example.azurecomm.net",
            email_to="recipient@example.com",
            email_reply_to="support@example.com",
            app_base_url="https://airco-tracker.eu",
            email_lang="zh",
        )
        product = Product(
            "Shop",
            "Airco 7000 BTU",
            "https://shop.test/airco",
            True,
            399.0,
            "Morgen in huis",
            7000,
        )
        message = build_message(config, [product], unsubscribe_token="signed-token")
        payload = _acs_payload(config, message)
        self.assertEqual(payload["recipients"]["to"][0]["address"], config.email_to)
        self.assertEqual(payload["senderAddress"], config.email_from)
        self.assertIn("Airco 7000 BTU", payload["content"]["plainText"])
        self.assertIn("<h2>", payload["content"]["html"])
        self.assertEqual(payload["replyTo"], [{"address": "support@example.com"}])
        self.assertEqual(payload["userEngagementTrackingDisabled"], True)
        self.assertEqual(
            payload["headers"]["List-Unsubscribe-Post"],
            "List-Unsubscribe=One-Click",
        )
        self.assertIn("/api/alerts/unsubscribe?token=signed-token", payload["headers"]["List-Unsubscribe"])
        self.assertIn("/unsubscribe?token=signed-token", payload["content"]["plainText"])

    def test_build_message_supports_three_languages(self) -> None:
        product = Product("Shop", "Airco 7000 BTU", "https://shop.test/airco", True, 399.0, "Morgen", 7000)
        for lang, subject_fragment in (
            ("zh", "台便携空调"),
            ("nl", "mobiele airco"),
            ("en", "portable air conditioners"),
        ):
            config = SimpleNamespace(
                email_from="DoNotReply@example.azurecomm.net",
                email_to="recipient@example.com",
                email_reply_to="",
                app_base_url="https://airco-tracker.eu",
                email_lang=lang,
            )
            message = build_message(config, [product])
            self.assertIn(subject_fragment, message["Subject"])

    def test_test_message_without_token_has_reply_to_but_no_unsubscribe_headers(self) -> None:
        config = SimpleNamespace(
            email_from="DoNotReply@example.azurecomm.net",
            email_to="recipient@example.com",
            email_reply_to="support@example.com",
            app_base_url="https://airco-tracker.eu",
            email_lang="en",
        )
        payload = _acs_payload(config, build_message(config, [], test=True))
        self.assertEqual(payload["replyTo"], [{"address": "support@example.com"}])
        self.assertNotIn("headers", payload)

    def test_targeted_pipeline_test_carries_visible_and_one_click_unsubscribe(self) -> None:
        config = SimpleNamespace(
            email_from="DoNotReply@example.azurecomm.net",
            email_to="recipient@example.com",
            email_reply_to="support@example.com",
            app_base_url="https://airco-tracker.eu",
            email_lang="en",
        )
        payload = _acs_payload(
            config,
            build_message(config, [], test=True, unsubscribe_token="signed-token"),
        )

        self.assertIn("/unsubscribe?token=signed-token", payload["content"]["plainText"])
        self.assertIn("/unsubscribe?token=signed-token", payload["content"]["html"])
        self.assertIn(
            "/api/alerts/unsubscribe?token=signed-token",
            payload["headers"]["List-Unsubscribe"],
        )
        self.assertEqual(
            payload["headers"]["List-Unsubscribe-Post"],
            "List-Unsubscribe=One-Click",
        )

    def test_unsubscribe_token_matches_cross_language_vector(self) -> None:
        self.assertEqual(
            sign_unsubscribe_token(
                "0123456789abcdef0123456789abcdef",
                "123e4567-e89b-12d3-a456-426614174000",
                7,
            ),
            "djEKYWxlcnRzLXVuc3Vic2NyaWJlCjEyM2U0NTY3LWU4OWItMTJkMy1hNDU2LTQyNjYxNDE3NDAwMAo3."
            "XqzD43DQ_6O4EoMUrW4BYdm3bNtM-Y5v1RFLAKE0FYQ",
        )


if __name__ == "__main__":
    unittest.main()
