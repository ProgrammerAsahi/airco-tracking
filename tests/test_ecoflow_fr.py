from __future__ import annotations

import json
import unittest

from airco_tracker.adapters.fr.ecoflow import EcoFlowFranceAdapter


COLLECTION_URL = "https://fr.ecoflow.com/collections/wave-serie/products.json?limit=250"
PRODUCT_URL = "https://fr.ecoflow.com/products/wave-3-portable-air-conditioner"


class _Fetcher:
    def __init__(self, responses: dict[str, str]) -> None:
        self.responses = responses

    def get(self, url: str) -> str:
        return self.responses[url]


class _Response:
    content = b'{"products": []}'
    headers = {"Content-Type": "application/json; charset=utf-8"}
    encoding = "utf-8"

    def raise_for_status(self) -> None:
        return None


class _Session:
    def get(self, url: str, **kwargs):
        self.call = (url, kwargs)
        return _Response()


class _ProductionFetcher:
    timeout = 25

    def __init__(self) -> None:
        self.session = _Session()

    def get(self, url: str) -> str:
        raise AssertionError("the generic HTML minimum-size check must not be used")


class EcoFlowFranceAdapterTests(unittest.TestCase):
    def test_tracks_immediate_and_preorder_variants_without_sold_out_items(self) -> None:
        collection = {
            "products": [
                {
                    "title": "Climatiseur portable EcoFlow WAVE 3",
                    "handle": "wave-3-portable-air-conditioner",
                    "body_html": "<p>Puissance de refroidissement 1 800 W</p>",
                    "variants": [
                        {
                            "id": 101,
                            "title": "WAVE 3 + DELTA 3 Max",
                            "available": True,
                            "price": 219800,
                        },
                        {
                            "id": 102,
                            "title": "WAVE 3 + batterie supplémentaire WAVE 3",
                            "available": False,
                            "price": "1499.00",
                        },
                        {
                            "id": 103,
                            "title": "WAVE 3 + DELTA 2 Max",
                            "available": False,
                            "price": 209800,
                        },
                        {
                            "id": 104,
                            "title": "WAVE 3 seule — précommande ouverte",
                            "available": True,
                            "price": 89900,
                        },
                        {
                            "id": 105,
                            "title": "Default Title",
                            "available": False,
                            "price": 89900,
                        },
                    ],
                },
                {
                    "title": "Batterie supplémentaire EcoFlow WAVE 3",
                    "handle": "wave-3-add-on-battery",
                    "body_html": "<p>Accessoire</p>",
                    "variants": [
                        {"id": 999, "title": "WAVE 3 batterie", "available": True, "price": 69900}
                    ],
                },
            ]
        }
        page = """
        <div class="swatch-element soldout" data-value="WAVE 3 + batterie supplémentaire WAVE 3">
          <div class="swatch-details-wrap">
            <span class="swatch-title">WAVE 3 + batterie supplémentaire WAVE 3</span>
            <span>Précommandez dès maintenant, expédition à partir du 20 juillet 2026.</span>
          </div>
        </div>
        <div class="swatch-element soldout" data-value="WAVE 3 + DELTA 2 Max">
          <span class="swatch-title">WAVE 3 + DELTA 2 Max</span>
          <span>Épuisé</span>
        </div>
        <div class="swatch-element" data-value="WAVE 3 seule — précommande ouverte">
          <span class="swatch-title">WAVE 3 seule — précommande ouverte</span>
          <span>Précommandez dès maintenant, expédition à partir du 25 juillet 2026.</span>
        </div>
        """
        adapter = EcoFlowFranceAdapter(
            _Fetcher({COLLECTION_URL: json.dumps(collection), PRODUCT_URL: page})
        )

        products = adapter.fetch_products()

        self.assertEqual(len(products), 5)
        immediate = next(product for product in products if product.url.endswith("variant=101"))
        marketed_but_sold_out = next(
            product for product in products if product.url.endswith("variant=102")
        )
        sold_out = next(product for product in products if product.url.endswith("variant=103"))
        preorder = next(product for product in products if product.url.endswith("variant=104"))
        default_variant = next(
            product for product in products if product.url.endswith("variant=105")
        )
        self.assertTrue(immediate.available)
        self.assertFalse(immediate.presale)
        self.assertEqual(immediate.price_eur, 2198.0)
        self.assertEqual(immediate.btu, 6100)
        self.assertFalse(marketed_but_sold_out.available)
        self.assertFalse(marketed_but_sold_out.presale)
        self.assertTrue(preorder.available)
        self.assertTrue(preorder.presale)
        self.assertEqual(preorder.price_eur, 899.0)
        self.assertIn("25 juillet 2026", preorder.delivery or "")
        self.assertFalse(sold_out.available)
        self.assertFalse(sold_out.presale)
        self.assertFalse(default_variant.available)
        self.assertTrue(all("batterie" not in p.name.casefold() or "climatiseur" in p.name.casefold() for p in products))

    def test_invalid_collection_fails_closed(self) -> None:
        adapter = EcoFlowFranceAdapter(_Fetcher({COLLECTION_URL: "{}"}))
        with self.assertRaisesRegex(RuntimeError, "no product list"):
            adapter.fetch_products()

    def test_explicit_empty_collection_is_a_successful_empty_snapshot(self) -> None:
        fetcher = _ProductionFetcher()
        adapter = EcoFlowFranceAdapter(fetcher)
        self.assertEqual(adapter.fetch_products(), [])
        self.assertEqual(fetcher.session.call[0], COLLECTION_URL)
        self.assertEqual(fetcher.session.call[1]["headers"], {"Accept": "application/json"})

    def test_relevant_product_with_broken_variant_schema_fails_closed(self) -> None:
        collection = {
            "products": [
                {
                    "title": "Climatiseur portable EcoFlow WAVE 3",
                    "handle": "wave-3-portable-air-conditioner",
                    "body_html": "<p>Puissance de refroidissement 1 800 W</p>",
                    "variants": [
                        {
                            "id": 101,
                            "title": "WAVE 3",
                            "available": True,
                            "price": 89900,
                        }
                    ],
                },
                {
                    "title": "Climatiseur portable EcoFlow WAVE 2",
                    "handle": "wave-2-portable-air-conditioner",
                    "body_html": "<p>Puissance de refroidissement 1 500 W</p>",
                    "variants": None,
                },
            ]
        }
        adapter = EcoFlowFranceAdapter(
            _Fetcher(
                {
                    COLLECTION_URL: json.dumps(collection),
                    PRODUCT_URL: "<main>En stock</main>",
                }
            )
        )

        with self.assertRaisesRegex(RuntimeError, "no variant list"):
            adapter.fetch_products()

    def test_preorder_selector_drift_fails_instead_of_reporting_immediate_stock(self) -> None:
        collection = {
            "products": [
                {
                    "title": "Climatiseur portable EcoFlow WAVE 3",
                    "handle": "wave-3-portable-air-conditioner",
                    "body_html": "<p>Puissance de refroidissement 1 800 W</p>",
                    "variants": [
                        {"id": 101, "title": "WAVE 3 seule", "available": True, "price": 89900}
                    ],
                }
            ]
        }
        changed_markup = """
        <div class="new-variant-card">
          <span class="new-variant-name">WAVE 3 seule</span>
          <span>Précommandez dès maintenant, expédition à partir du 20 juillet 2026.</span>
        </div>
        """
        adapter = EcoFlowFranceAdapter(
            _Fetcher({COLLECTION_URL: json.dumps(collection), PRODUCT_URL: changed_markup})
        )

        with self.assertRaisesRegex(RuntimeError, "preorder copy could not be mapped"):
            adapter.fetch_products()

    def test_partial_preorder_selector_drift_fails_closed(self) -> None:
        collection = {
            "products": [
                {
                    "title": "Climatiseur portable EcoFlow WAVE 3",
                    "handle": "wave-3-portable-air-conditioner",
                    "body_html": "<p>Puissance de refroidissement 1 800 W</p>",
                    "variants": [
                        {
                            "id": 101,
                            "title": "WAVE 3 seule",
                            "available": True,
                            "price": 89900,
                        },
                        {
                            "id": 102,
                            "title": "WAVE 3 + batterie",
                            "available": True,
                            "price": 149900,
                        },
                    ],
                }
            ]
        }
        partially_changed_markup = """
        <div class="swatch-element" data-value="WAVE 3 seule">
          <span class="swatch-title">WAVE 3 seule</span>
          <span>Précommandez dès maintenant, expédition à partir du 20 juillet 2026.</span>
        </div>
        <div class="new-variant-card">
          <span class="new-variant-name">WAVE 3 + batterie</span>
          <span>Précommandez dès maintenant, expédition à partir du 25 juillet 2026.</span>
        </div>
        """
        adapter = EcoFlowFranceAdapter(
            _Fetcher(
                {
                    COLLECTION_URL: json.dumps(collection),
                    PRODUCT_URL: partially_changed_markup,
                }
            )
        )

        with self.assertRaisesRegex(RuntimeError, "preorder copy could not be mapped"):
            adapter.fetch_products()

    def test_preorder_title_drift_fails_instead_of_reporting_immediate_stock(self) -> None:
        collection = {
            "products": [
                {
                    "title": "Climatiseur portable EcoFlow WAVE 3",
                    "handle": "wave-3-portable-air-conditioner",
                    "body_html": "<p>Puissance de refroidissement 1 800 W</p>",
                    "variants": [
                        {
                            "id": 101,
                            "title": "WAVE 3 seule",
                            "available": True,
                            "price": 89900,
                        }
                    ],
                }
            ]
        }
        mismatched_title = """
        <div class="swatch-element" data-value="WAVE 3 uniquement">
          <span class="swatch-title">WAVE 3 uniquement</span>
          <span>Précommandez dès maintenant, expédition à partir du 20 juillet 2026.</span>
        </div>
        """
        adapter = EcoFlowFranceAdapter(
            _Fetcher({COLLECTION_URL: json.dumps(collection), PRODUCT_URL: mismatched_title})
        )

        with self.assertRaisesRegex(RuntimeError, "preorder copy could not be mapped"):
            adapter.fetch_products()

    def test_non_boolean_availability_fails_closed(self) -> None:
        collection = {
            "products": [
                {
                    "title": "Climatiseur portable EcoFlow WAVE 3",
                    "handle": "wave-3-portable-air-conditioner",
                    "body_html": "<p>Puissance de refroidissement 1 800 W</p>",
                    "variants": [
                        {
                            "id": 101,
                            "title": "WAVE 3 seule",
                            "available": "false",
                            "price": 89900,
                        }
                    ],
                }
            ]
        }
        adapter = EcoFlowFranceAdapter(
            _Fetcher({COLLECTION_URL: json.dumps(collection), PRODUCT_URL: "<main />"})
        )

        with self.assertRaisesRegex(RuntimeError, "invalid availability"):
            adapter.fetch_products()

    def test_missing_or_non_positive_variant_price_fails_closed(self) -> None:
        invalid_prices = (None, 0, -100, "free")
        for price in invalid_prices:
            with self.subTest(price=price):
                variant = {
                    "id": 101,
                    "title": "WAVE 3 seule",
                    "available": True,
                }
                if price is not None:
                    variant["price"] = price
                collection = {
                    "products": [
                        {
                            "title": "Climatiseur portable EcoFlow WAVE 3",
                            "handle": "wave-3-portable-air-conditioner",
                            "body_html": "<p>Puissance de refroidissement 1 800 W</p>",
                            "variants": [variant],
                        }
                    ]
                }
                adapter = EcoFlowFranceAdapter(
                    _Fetcher(
                        {COLLECTION_URL: json.dumps(collection), PRODUCT_URL: "<main />"}
                    )
                )

                with self.assertRaisesRegex(RuntimeError, "invalid price"):
                    adapter.fetch_products()


if __name__ == "__main__":
    unittest.main()
