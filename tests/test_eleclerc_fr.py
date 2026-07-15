from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from airco_tracker.adapters.fr.eleclerc import (
    ELeclercFranceAdapter,
    _ELeclercLiveApiClient,
    _parse_iso_datetime,
)


NOW = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)


class _Fetcher:
    timeout = 25
    session = object()


class _Cache:
    def __init__(self, value=None) -> None:
        self.value = value
        self.saved = []

    def load(self, namespace, key):
        return self.value

    def save(self, namespace, key, payload):
        self.saved.append((namespace, key, payload))
        self.value = payload


class _BrokenCache(_Cache):
    def load(self, namespace, key):
        raise RuntimeError("corrupt cache")


class _Client:
    def __init__(self, search_pages=None, details=None, search_error=None) -> None:
        self.search_pages = search_pages or {}
        self.details = details or {}
        self.search_error = search_error
        self.search_calls = []
        self.bulk_calls = []

    def search(self, query, page, size):
        self.search_calls.append((query, page, size))
        if self.search_error is not None:
            raise self.search_error
        return self.search_pages[(query, page)]

    def product_details(self, skus):
        requested = list(skus)
        self.bulk_calls.append(requested)
        return [self.details[sku] for sku in requested if sku in self.details]


class _Response:
    def __init__(self, payload) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self.payload


class _Session:
    def __init__(self) -> None:
        self.post_calls = []
        self.get_calls = []

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        return _Response({"items": [], "count": 0, "total": 0})

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        return _Response([])


def _search_item(
    sku: str,
    name: str = "Climatiseur mobile 9000 BTU R290",
    family: str = "climatiseur",
):
    return {
        "id": sku,
        "sku": sku,
        "label": name,
        "slug": f"product-{sku}",
        "family": {"code": family, "label": family},
        "attributeGroups": [
            {
                "attributes": [
                    {"code": "description", "value": "Compresseur, tuyau et réfrigérant R290"}
                ]
            }
        ],
        "variants": [{"id": sku, "sku": sku, "offers": []}],
    }


def _offer(
    status: str,
    *,
    price_cents: int = 49900,
    discount_price_cents: int | None = None,
    stock: int | None = 1,
    seller: str = "E.Leclerc Test",
    offer_id: str = "offer-1",
    start: str = "2026-07-01T00:00:00.1234567Z",
    currency: str = "EUR",
):
    result = {
        "id": offer_id,
        "startDate": start,
        "currency": {"code": currency, "symbol": "€"},
        "shop": {"id": f"shop-{offer_id}", "label": seller, "signCode": "0000"},
        "basePrice": {"price": {"price": price_cents, "priceWithAllTaxes": price_cents}},
        "additionalFields": [
            {"code": "availability-status", "type": "text", "value": status},
            {"code": "type", "type": "multiSelect", "value": ["marketplace"]},
        ],
    }
    if stock is not None:
        result["stock"] = stock
    if discount_price_cents is not None:
        result["basePrice"]["discountPrice"] = {
            "price": {
                "price": discount_price_cents,
                "priceWithAllTaxes": discount_price_cents,
            }
        }
    return result


def _detail(sku: str, offers, name: str = "Climatiseur mobile 9000 BTU R290"):
    item = _search_item(sku, name)
    item["variants"] = [
        {
            "id": sku,
            "sku": sku,
            "slug": f"product-{sku}",
            "attributes": [{"code": "ean", "value": sku}],
            "offers": offers,
        }
    ]
    return item


def _search_payload(items, total=None):
    return {
        "items": items,
        "count": len(items),
        "total": len(items) if total is None else total,
    }


def _cache_payload(*skus: str, imported: datetime = NOW):
    return {
        "version": 2,
        "last_imported": imported.isoformat(),
        "rows": [{"sku": sku} for sku in skus],
        "source_row_count": len(skus),
    }


class ELeclercFranceAdapterTests(unittest.TestCase):
    def test_dotnet_fractional_seconds_are_normalised_for_python_39(self) -> None:
        self.assertEqual(
            _parse_iso_datetime("2026-07-14T20:38:03.17705+00:00").microsecond,
            177050,
        )
        self.assertEqual(
            _parse_iso_datetime("2026-07-01T00:00:00.1234567Z").microsecond,
            123456,
        )

    def test_live_client_uses_filtered_discovery_and_repeated_bulk_sku_params(self) -> None:
        session = _Session()
        client = _ELeclercLiveApiClient(session, 25)

        client.search("climatiseur mobile", 1, 96)
        client.product_details(["8690842747755", "8436597490085"])

        search = session.post_calls[0][1]
        self.assertEqual(
            search["json"],
            {
                "text": "climatiseur mobile",
                "page": 1,
                "size": 96,
                "filters": {"type_de_produit": {"value": ["Climatiseur"]}},
            },
        )
        self.assertEqual(
            session.get_calls[0][1]["params"],
            [("skus", "8690842747755"), ("skus", "8436597490085")],
        )

    def test_discovery_is_cached_for_twelve_hours_but_stock_is_always_refreshed(self) -> None:
        sku = "8690842747755"
        item = _search_item(sku)
        client = _Client(
            search_pages={
                ("climatiseur mobile", 1): _search_payload([item]),
            },
            details={sku: _detail(sku, [_offer("in-stock")])},
        )
        cache = _Cache()
        adapter = ELeclercFranceAdapter(
            _Fetcher(), cache=cache, client=client, now=lambda: NOW, sleep=lambda _delay: None
        )

        first = adapter.fetch_products()
        second = adapter.fetch_products()

        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)
        self.assertEqual(len(client.search_calls), 1)
        self.assertEqual(client.bulk_calls, [[sku], [sku]])
        self.assertEqual(cache.saved[0][2]["rows"], [{"sku": sku}])
        self.assertTrue(first[0].available)

    def test_corrupt_discovery_cache_is_rebuilt_from_the_live_api(self) -> None:
        sku = "8690842747755"
        item = _search_item(sku)
        client = _Client(
            search_pages={
                ("climatiseur mobile", 1): _search_payload([item]),
            },
            details={sku: _detail(sku, [_offer("in-stock")])},
        )
        cache = _BrokenCache()

        with self.assertLogs("airco_tracker.adapters.fr.eleclerc", level="WARNING"):
            products = ELeclercFranceAdapter(
                _Fetcher(),
                cache=cache,
                client=client,
                now=lambda: NOW,
                sleep=lambda _delay: None,
            ).fetch_products()

        self.assertEqual(len(products), 1)
        self.assertEqual(len(client.search_calls), 1)
        self.assertEqual(cache.saved[0][2]["rows"], [{"sku": sku}])

    def test_search_paginates_one_based_and_bulk_refreshes_in_batches(self) -> None:
        skus = ["1001", "1002", "1003"]
        client = _Client(
            search_pages={
                ("climatiseur mobile", 1): _search_payload(
                    [_search_item(skus[0]), _search_item(skus[1])], total=3
                ),
                ("climatiseur mobile", 2): _search_payload([_search_item(skus[2])], total=3),
            },
            details={sku: _detail(sku, [_offer("in-stock", offer_id=sku)]) for sku in skus},
        )
        with (
            patch("airco_tracker.adapters.fr.eleclerc._DISCOVERY_PAGE_SIZE", 2),
            patch("airco_tracker.adapters.fr.eleclerc._BULK_BATCH_SIZE", 2),
        ):
            products = ELeclercFranceAdapter(
                _Fetcher(),
                cache=_Cache(),
                client=client,
                now=lambda: NOW,
                sleep=lambda _delay: None,
            ).fetch_products()

        self.assertEqual(len(products), 3)
        self.assertEqual([call[1] for call in client.search_calls], [1, 2])
        self.assertEqual(client.bulk_calls, [["1001", "1002"], ["1003"]])

    def test_discovery_requires_exact_climatiseur_family_and_real_portable_ac(self) -> None:
        real = _search_item("2001")
        porta_split = _search_item("2005", "Midea PortaSplit climatiseur 12000 BTU")
        accessory = _search_item(
            "2002",
            "Kit de calfeutrage pour climatiseur mobile",
            "accessoires_climatisation",
        )
        cooler = _search_item(
            "2003",
            "Rafraîchisseur d'air et climatiseur mobile avec réservoir",
        )
        wall = _search_item("2004", "Climatiseur mural mobile split 12000 BTU")
        client = _Client(
            search_pages={
                ("climatiseur mobile", 1): _search_payload(
                    [real, porta_split, accessory, cooler, wall]
                )
            },
            details={
                "2001": _detail("2001", [_offer("in-stock")]),
                "2005": _detail(
                    "2005",
                    [_offer("in-stock", offer_id="porta")],
                    "Midea PortaSplit climatiseur 12000 BTU",
                ),
            },
        )
        products = ELeclercFranceAdapter(
            _Fetcher(),
            cache=_Cache(),
            client=client,
            now=lambda: NOW,
            sleep=lambda _delay: None,
        ).fetch_products()

        self.assertEqual(len(products), 2)
        self.assertEqual(client.bulk_calls, [["2001", "2005"]])

    def test_valid_title_is_not_rejected_by_comparison_text_or_dehumidifier(self) -> None:
        sku = "2006"
        item = _search_item(
            sku,
            "Climatiseur mobile 9000 BTU avec déshumidificateur",
        )
        item["attributeGroups"][0]["attributes"][0]["value"] = (
            "Humidificateur : Non. Comparaison avec un climatiseur mural."
        )
        detail = _detail(
            sku,
            [_offer("in-stock")],
            "Climatiseur mobile 9000 BTU avec déshumidificateur",
        )
        detail["attributeGroups"] = item["attributeGroups"]
        client = _Client(
            search_pages={
                ("climatiseur mobile", 1): _search_payload([item]),
            },
            details={sku: detail},
        )

        products = ELeclercFranceAdapter(
            _Fetcher(),
            cache=_Cache(),
            client=client,
            now=lambda: NOW,
            sleep=lambda _delay: None,
        ).fetch_products()

        self.assertEqual(len(products), 1)
        self.assertTrue(products[0].available)

    def test_immediate_offer_wins_then_uses_its_cheapest_valid_seller(self) -> None:
        sku = "3001"
        offers = [
            _offer(
                "preorder", price_cents=19900, stock=0, seller="Presale", offer_id="pre"
            ),
            _offer(
                "in-stock", price_cents=59900, stock=2, seller="Expensive", offer_id="high"
            ),
            _offer(
                "in-stock", price_cents=44900, stock=1, seller="Cheapest", offer_id="low"
            ),
        ]
        client = _Client(details={sku: _detail(sku, offers)})
        product = ELeclercFranceAdapter(
            _Fetcher(),
            cache=_Cache(_cache_payload(sku)),
            client=client,
            now=lambda: NOW,
        ).fetch_products()[0]

        self.assertTrue(product.available)
        self.assertFalse(product.presale)
        self.assertEqual(product.price_eur, 449.0)
        self.assertIn("Cheapest", product.delivery or "")
        self.assertEqual(product.btu, 9000)
        query = parse_qs(urlsplit(product.url).query)
        self.assertEqual(query["awinmid"], ["15135"])
        self.assertEqual(query["awinaffid"], ["2981827"])
        self.assertEqual(query["cons"], ["0"])
        self.assertEqual(query["ued"], [f"https://www.e.leclerc/fp/{sku}"])

    def test_current_discount_price_is_used_before_base_price(self) -> None:
        sku = "8056159259297"
        client = _Client(
            details={
                sku: _detail(
                    sku,
                    [
                        _offer(
                            "in-stock",
                            price_cents=144899,
                            discount_price_cents=129990,
                        )
                    ],
                )
            }
        )

        product = ELeclercFranceAdapter(
            _Fetcher(),
            cache=_Cache(_cache_payload(sku)),
            client=client,
            now=lambda: NOW,
        ).fetch_products()[0]

        self.assertEqual(product.price_eur, 1299.90)

    def test_presale_statuses_are_separate_and_shipped_under_is_not_available(self) -> None:
        presale_sku = "4001"
        shipped_sku = "4002"
        client = _Client(
            details={
                presale_sku: _detail(
                    presale_sku,
                    [
                        _offer(
                            "future-stock",
                            price_cents=39900,
                            stock=0,
                            start="2026-07-20T00:00:00Z",
                            offer_id="future",
                        ),
                        _offer(
                            "unlimited-preorder",
                            price_cents=34900,
                            stock=None,
                            offer_id="preorder",
                        ),
                    ],
                ),
                shipped_sku: _detail(
                    shipped_sku,
                    [
                        _offer("shipped-under", price_cents=29900, stock=20),
                        _offer("unavailable", price_cents=28900, stock=0, offer_id="unavailable"),
                    ],
                ),
            }
        )
        products = ELeclercFranceAdapter(
            _Fetcher(),
            cache=_Cache(_cache_payload(presale_sku, shipped_sku)),
            client=client,
            now=lambda: NOW,
        ).fetch_products()
        by_name = {
            urlsplit(parse_qs(urlsplit(product.url).query)["ued"][0]).path.rsplit("/", 1)[
                -1
            ]: product
            for product in products
        }

        self.assertTrue(by_name[presale_sku].available)
        self.assertTrue(by_name[presale_sku].presale)
        self.assertEqual(by_name[presale_sku].price_eur, 349.0)
        self.assertFalse(by_name[shipped_sku].available)
        self.assertFalse(by_name[shipped_sku].presale)
        self.assertIsNone(by_name[shipped_sku].price_eur)

    def test_future_offer_start_is_not_exposed_as_active_presale(self) -> None:
        sku = "4003"
        client = _Client(
            details={
                sku: _detail(
                    sku,
                    [
                        _offer(
                            "future-stock",
                            price_cents=39900,
                            stock=0,
                            start="2026-07-20T00:00:00Z",
                        )
                    ],
                )
            }
        )

        product = ELeclercFranceAdapter(
            _Fetcher(),
            cache=_Cache(_cache_payload(sku)),
            client=client,
            now=lambda: NOW,
        ).fetch_products()[0]

        self.assertFalse(product.available)
        self.assertFalse(product.presale)
        self.assertIsNone(product.price_eur)

    def test_response_time_clock_skew_does_not_hide_current_stock(self) -> None:
        sku = "4004"
        client = _Client(
            details={
                sku: _detail(
                    sku,
                    [
                        _offer(
                            "in-stock",
                            stock=1,
                            start="2026-07-14T12:00:30Z",
                        )
                    ],
                )
            }
        )

        product = ELeclercFranceAdapter(
            _Fetcher(),
            cache=_Cache(_cache_payload(sku)),
            client=client,
            now=lambda: NOW,
        ).fetch_products()[0]

        self.assertTrue(product.available)
        self.assertFalse(product.presale)

    def test_valid_empty_discovery_clears_the_known_sku_cache(self) -> None:
        cache = _Cache()
        client = _Client(
            search_pages={
                ("climatiseur mobile", 1): _search_payload([]),
            }
        )

        products = ELeclercFranceAdapter(
            _Fetcher(),
            cache=cache,
            client=client,
            now=lambda: NOW,
            sleep=lambda _delay: None,
        ).fetch_products()

        self.assertEqual(products, [])
        self.assertEqual(cache.saved[0][2]["rows"], [])
        self.assertEqual(cache.saved[0][2]["source_row_count"], 0)

    def test_missing_bulk_sku_and_unknown_status_fail_closed(self) -> None:
        client = _Client(details={"5001": _detail("5001", [_offer("in-stock")])})
        with self.assertRaisesRegex(RuntimeError, "omitted known SKU"):
            ELeclercFranceAdapter(
                _Fetcher(),
                cache=_Cache(_cache_payload("5001", "5002")),
                client=client,
                now=lambda: NOW,
            ).fetch_products()

        unknown = _Client(
            details={"5003": _detail("5003", [_offer("available-someday")])}
        )
        with self.assertRaisesRegex(RuntimeError, "invalid availability status"):
            ELeclercFranceAdapter(
                _Fetcher(),
                cache=_Cache(_cache_payload("5003")),
                client=unknown,
                now=lambda: NOW,
            ).fetch_products()

    def test_malformed_available_offer_fails_instead_of_erasing_stock(self) -> None:
        sku = "5004"
        invalid = _offer("in-stock", start="not-a-date")
        client = _Client(details={sku: _detail(sku, [invalid])})
        with self.assertRaisesRegex(RuntimeError, "invalid startDate"):
            ELeclercFranceAdapter(
                _Fetcher(),
                cache=_Cache(_cache_payload(sku)),
                client=client,
                now=lambda: NOW,
            ).fetch_products()

    def test_stale_discovery_cache_is_safe_fallback_because_stock_is_refreshed(self) -> None:
        sku = "6001"
        client = _Client(
            details={sku: _detail(sku, [_offer("in-stock")])},
            search_error=RuntimeError("rate limited"),
        )
        with self.assertLogs("airco_tracker.adapters.fr.eleclerc", level="WARNING"):
            products = ELeclercFranceAdapter(
                _Fetcher(),
                cache=_Cache(_cache_payload(sku, imported=NOW - timedelta(hours=13))),
                client=client,
                now=lambda: NOW,
                sleep=lambda _delay: None,
            ).fetch_products()

        self.assertEqual(len(products), 1)
        self.assertEqual(client.bulk_calls, [[sku]])
        self.assertTrue(products[0].available)

    def test_invalid_search_schema_fails_before_cache_is_written(self) -> None:
        client = _Client(
            search_pages={
                ("climatiseur mobile", 1): {"items": [], "count": 1, "total": 1}
            }
        )
        cache = _Cache()
        with (
            self.assertRaisesRegex(RuntimeError, "invalid search response schema"),
        ):
            ELeclercFranceAdapter(
                _Fetcher(),
                cache=cache,
                client=client,
                now=lambda: NOW,
                sleep=lambda _delay: None,
            ).fetch_products()
        self.assertEqual(cache.saved, [])


if __name__ == "__main__":
    unittest.main()
