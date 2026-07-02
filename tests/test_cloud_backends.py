from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from airco_tracker.mailer import _acs_payload, build_message
from airco_tracker.models import Product
from airco_tracker.state_store import LocalStateStore


class CloudBackendTests(unittest.TestCase):
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
        message = build_message(config, [product])
        payload = _acs_payload(config, message)
        self.assertEqual(payload["recipients"]["to"][0]["address"], config.email_to)
        self.assertEqual(payload["senderAddress"], config.email_from)
        self.assertIn("Airco 7000 BTU", payload["content"]["plainText"])
        self.assertIn("<h2>", payload["content"]["html"])

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
                email_lang=lang,
            )
            message = build_message(config, [product])
            self.assertIn(subject_fragment, message["Subject"])


if __name__ == "__main__":
    unittest.main()
