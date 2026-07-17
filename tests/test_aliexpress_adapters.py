from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest import mock

from airco_tracker.adapters.fr.aliexpress import AliExpressFranceAdapter
from airco_tracker.adapters.nl.aliexpress import AliExpressNetherlandsAdapter
from airco_tracker.adapters.shared.aliexpress import (
    AliExpressAvailabilityUnknown,
    AliExpressSkuNoResult,
)
from airco_tracker.aliexpress import AliExpressApiError


NOW = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
PRODUCT_ID = "1005009123456789"
CANONICAL_URL = f"https://www.aliexpress.com/item/{PRODUCT_ID}.html"
SOURCE_URL = f"https://fr.aliexpress.com/item/{PRODUCT_ID}.html"
PROMOTION_URL = "https://s.click.aliexpress.com/e/_example123"


class _Fetcher:
    timeout = 25
    session = object()


class _Cache:
    def __init__(self, value=None) -> None:
        self.value = value
        self.loads = []
        self.saves = []

    def load(self, namespace, key):
        self.loads.append((namespace, key))
        return self.value

    def save(self, namespace, key, payload):
        self.saves.append((namespace, key, payload))
        self.value = payload


class _FailingSaveCache(_Cache):
    def save(self, namespace, key, payload):
        self.saves.append((namespace, key, payload))
        raise RuntimeError("cache unavailable")


class _Client:
    def __init__(self, *, rows=None, sku_payloads=None, query_payloads=None) -> None:
        self.rows = list(rows or [])
        self.sku_payloads = dict(sku_payloads or {})
        self.query_payloads = (
            list(query_payloads) if query_payloads is not None else None
        )
        self.query_calls = []
        self.sku_calls = []

    def product_query(self, params):
        self.query_calls.append(dict(params))
        if self.query_payloads is not None:
            payload = self.query_payloads[len(self.query_calls) - 1]
            if isinstance(payload, Exception):
                raise payload
            return payload
        # Return candidates once; the remaining localized discovery queries
        # are valid explicit-empty pages.
        rows = self.rows if len(self.query_calls) == 1 else []
        return _query_payload(rows)

    def product_sku_detail(self, params):
        copied = dict(params)
        self.sku_calls.append(copied)
        payload = self.sku_payloads[copied["product_id"]]
        if isinstance(payload, Exception):
            raise payload
        return payload


def _query_row(
    product_id=PRODUCT_ID,
    title="Climatiseur mobile Midea 12000 BTU",
    *,
    detail_url=None,
):
    if detail_url is None:
        detail_url = (
            f"https://fr.aliexpress.com/item/{product_id}.html?gatewayAdapt=glo2fra"
        )
    return {
        "product_id": product_id,
        "product_title": title,
        "product_detail_url": detail_url,
        "promotion_link": PROMOTION_URL,
        "target_sale_price": "399.99",
        "target_sale_price_currency": "EUR",
        "first_level_category_name": "Électroménager",
        "second_level_category_name": "Climatiseurs",
    }


def _query_payload(rows):
    return {
        "result": {
            "current_page_no": 1,
            "current_record_count": len(rows),
            "total_page_no": 1 if rows else 0,
            "total_record_count": len(rows),
            **({"products": {"product": rows}} if rows else {}),
        }
    }


def _page_payload(rows, *, page, total_pages, total_records=None):
    return {
        "result": {
            "current_page_no": page,
            "current_record_count": len(rows),
            "total_page_no": total_pages,
            "total_record_count": total_records if total_records is not None else len(rows),
            **({"products": {"product": rows}} if rows else {}),
        }
    }


def _sku(
    sku_id="20000000000000001",
    *,
    price="399.99",
    currency="EUR",
    stock_key=None,
    stock_value=None,
    properties="Cooling capacity: 12000 BTU; EU plug",
):
    value = {
        "sku_id": sku_id,
        "currency": currency,
        "price_with_tax": "449.99",
        "sale_price_with_tax": price,
        "shipping_fees": "0.00",
        "min_delivery_days": 3,
        "max_delivery_days": 7,
        "ship_from_country": "FR",
        "color": "White",
        "size": "12000 BTU",
        "sku_properties": properties,
        "link": PROMOTION_URL,
    }
    if stock_key is not None:
        value[stock_key] = stock_value
    return value


def _sku_payload(*skus, title="Climatiseur mobile Midea 12000 BTU"):
    # This is the exact streamlined business shape documented by the user:
    # code/success at the business level and ae_item_info under result.
    return {
        "code": "200",
        "success": "true",
        "result": {
            "ae_item_info": {
                "product_id": PRODUCT_ID,
                "title": title,
                "en_title": "Midea 12000 BTU Portable Air Conditioner",
                "original_link": f"{SOURCE_URL}?spm=tracking-value",
                "display_category_name_l1": "Home Appliances",
                "display_category_name_l2": "Air Conditioners",
            },
            "ae_item_sku_info": list(skus),
        },
    }


def _adapter(
    adapter_class,
    sku_payload,
    *,
    rows=None,
    cache=None,
    query_payloads=None,
    monotonic=None,
    verified_stock_field=None,
):
    client = _Client(
        rows=[_query_row()] if rows is None else rows,
        sku_payloads={PRODUCT_ID: sku_payload},
        query_payloads=query_payloads,
    )
    concrete_adapter_class = adapter_class
    if verified_stock_field is not None:
        concrete_adapter_class = type(
            f"Verified{adapter_class.__name__}",
            (adapter_class,),
            {"verified_stock_field": verified_stock_field},
        )
    adapter = concrete_adapter_class(
        _Fetcher(),
        client=client,
        cache=cache or _Cache(),
        now=lambda: NOW,
        monotonic=monotonic,
    )
    return adapter, client


class AliExpressAdapterTests(unittest.TestCase):
    def test_documented_sku_metadata_never_becomes_stock(self) -> None:
        adapter, client = _adapter(AliExpressFranceAdapter, _sku_payload(_sku()))

        with self.assertRaisesRegex(
            AliExpressAvailabilityUnknown, "did not provide stock evidence"
        ):
            adapter.fetch_products()

        self.assertEqual(len(client.sku_calls), 1)
        self.assertEqual(
            client.sku_calls[0],
            {
                "ship_to_country": "FR",
                "product_id": PRODUCT_ID,
                "target_currency": "EUR",
                "target_language": "FR",
                "need_deliver_info": "Yes",
            },
        )
        self.assertTrue(client.query_calls)
        self.assertTrue(all(call["ship_to_country"] == "FR" for call in client.query_calls))
        self.assertTrue(all(call["target_currency"] == "EUR" for call in client.query_calls))

    def test_inspection_exposes_unknown_without_calling_it_available(self) -> None:
        adapter, _client = _adapter(AliExpressFranceAdapter, _sku_payload(_sku()))

        offers = adapter.inspect_offers()

        self.assertEqual(len(offers), 1)
        self.assertIsNone(offers[0].orderable)
        self.assertEqual(offers[0].price_eur, 399.99)
        self.assertEqual(offers[0].btu, 12000)
        self.assertEqual(offers[0].url, CANONICAL_URL)
        self.assertEqual(offers[0].affiliate_url, PROMOTION_URL)
        self.assertIn("3–7 jours", offers[0].delivery or "")

    def test_explicit_orderability_can_be_mapped_without_changing_identity(self) -> None:
        adapter, _client = _adapter(
            AliExpressFranceAdapter,
            _sku_payload(_sku(stock_key="is_orderable", stock_value=True)),
            verified_stock_field="is_orderable",
        )

        products = adapter.fetch_products()

        self.assertEqual(len(products), 1)
        product = products[0]
        self.assertTrue(product.available)
        self.assertFalse(product.presale)
        self.assertEqual(product.country, "fr")
        self.assertEqual(product.site, "AliExpress")
        self.assertEqual(product.url, CANONICAL_URL)
        self.assertEqual(product.affiliate_url, PROMOTION_URL)
        self.assertEqual(product.purchase_url, PROMOTION_URL)

    def test_explicit_sold_out_signal_is_not_reported_as_available(self) -> None:
        adapter, _client = _adapter(
            AliExpressNetherlandsAdapter,
            _sku_payload(_sku(stock_key="availability_status", stock_value="sold_out")),
            verified_stock_field="availability_status",
        )

        products = adapter.fetch_products()

        self.assertEqual(len(products), 1)
        self.assertFalse(products[0].available)
        self.assertEqual(products[0].country, "nl")
        self.assertIn("Verzonden vanuit FR", products[0].delivery or "")

    def test_twenty_unavailable_skus_remain_unknown_because_the_api_may_truncate(self) -> None:
        skus = [
            _sku(
                str(20_000_000_000_000_000 + index),
                stock_key="available",
                stock_value=False,
            )
            for index in range(20)
        ]
        adapter, _client = _adapter(
            AliExpressFranceAdapter,
            _sku_payload(*skus),
            verified_stock_field="available",
        )

        with self.assertRaisesRegex(
            AliExpressAvailabilityUnknown, "did not provide stock evidence"
        ):
            adapter.fetch_products()

    def test_twenty_skus_with_one_orderable_variant_prove_availability(self) -> None:
        skus = [
            _sku(
                str(20_000_000_000_000_000 + index),
                stock_key="available",
                stock_value=index == 19,
            )
            for index in range(20)
        ]
        adapter, _client = _adapter(
            AliExpressFranceAdapter,
            _sku_payload(*skus),
            verified_stock_field="available",
        )

        products = adapter.fetch_products()

        self.assertEqual(len(products), 1)
        self.assertTrue(products[0].available)

    def test_more_than_twenty_skus_is_rejected_as_an_invalid_contract(self) -> None:
        skus = [_sku(str(20_000_000_000_000_000 + index)) for index in range(21)]
        adapter, _client = _adapter(
            AliExpressFranceAdapter,
            _sku_payload(*skus),
        )

        with self.assertRaisesRegex(RuntimeError, "invalid SKU list"):
            adapter.fetch_products()

    def test_live_traffic_sku_wrapper_is_supported(self) -> None:
        payload = _sku_payload(_sku())
        payload["result"]["ae_item_sku_info"] = {
            "traffic_sku_info_list": payload["result"]["ae_item_sku_info"]
        }
        adapter, _client = _adapter(AliExpressFranceAdapter, payload)

        offers = adapter.inspect_offers()

        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0].sku_id, "20000000000000001")

    def test_invalid_traffic_sku_wrappers_fail_closed(self) -> None:
        for wrapped in ({}, {"traffic_sku_info_list": None}, {"other": [_sku()]}):
            with self.subTest(wrapped=wrapped):
                payload = _sku_payload(_sku())
                payload["result"]["ae_item_sku_info"] = wrapped
                adapter, _client = _adapter(AliExpressFranceAdapter, payload)

                with self.assertRaisesRegex(RuntimeError, "invalid SKU list"):
                    adapter.inspect_offers()

    def test_405_is_unknown_and_never_mapped_to_sold_out(self) -> None:
        adapter, _client = _adapter(
            AliExpressFranceAdapter,
            AliExpressApiError(
                "aliexpress.affiliate.product.sku.detail.get", code="405"
            ),
        )

        with self.assertRaisesRegex(AliExpressSkuNoResult, "stock is unknown"):
            adapter.fetch_products()

    def test_live_code_15_sub_code_405_is_also_unknown(self) -> None:
        adapter, _client = _adapter(
            AliExpressFranceAdapter,
            AliExpressApiError(
                "aliexpress.affiliate.product.sku.detail.get",
                code="15",
                sub_code="405",
            ),
        )

        with self.assertRaisesRegex(AliExpressSkuNoResult, "stock is unknown"):
            adapter.fetch_products()

    def test_logistics_fields_alone_do_not_flip_availability(self) -> None:
        sku = _sku()
        sku.update(
            {
                "shipping_fees": "12.50",
                "delivery_days": 2,
                "ship_from_country": "NL",
            }
        )
        adapter, _client = _adapter(AliExpressNetherlandsAdapter, _sku_payload(sku))

        offers = adapter.inspect_offers()

        self.assertIsNone(offers[0].orderable)
        self.assertIn("verzendkosten € 12.50", offers[0].delivery or "")

    def test_accessories_coolers_and_fixed_systems_are_not_detailed(self) -> None:
        rows = [
            _query_row(),
            _query_row("1005009000000001", "Portable Air Conditioner Exhaust Hose Kit"),
            _query_row("1005009000000002", "Mini Evaporative Air Cooler USB"),
            _query_row("1005009000000003", "Wall Mounted Split Air Conditioner"),
        ]
        adapter, client = _adapter(
            AliExpressFranceAdapter,
            _sku_payload(_sku(stock_key="available", stock_value=True)),
            rows=rows,
            verified_stock_field="available",
        )

        products = adapter.fetch_products()

        self.assertEqual(len(products), 1)
        self.assertEqual([call["product_id"] for call in client.sku_calls], [PRODUCT_ID])

    def test_accessory_sku_and_below_threshold_variant_are_excluded(self) -> None:
        accessory = _sku(
            "20000000000000002",
            stock_key="available",
            stock_value=True,
            properties="Exhaust hose replacement for portable air conditioner",
        )
        cheap = _sku(
            "20000000000000003",
            price="39.99",
            stock_key="available",
            stock_value=True,
        )
        real = _sku(stock_key="available", stock_value=True)
        adapter, _client = _adapter(
            AliExpressFranceAdapter,
            _sku_payload(accessory, cheap, real),
            verified_stock_field="available",
        )

        products = adapter.fetch_products()

        self.assertEqual(len(products), 1)
        self.assertEqual(products[0].price_eur, 399.99)

    def test_fixed_system_variant_is_excluded_from_a_mixed_listing(self) -> None:
        wall = _sku(
            "20000000000000002",
            price="199.99",
            stock_key="available",
            stock_value=True,
            properties="Wall mounted split system 12000 BTU",
        )
        portable = _sku(stock_key="available", stock_value=True)
        adapter, _client = _adapter(
            AliExpressFranceAdapter,
            _sku_payload(wall, portable),
            verified_stock_field="available",
        )

        products = adapter.fetch_products()

        self.assertEqual(len(products), 1)
        self.assertEqual(products[0].price_eur, 399.99)

    def test_query_only_preorder_marker_is_preserved(self) -> None:
        row = _query_row(title="Pre-order climatiseur mobile Midea 12000 BTU")
        adapter, _client = _adapter(
            AliExpressFranceAdapter,
            _sku_payload(
                _sku(stock_key="available", stock_value=True),
                title="Climatiseur mobile Midea 12000 BTU",
            ),
            rows=[row],
            verified_stock_field="available",
        )

        products = adapter.fetch_products()

        self.assertEqual(len(products), 1)
        self.assertTrue(products[0].presale)

    def test_non_eur_fails_closed(self) -> None:
        wrong_currency, _client = _adapter(
            AliExpressFranceAdapter,
            _sku_payload(
                _sku(currency="USD", stock_key="available", stock_value=True)
            ),
            verified_stock_field="available",
        )
        with self.assertRaisesRegex(RuntimeError, "not denominated in EUR"):
            wrong_currency.fetch_products()

    def test_unverified_stock_shaped_fields_are_ignored(self) -> None:
        conflicting = _sku(stock_key="available", stock_value=True)
        conflicting["stock_quantity"] = 0
        conflict_adapter, _client = _adapter(
            AliExpressFranceAdapter, _sku_payload(conflicting)
        )
        with self.assertRaisesRegex(
            AliExpressAvailabilityUnknown, "did not provide stock evidence"
        ):
            conflict_adapter.fetch_products()

    def test_unexpected_affiliate_host_is_dropped_without_changing_identity(self) -> None:
        sku = _sku(stock_key="available", stock_value=True)
        sku["link"] = "https://evil.example/redirect"
        row = _query_row()
        row["promotion_link"] = "https://evil.example/also-bad"
        adapter, _client = _adapter(
            AliExpressFranceAdapter,
            _sku_payload(sku),
            rows=[row],
            verified_stock_field="available",
        )

        product = adapter.fetch_products()[0]

        self.assertEqual(product.url, CANONICAL_URL)
        self.assertIsNone(product.affiliate_url)
        self.assertEqual(product.purchase_url, CANONICAL_URL)

    def test_locale_host_and_query_changes_do_not_change_product_identity(self) -> None:
        row = _query_row(
            detail_url=f"https://de.aliexpress.com/item/{PRODUCT_ID}.html?gatewayAdapt=glo2deu"
        )
        payload = _sku_payload(_sku(stock_key="available", stock_value=True))
        payload["result"]["ae_item_info"]["original_link"] = (
            f"https://nl.aliexpress.com/item/{PRODUCT_ID}.html?spm=another-value"
        )
        adapter, _client = _adapter(
            AliExpressFranceAdapter,
            payload,
            rows=[row],
            verified_stock_field="available",
        )

        self.assertEqual(adapter.fetch_products()[0].url, CANONICAL_URL)

    def test_query_or_sku_url_for_another_product_is_rejected(self) -> None:
        wrong_id = "1005009000000001"
        wrong_query = _query_row(
            detail_url=f"https://fr.aliexpress.com/item/{wrong_id}.html"
        )
        query_adapter, _client = _adapter(
            AliExpressFranceAdapter,
            _sku_payload(_sku()),
            rows=[wrong_query],
        )
        with self.assertRaisesRegex(RuntimeError, "does not match its product id"):
            query_adapter.fetch_products()

        wrong_sku = _sku_payload(_sku(stock_key="available", stock_value=True))
        wrong_sku["result"]["ae_item_info"]["original_link"] = (
            f"https://fr.aliexpress.com/item/{wrong_id}.html"
        )
        sku_adapter, _client = _adapter(
            AliExpressFranceAdapter,
            wrong_sku,
            verified_stock_field="available",
        )
        with self.assertRaisesRegex(RuntimeError, "does not match its product id"):
            sku_adapter.fetch_products()

    def test_discovery_cache_is_destination_scoped_and_sku_details_are_refreshed(self) -> None:
        cache = _Cache()
        adapter, client = _adapter(
            AliExpressFranceAdapter,
            _sku_payload(_sku(stock_key="available", stock_value=True)),
            cache=cache,
            verified_stock_field="available",
        )

        adapter.fetch_products()
        adapter.fetch_products()

        self.assertEqual(len(client.query_calls), 4)
        self.assertEqual(len(client.sku_calls), 2)
        self.assertEqual(cache.loads[0][0], "aliexpress-fr-discovery-v1")
        self.assertEqual(len(cache.saves), 1)

    def test_cache_source_row_count_mismatch_is_rejected_and_rebuilt(self) -> None:
        cache = _Cache(
            {
                "version": 3,
                "last_imported": NOW.isoformat(),
                "discovery_complete": True,
                "rows": [
                    {
                        "product_id": PRODUCT_ID,
                        "title": "Climatiseur mobile Midea 12000 BTU",
                        "detail_url": CANONICAL_URL,
                        "promotion_link": PROMOTION_URL,
                    }
                ],
                "source_row_count": 0,
            }
        )
        adapter, client = _adapter(
            AliExpressFranceAdapter,
            _sku_payload(_sku()),
            cache=cache,
        )

        offers = adapter.inspect_offers()

        self.assertEqual(len(offers), 1)
        self.assertEqual(len(client.query_calls), 4)
        self.assertEqual(len(client.sku_calls), 1)
        self.assertEqual(len(cache.saves), 1)
        self.assertEqual(cache.value["source_row_count"], 1)
        self.assertEqual(len(cache.value["rows"]), 1)

    def test_explicit_empty_discovery_is_a_successful_empty_snapshot(self) -> None:
        adapter, client = _adapter(
            AliExpressFranceAdapter,
            _sku_payload(_sku()),
            rows=[],
        )

        self.assertEqual(adapter.fetch_products(), [])
        self.assertEqual(client.sku_calls, [])

    def test_missing_pagination_metadata_is_not_treated_as_empty(self) -> None:
        payload = _query_payload([])
        del payload["result"]["current_record_count"]
        cache = _Cache()
        adapter, client = _adapter(
            AliExpressFranceAdapter,
            _sku_payload(_sku()),
            cache=cache,
            query_payloads=[payload],
        )

        with self.assertRaisesRegex(RuntimeError, "omitted pagination metadata"):
            adapter.fetch_products()

        self.assertEqual(len(client.query_calls), 1)
        self.assertEqual(client.sku_calls, [])
        self.assertEqual(cache.saves, [])

    def test_missing_total_page_number_is_a_diagnostic_window_only(self) -> None:
        first = _query_payload([_query_row()])
        empty = _query_payload([])
        del first["result"]["total_page_no"]
        del empty["result"]["total_page_no"]
        adapter, client = _adapter(
            AliExpressFranceAdapter,
            _sku_payload(_sku()),
            query_payloads=[first, empty, empty, empty],
        )

        offers = adapter.inspect_offers()

        self.assertEqual(len(offers), 1)
        self.assertEqual(len(client.query_calls), 4)
        self.assertEqual(len(client.sku_calls), 1)

    def test_missing_total_page_number_cannot_activate_verified_stock(self) -> None:
        payload = _query_payload([_query_row()])
        del payload["result"]["total_page_no"]
        cache = _Cache()
        adapter, client = _adapter(
            AliExpressFranceAdapter,
            _sku_payload(_sku(stock_key="available", stock_value=True)),
            cache=cache,
            query_payloads=[payload],
            verified_stock_field="available",
        )

        with self.assertRaisesRegex(
            AliExpressAvailabilityUnknown, "production stock discovery is incomplete"
        ):
            adapter.fetch_products()

        self.assertEqual(client.sku_calls, [])
        self.assertEqual(cache.saves, [])

    def test_incomplete_diagnostic_cache_cannot_activate_or_fallback_on_limit(self) -> None:
        first = _query_payload([_query_row()])
        empty = _query_payload([])
        del first["result"]["total_page_no"]
        del empty["result"]["total_page_no"]
        cache = _Cache()
        diagnostic, diagnostic_client = _adapter(
            AliExpressFranceAdapter,
            _sku_payload(_sku()),
            cache=cache,
            query_payloads=[first, empty, empty, empty],
        )

        self.assertEqual(len(diagnostic.inspect_offers()), 1)
        self.assertEqual(len(diagnostic_client.query_calls), 4)
        self.assertEqual(cache.value["version"], 3)
        self.assertFalse(cache.value["discovery_complete"])

        limited = AliExpressApiError(
            "aliexpress.affiliate.product.query",
            code="15",
            sub_code="ApiCallLimit",
        )
        enabled, enabled_client = _adapter(
            AliExpressFranceAdapter,
            _sku_payload(_sku(stock_key="available", stock_value=True)),
            cache=cache,
            query_payloads=[limited],
            verified_stock_field="available",
        )

        with self.assertRaises(AliExpressApiError) as raised:
            enabled.fetch_products()

        self.assertEqual(raised.exception.sub_code, "ApiCallLimit")
        self.assertEqual(len(enabled_client.query_calls), 1)
        self.assertEqual(enabled_client.sku_calls, [])

    def test_legacy_cache_is_rebuilt_before_verified_stock_is_used(self) -> None:
        cache = _Cache(
            {
                "version": 2,
                "last_imported": NOW.isoformat(),
                "rows": [
                    {
                        "product_id": PRODUCT_ID,
                        "title": "Climatiseur mobile Midea 12000 BTU",
                        "detail_url": CANONICAL_URL,
                        "promotion_link": PROMOTION_URL,
                    }
                ],
                "source_row_count": 1,
            }
        )
        enabled, client = _adapter(
            AliExpressFranceAdapter,
            _sku_payload(_sku(stock_key="available", stock_value=True)),
            cache=cache,
            verified_stock_field="available",
        )

        products = enabled.fetch_products()

        self.assertEqual(len(products), 1)
        self.assertTrue(products[0].available)
        self.assertEqual(len(client.query_calls), 4)
        self.assertEqual(len(client.sku_calls), 1)
        self.assertEqual(cache.value["version"], 3)
        self.assertTrue(cache.value["discovery_complete"])

    def test_inconsistent_current_record_count_fails_closed(self) -> None:
        payload = _query_payload([_query_row()])
        payload["result"]["current_record_count"] = 2
        cache = _Cache()
        adapter, client = _adapter(
            AliExpressFranceAdapter,
            _sku_payload(_sku()),
            cache=cache,
            query_payloads=[payload],
        )

        with self.assertRaisesRegex(RuntimeError, "inconsistent page count"):
            adapter.fetch_products()

        self.assertEqual(len(client.query_calls), 1)
        self.assertEqual(client.sku_calls, [])
        self.assertEqual(cache.saves, [])

    def test_positive_total_with_an_empty_only_page_fails_closed(self) -> None:
        payload = _page_payload([], page=1, total_pages=1, total_records=1)
        adapter, client = _adapter(
            AliExpressFranceAdapter,
            _sku_payload(_sku()),
            query_payloads=[payload],
        )

        with self.assertRaisesRegex(RuntimeError, "pagination ended unexpectedly"):
            adapter.fetch_products()

        self.assertEqual(len(client.query_calls), 1)
        self.assertEqual(client.sku_calls, [])

    def test_empty_last_page_and_cumulative_shortfall_fail_closed(self) -> None:
        pages = [
            _page_payload([_query_row()], page=1, total_pages=2, total_records=2),
            _page_payload([], page=2, total_pages=2, total_records=2),
        ]
        adapter, client = _adapter(
            AliExpressFranceAdapter,
            _sku_payload(_sku()),
            query_payloads=pages,
        )

        with self.assertRaisesRegex(RuntimeError, "pagination ended unexpectedly"):
            adapter.fetch_products()

        self.assertEqual(len(client.query_calls), 2)
        self.assertEqual(client.sku_calls, [])

    def test_pagination_total_drift_and_final_count_mismatch_fail_closed(self) -> None:
        second_id = "1005009000000002"
        drift = [
            _page_payload([_query_row()], page=1, total_pages=2, total_records=2),
            _page_payload(
                [_query_row(second_id)], page=2, total_pages=2, total_records=3
            ),
        ]
        drift_adapter, _client = _adapter(
            AliExpressFranceAdapter,
            _sku_payload(_sku()),
            query_payloads=drift,
        )
        with self.assertRaisesRegex(
            AliExpressAvailabilityUnknown, "metadata changed"
        ):
            drift_adapter.fetch_products()

        short = [
            _page_payload([_query_row()], page=1, total_pages=2, total_records=3),
            _page_payload(
                [_query_row(second_id)], page=2, total_pages=2, total_records=3
            ),
        ]
        short_adapter, _client = _adapter(
            AliExpressFranceAdapter,
            _sku_payload(_sku()),
            query_payloads=short,
        )
        with self.assertRaisesRegex(
            AliExpressAvailabilityUnknown, "incomplete product count"
        ):
            short_adapter.fetch_products()

    def test_more_than_four_pages_does_not_save_partial_discovery(self) -> None:
        pages = [
            _page_payload(
                [_query_row(f"100500900000000{page}")],
                page=page,
                total_pages=5,
                total_records=5,
            )
            for page in range(1, 5)
        ]
        cache = _Cache()
        adapter, client = _adapter(
            AliExpressFranceAdapter,
            _sku_payload(_sku()),
            cache=cache,
            query_payloads=pages,
        )

        with self.assertRaisesRegex(
            AliExpressAvailabilityUnknown, "pagination exceeded"
        ):
            adapter.fetch_products()

        self.assertEqual(
            [call["page_no"] for call in client.query_calls],
            [1, 2, 3, 4],
        )
        self.assertEqual(client.sku_calls, [])
        self.assertEqual(cache.saves, [])

    def test_cache_save_failure_keeps_complete_in_memory_result(self) -> None:
        cache = _FailingSaveCache()
        adapter, client = _adapter(
            AliExpressFranceAdapter,
            _sku_payload(_sku(stock_key="available", stock_value=True)),
            cache=cache,
            verified_stock_field="available",
        )

        products = adapter.fetch_products()

        self.assertEqual(len(products), 1)
        self.assertTrue(products[0].available)
        self.assertEqual(len(client.sku_calls), 1)
        self.assertEqual(len(cache.saves), 1)

    def test_missing_credentials_fail_on_fetch_not_construction(self) -> None:
        with mock.patch.dict(
            "os.environ",
            {"ALIEXPRESS_APP_KEY": "", "ALIEXPRESS_APP_SECRET": ""},
        ):
            adapter = AliExpressFranceAdapter(
                _Fetcher(),
                cache=_Cache(),
                now=lambda: NOW,
            )

            with self.assertRaisesRegex(RuntimeError, "credentials are not configured"):
                adapter.fetch_products()

    def test_budget_exhaustion_stops_before_the_next_api_call(self) -> None:
        clock_values = iter((0.0, 0.0, 70.0))
        cache = _Cache()
        adapter, client = _adapter(
            AliExpressFranceAdapter,
            _sku_payload(_sku()),
            cache=cache,
            monotonic=lambda: next(clock_values),
        )

        with self.assertRaisesRegex(
            AliExpressAvailabilityUnknown, "exhausted its per-country API time budget"
        ):
            adapter.fetch_products()

        self.assertEqual(len(client.query_calls), 1)
        self.assertEqual(client.sku_calls, [])
        self.assertEqual(cache.saves, [])


if __name__ == "__main__":
    unittest.main()
