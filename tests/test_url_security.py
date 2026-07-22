from __future__ import annotations

import unittest

from airco_tracker.adapters.registry import load_adapter_specs
from airco_tracker.models import Product
from airco_tracker.url_security import (
    MERCHANT_HOSTS_BY_SITE_ID,
    normalized_https_url,
    redirect_host_allowed,
    validate_affiliate_url,
    validate_discovered_merchant_url,
    validate_product_url,
)


class UrlSecurityTests(unittest.TestCase):
    def test_every_registered_adapter_has_an_explicit_merchant_allowlist(self) -> None:
        registered = {
            spec.site_id for spec in load_adapter_specs(["nl", "fr"])
        }
        self.assertEqual(registered - MERCHANT_HOSTS_BY_SITE_ID.keys(), set())

    def test_product_url_rejects_lookalikes_userinfo_and_unknown_merchants(self) -> None:
        invalid = (
            "https://evil.example/?next=https://www.e.leclerc/fp/1234",
            "https://www.e.leclerc@evil.example/fp/1234",
            "https://www.e.leclerc.evil.example/fp/1234",
            "http://www.e.leclerc/fp/1234",
            "https://www.e.leclerc:8443/fp/1234",
        )
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validate_product_url(value, site_id="fr:E.Leclerc France")

        with self.assertRaises(ValueError):
            Product(
                site="Unknown production merchant",
                name="Portable airco",
                url="https://unknown.example/product",
                available=True,
                country="fr",
            )

    def test_affiliate_url_is_exactly_bounded_to_approved_networks(self) -> None:
        self.assertEqual(
            validate_affiliate_url("https://www.awin1.com/cread.php?id=1#ignored"),
            "https://www.awin1.com/cread.php?id=1",
        )
        for value in (
            "https://awin1.com.evil.example/cread.php",
            "https://www.awin1.com@evil.example/cread.php",
            "javascript:alert(1)",
            "https://evil.example/redirect",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validate_affiliate_url(value)

    def test_optional_bad_affiliate_enrichment_never_replaces_canonical_url(self) -> None:
        product = Product(
            site="E.Leclerc France",
            name="Portable airco",
            url="https://www.e.leclerc/fp/1234",
            available=True,
            country="fr",
            affiliate_url="https://evil.example/redirect",
        )
        self.assertIsNone(product.affiliate_url)
        self.assertEqual(product.purchase_url, product.url)

    def test_normalization_rejects_control_characters_and_credentials(self) -> None:
        for value in (
            "https://shop.example/a\nInjected: yes",
            "https://user:secret@shop.example/a",
            "https:///missing-host",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalized_https_url(value)

    def test_redirects_allow_only_exact_www_peer_or_explicit_hosts(self) -> None:
        self.assertTrue(redirect_host_allowed("shop.example", "shop.example"))
        self.assertTrue(redirect_host_allowed("shop.example", "www.shop.example"))
        self.assertTrue(redirect_host_allowed("www.shop.example", "shop.example"))
        self.assertFalse(redirect_host_allowed("api.shop.example", "cdn.shop.example"))
        self.assertTrue(
            redirect_host_allowed(
                "api.shop.example",
                "cdn.shop.example",
                ("cdn.shop.example",),
            )
        )
        # Never infer that unrelated hosts share a trust boundary merely
        # because their public suffix contains more than one label.
        self.assertFalse(redirect_host_allowed("one.co.uk", "two.co.uk"))

    def test_html_discovery_is_limited_to_the_configured_merchant(self) -> None:
        self.assertEqual(
            validate_discovered_merchant_url(
                "https://www.hubo.nl/products/mobiele-airco#stock",
                site="Hubo",
            ),
            "https://www.hubo.nl/products/mobiele-airco",
        )
        for value in (
            "https://evil.example/products/mobiele-airco",
            "https://www.hubo.nl.evil.example/products/mobiele-airco",
            "https://127.0.0.1/products/mobiele-airco",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validate_discovered_merchant_url(value, site="Hubo")


if __name__ == "__main__":
    unittest.main()
