from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from airco_tracker.i18n import SUPPORTED_LANGS
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

    def test_build_message_supports_all_languages(self) -> None:
        product = Product("Shop", "Airco 7000 BTU", "https://shop.test/airco", True, 399.0, "Morgen", 7000)
        for lang, subject_fragment, intro_fragment in (
            ("zh", "台便携空调", "检测到以下可配送到荷兰的便携空调"),
            ("nl", "1 mobiele airco weer", "De volgende mobiele airco kan"),
            ("en", "1 portable air conditioner back", "The following portable air conditioner can"),
            ("fr", "1 climatiseur mobile de nouveau", "Le climatiseur mobile suivant peut"),
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
            self.assertIn(
                intro_fragment,
                message.get_body(preferencelist=("plain",)).get_content(),
            )

    def test_alert_message_prefers_affiliate_purchase_url(self) -> None:
        config = SimpleNamespace(
            email_from="DoNotReply@example.azurecomm.net",
            email_to="recipient@example.com",
            email_reply_to="",
            app_base_url="https://airco-tracker.eu",
            email_lang="en",
        )
        product = Product(
            "Shop",
            "Airco 7000 BTU",
            "https://shop.test/airco",
            True,
            affiliate_url="https://www.awin1.com/cread.php?ued=airco",
        )

        message = build_message(config, [product])
        plain = message.get_body(preferencelist=("plain",)).get_content()
        html_body = message.get_body(preferencelist=("html",)).get_content()
        self.assertIn(product.affiliate_url, plain)
        self.assertIn(product.affiliate_url, html_body)
        self.assertNotIn("https://shop.test/airco", plain)
        self.assertIn("affiliate links", plain)
        self.assertIn("affiliate links", html_body)
        self.assertLess(plain.index("affiliate links"), plain.index(product.affiliate_url))
        self.assertLess(html_body.index("affiliate links"), html_body.index(product.affiliate_url))

    def test_alert_message_rejects_non_https_affiliate_purchase_url(self) -> None:
        config = SimpleNamespace(
            email_from="DoNotReply@example.azurecomm.net",
            email_to="recipient@example.com",
            email_reply_to="",
            app_base_url="https://airco-tracker.eu",
            email_lang="en",
        )
        product = Product(
            "Shop",
            "Airco 7000 BTU",
            "https://shop.test/airco",
            True,
            affiliate_url="javascript:alert(1)",
        )

        message = build_message(config, [product])
        plain = message.get_body(preferencelist=("plain",)).get_content()
        html_body = message.get_body(preferencelist=("html",)).get_content()
        self.assertIn(product.url, plain)
        self.assertIn(product.url, html_body)
        self.assertNotIn("javascript:", plain)
        self.assertNotIn("javascript:", html_body)
        self.assertNotIn("affiliate links", plain)
        self.assertNotIn("affiliate links", html_body)

    def test_alert_message_rejects_control_characters_in_affiliate_url(self) -> None:
        config = SimpleNamespace(
            email_from="DoNotReply@example.azurecomm.net",
            email_to="recipient@example.com",
            email_reply_to="",
            app_base_url="https://airco-tracker.eu",
            email_lang="en",
        )
        product = Product(
            "Shop",
            "Airco 7000 BTU",
            "https://shop.test/airco",
            True,
            affiliate_url="https://www.awin1.com/cread.php?ok=1\nForged: value",
        )

        message = build_message(config, [product])
        plain = message.get_body(preferencelist=("plain",)).get_content()
        self.assertIn(product.url, plain)
        self.assertNotIn("Forged: value", plain)
        self.assertNotIn("affiliate links", plain)

    def test_alert_message_discloses_legacy_awin_purchase_url(self) -> None:
        config = SimpleNamespace(
            email_from="DoNotReply@example.azurecomm.net",
            email_to="recipient@example.com",
            email_reply_to="",
            app_base_url="https://airco-tracker.eu",
            email_lang="en",
        )
        product = Product(
            "E.Leclerc France",
            "Climatiseur 9000 BTU",
            "https://www.e.leclerc/fp/123456789",
            True,
            country="fr",
            affiliate_url=(
                "https://www.awin1.com/cread.php?awinmid=15135&awinaffid=2981827"
            ),
        )

        message = build_message(config, [product])
        plain = message.get_body(preferencelist=("plain",)).get_content()
        html_body = message.get_body(preferencelist=("html",)).get_content()
        self.assertIn("affiliate links", plain)
        self.assertIn("affiliate links", html_body)
        self.assertLess(plain.index("affiliate links"), plain.index(product.purchase_url))

    def test_alert_message_localizes_destination_and_price_for_french(self) -> None:
        config = SimpleNamespace(
            email_from="DoNotReply@example.azurecomm.net",
            email_to="recipient@example.com",
            email_reply_to="",
            app_base_url="https://airco-tracker.eu",
            email_lang="fr",
        )
        product = Product(
            "Boutique",
            "Climatiseur 9000 BTU",
            "https://shop.test/airco",
            True,
            1234.5,
            None,
            9000,
            country="nl",
        )

        message = build_message(config, [product], delivery_country="fr")
        plain = message.get_body(preferencelist=("plain",)).get_content()
        html_body = message.get_body(preferencelist=("html",)).get_content()

        self.assertIn(
            "Le climatiseur mobile suivant peut de nouveau être livré à une adresse située en France",
            plain,
        )
        self.assertIn("1\u202f234,50\u00a0€", plain)
        self.assertIn("lang='fr'", html_body)
        self.assertIn("<h2>Climatiseur mobile de nouveau en stock</h2>", html_body)

    def test_alert_message_uses_plural_subject_for_multiple_products(self) -> None:
        config = SimpleNamespace(
            email_from="DoNotReply@example.azurecomm.net",
            email_to="recipient@example.com",
            email_reply_to="",
            app_base_url="https://airco-tracker.eu",
            email_lang="fr",
        )
        products = [
            Product("Boutique", f"Climatiseur {index}", f"https://shop.test/{index}", True)
            for index in (1, 2)
        ]

        message = build_message(config, products, delivery_country="fr")
        plain = message.get_body(preferencelist=("plain",)).get_content()
        html_body = message.get_body(preferencelist=("html",)).get_content()

        self.assertIn("2 climatiseurs mobiles", message["Subject"])
        self.assertIn("Les climatiseurs mobiles suivants peuvent", plain)
        self.assertIn("<h2>Climatiseurs mobiles de nouveau en stock</h2>", html_body)

    def test_alert_message_localizes_destination_and_price_for_dutch(self) -> None:
        config = SimpleNamespace(
            email_from="DoNotReply@example.azurecomm.net",
            email_to="recipient@example.com",
            email_reply_to="",
            app_base_url="https://airco-tracker.eu",
            email_lang="nl",
        )
        product = Product(
            "Winkel",
            "Mobiele airco",
            "https://shop.test/airco",
            True,
            1234.5,
            None,
            9000,
            country="fr",
        )

        message = build_message(config, [product], delivery_country="nl")
        plain = message.get_body(preferencelist=("plain",)).get_content()

        self.assertIn("De volgende mobiele airco kan weer naar een adres in Nederland", plain)
        self.assertIn("€\u00a01.234,50", plain)

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

    def test_french_visible_unsubscribe_link_preserves_language(self) -> None:
        config = SimpleNamespace(
            email_from="DoNotReply@example.azurecomm.net",
            email_to="recipient@example.com",
            email_reply_to="support@example.com",
            app_base_url="https://airco-tracker.eu",
            email_lang="fr",
        )
        payload = _acs_payload(
            config,
            build_message(config, [], test=True, unsubscribe_token="signed-token"),
        )

        self.assertIn(
            "/unsubscribe?token=signed-token&lang=fr",
            payload["content"]["plainText"],
        )
        self.assertNotIn("lang=fr", payload["headers"]["List-Unsubscribe"])

    def test_all_local_translation_keys_cover_every_supported_language(self) -> None:
        local_file = Path(__file__).parents[1] / "airco_tracker" / "i18n_local.json"
        data = json.loads(local_file.read_text(encoding="utf-8"))

        self.assertEqual(set(SUPPORTED_LANGS), {"zh", "nl", "en", "fr"})
        for scope, messages in data.items():
            with self.subTest(scope=scope):
                self.assertIsInstance(messages, dict)
            for key, translations in messages.items():
                with self.subTest(scope=scope, key=key):
                    self.assertEqual(set(translations), set(SUPPORTED_LANGS))
                    self.assertTrue(all(str(translations[lang]).strip() for lang in SUPPORTED_LANGS))

    def test_web_metric_labels_are_count_free_and_have_singular_variants(self) -> None:
        local_file = Path(__file__).parents[1] / "airco_tracker" / "i18n_local.json"
        web = json.loads(local_file.read_text(encoding="utf-8"))["web"]
        metric_keys = (
            "metric_in_stock",
            "metric_in_stock_one",
            "metric_stores_stocked",
            "metric_stores_stocked_one",
            "metric_stores_tracked",
            "metric_stores_tracked_one",
        )

        for key in metric_keys:
            with self.subTest(key=key):
                self.assertEqual(set(web[key]), set(SUPPORTED_LANGS))
                self.assertTrue(all("{count}" not in web[key][lang] for lang in SUPPORTED_LANGS))

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
