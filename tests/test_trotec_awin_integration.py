from __future__ import annotations

import os
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

from airco_tracker.adapters.fr.trotec import (
    TrotecFranceAdapter,
    _build_awin_client,
    _parse_hit,
)


CANONICAL = "https://fr.trotec.com/shop/pac-3910-x-wifi.html"


def _awin_link(destination=CANONICAL):
    return "https://www.awin1.com/cread.php?" + urlencode(
        {
            "awinmid": "62319",
            "awinaffid": "2981827",
            "ued": destination,
            "cons": "0",
        }
    )


def _hit(**overrides):
    hit = {
        "name": "Appareil de climatisation local PAC 3910 X WiFi",
        "url": CANONICAL,
        "sku": "1210002006",
        "availability_status": "En stock",
        "sold_out": "Non",
        "price": {"EUR": {"default": 699.99}},
        "main_characteristic_3_value": "14000 Btu/h",
        "categories_without_path": ["Climatiseur mobile", "Climatiseur"],
    }
    hit.update(overrides)
    return hit


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _Session:
    def __init__(self, payload):
        self.payload = payload
        self.post_calls = []

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        return _Response(self.payload)


class _Fetcher:
    timeout = 25

    def __init__(self, hits):
        self.session = _Session({"results": [{"hits": hits}]})

    def get(self, _url):
        return (
            'window.algoliaConfig = {"applicationId":"APP123",'
            '"apiKey":"public-key","baseIndexName":"fr"};'
        )


class _LinkClient:
    def __init__(self, links=None, error=None):
        self._links = links or {}
        self._error = error
        self.calls = []

    def links_for(self, destinations):
        self.calls.append(tuple(destinations))
        if self._error is not None:
            raise self._error
        return self._links


class TrotecAwinIntegrationTests(unittest.TestCase):
    def test_api_generated_link_is_attached_without_changing_stock_truth(self):
        client = _LinkClient({CANONICAL: _awin_link()})

        products = TrotecFranceAdapter(
            _Fetcher([_hit()]), awin_client=client
        ).fetch_products()

        self.assertEqual(len(products), 1)
        product = products[0]
        self.assertTrue(product.available)
        self.assertFalse(product.presale)
        self.assertEqual(product.url, CANONICAL)
        self.assertEqual(product.affiliate_url, _awin_link())
        query = parse_qs(urlsplit(product.affiliate_url).query)
        self.assertEqual(query["awinmid"], ["62319"])
        self.assertEqual(query["awinaffid"], ["2981827"])
        self.assertEqual(query["cons"], ["0"])
        self.assertEqual(query["ued"], [CANONICAL])
        self.assertEqual(client.calls, [(CANONICAL,)])

    def test_api_is_called_once_for_all_first_party_products(self):
        second = CANONICAL + "?variant=2"
        client = _LinkClient({CANONICAL: _awin_link()})
        fetcher = _Fetcher([_hit(), _hit(url=second, sku="1210002007")])

        products = TrotecFranceAdapter(fetcher, awin_client=client).fetch_products()

        self.assertEqual(len(products), 2)
        self.assertEqual(client.calls, [(CANONICAL, second)])
        self.assertEqual(len(fetcher.session.post_calls), 1)
        linked = {product.url: product.affiliate_url for product in products}
        self.assertEqual(linked[CANONICAL], _awin_link())
        self.assertIsNone(linked[second])

    def test_api_failure_keeps_live_stock_and_canonical_purchase_url(self):
        client = _LinkClient(error=RuntimeError("link builder unavailable"))
        fetcher = _Fetcher([_hit()])

        with self.assertLogs("airco_tracker.adapters.fr.trotec", level="WARNING"):
            products = TrotecFranceAdapter(fetcher, awin_client=client).fetch_products()

        self.assertEqual(len(products), 1)
        self.assertTrue(products[0].available)
        self.assertEqual(products[0].url, CANONICAL)
        self.assertIsNone(products[0].affiliate_url)
        self.assertEqual(products[0].purchase_url, CANONICAL)

    def test_parser_alone_never_locally_fabricates_an_awin_link(self):
        product = _parse_hit(_hit())
        self.assertIsNotNone(product)
        self.assertIsNone(product.affiliate_url)
        self.assertEqual(product.purchase_url, CANONICAL)

    @patch("airco_tracker.adapters.fr.trotec.build_partner_feed_cache")
    def test_legacy_secret_url_is_not_supported(self, cache_builder):
        fetcher = _Fetcher([])
        environment = {
            "AWIN_TROTEC_FEED_URL": "https://example.test/secret-feed",
            "AWIN_PUBLISHER_API_TOKEN": "",
        }

        with patch.dict(os.environ, environment, clear=False):
            result = _build_awin_client(fetcher)

        self.assertIsNone(result)
        cache_builder.assert_not_called()

    @patch("airco_tracker.adapters.fr.trotec.AwinLinkBuilderClient")
    @patch("airco_tracker.adapters.fr.trotec.build_partner_feed_cache")
    def test_token_uses_approved_trotec_program_coordinates(
        self, cache_builder, client_class
    ):
        fetcher = _Fetcher([])
        cache_builder.return_value = object()
        client_class.return_value = object()

        with patch.dict(
            os.environ,
            {"AWIN_PUBLISHER_API_TOKEN": "token"},
            clear=False,
        ):
            result = _build_awin_client(fetcher)

        self.assertIs(result, client_class.return_value)
        kwargs = client_class.call_args.kwargs
        self.assertEqual(kwargs["publisher_id"], "2981827")
        self.assertEqual(kwargs["advertiser_id"], "62319")
        self.assertEqual(kwargs["bearer_token"], "token")
        self.assertEqual(kwargs["timeout"], 10)
        self.assertIsNot(kwargs["session"], fetcher.session)

    def test_unknown_sold_out_signal_fails_closed_for_every_relevant_product(self):
        for value in (None, "", "unknown", object()):
            with self.subTest(value=value):
                with self.assertRaisesRegex(RuntimeError, "sold_out"):
                    _parse_hit(_hit(sold_out=value))
                with self.assertRaisesRegex(RuntimeError, "sold_out"):
                    _parse_hit(
                        _hit(
                            availability_status="Actuellement indisponible",
                            sold_out=value,
                        )
                    )

    def test_unknown_availability_status_fails_closed(self):
        for value in (None, "", "Disponible sous peu", "schema-drift"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(RuntimeError, "availability status"):
                    _parse_hit(_hit(availability_status=value))

    def test_boolean_sold_out_signal_is_respected(self):
        sold_out = _parse_hit(_hit(sold_out=True))
        orderable = _parse_hit(_hit(sold_out=False))
        self.assertFalse(sold_out.available)
        self.assertTrue(orderable.available)

    def test_non_trotec_product_url_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "invalid URL"):
            _parse_hit(_hit(url="https://evil.example/phish"))


if __name__ == "__main__":
    unittest.main()
