from __future__ import annotations

import json
import unittest

from bs4 import BeautifulSoup

from airco_tracker.adapters.bol import BolAdapter
from airco_tracker.adapters.coolblue import CoolblueAdapter
from airco_tracker.adapters.electroworld import ElectroWorldAdapter
from airco_tracker.adapters.ep import EpAdapter
from airco_tracker.adapters.mediamarkt import MediaMarktAdapter
from airco_tracker.adapters.wehkamp import WehkampAdapter
from airco_tracker.adapters.base import parse_btu, parse_price


class DummyResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class DummySession:
    def __init__(self, search_payload):
        self.search_payload = search_payload
        self.token_calls = []
        self.search_calls = []

    def post(self, url, **kwargs):
        self.token_calls.append((url, kwargs))
        return DummyResponse({"access_token": "token"})

    def get(self, url, **kwargs):
        self.search_calls.append((url, kwargs))
        return DummyResponse(self.search_payload)


class DummyFetcher:
    def __init__(self, search_payload=None):
        self.timeout = 25
        self.session = DummySession(search_payload or {})


class CatalogSession:
    def __init__(self, payload):
        self.payload = payload
        self.post_calls = []

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        return DummyResponse(self.payload)


class CatalogFetcher:
    def __init__(self, page, payload):
        self.timeout = 25
        self.page = page
        self.session = CatalogSession(payload)

    def get(self, url):
        return self.page


class ParserTests(unittest.TestCase):
    def test_dutch_price_and_btu_formats(self) -> None:
        self.assertEqual(parse_price("504 ,- Tijdelijk uitverkocht"), 504.0)
        self.assertEqual(parse_price("De prijs is '499' euro en '99' cent"), 499.99)
        self.assertEqual(parse_btu("14K BTU/h"), 14000)
        self.assertEqual(parse_btu("14.000 BTU/h"), 14000)

    def test_coolblue_out_of_stock_and_available(self) -> None:
        html = """
        <main>
          <article><a href="/product/1/test.html"><img alt="Test 9000 BTU"></a>
            <p>€ 399,00</p><p>Tijdelijk uitverkocht</p></article>
          <article><a href="/product/2/good.html">Good 12000 BTU</a>
            <p>€ 499,00</p><p>Morgen bezorgd</p></article>
        </main>"""
        products = CoolblueAdapter(DummyFetcher()).parse(BeautifulSoup(html, "html.parser"), "https://www.coolblue.nl/mobiele-aircos")
        self.assertEqual([p.available for p in products], [False, True])
        self.assertEqual(products[1].price_eur, 499.0)

    def test_mediamarkt_requires_online_stock(self) -> None:
        html = """
        <article><a href="/nl/product/_one-123.html">One 7000 BTU</a><span>€ 247,00</span>
        <span>Online op voorraad</span><button>Ik wil bestellen</button></article>
        <article><a href="/nl/product/_two-456.html">Two 9000 BTU</a><span>€ 350,00</span>
        <span>Helaas geen bezorging mogelijk</span></article>"""
        products = MediaMarktAdapter(DummyFetcher()).parse(BeautifulSoup(html, "html.parser"), "https://www.mediamarkt.nl/")
        self.assertEqual([p.available for p in products], [True, False])

    def test_ep_uses_green_online_stock_marker(self) -> None:
        html = """
        <div class="lister-card">
          <a class="lister-card__title" href="/products/one/1/">One 9000 BTU</a>
          <div class="prijs"><span>349,95</span></div>
          <p class="stock is-green"><span title="Morgen in huis">Morgen in huis</span></p>
        </div>
        <div class="lister-card">
          <a class="lister-card__title" href="/products/two/2/">Two 12000 BTU</a>
          <div class="prijs"><span>499,-</span></div>
          <p class="stock is-black"><span title="Tijdelijk uitverkocht">Tijdelijk uitverkocht</span></p>
        </div>"""
        products = EpAdapter(DummyFetcher()).parse(
            BeautifulSoup(html, "html.parser"),
            "https://www.ep.nl/producten/categorie-mobiele-airco/",
        )
        self.assertEqual([product.available for product in products], [True, False])
        self.assertEqual(products[0].price_eur, 349.95)
        self.assertEqual(products[0].btu, 9000)
        self.assertEqual(products[0].delivery, "Morgen in huis")

    def test_electroworld_reads_public_category_search(self) -> None:
        config = {
            "applicationId": "APP123",
            "apiKey": "public-search-key",
            "baseIndexName": "prd_electro_world",
            "request": {"path": "Home /// Airco's /// Mobiele airco's", "level": 2},
        }
        encoded = json.dumps(json.dumps(config, separators=(",", ":")))[1:-1]
        page = f"<script>window.algoliaConfig = JSON.parse('{encoded}')</script>"
        payload = {
            "results": [
                {
                    "hits": [
                        {
                            "name": "Inventum AC901 9000 BTU",
                            "url": "https://www.electroworld.nl/inventum-ac901",
                            "in_stock_frontend": True,
                            "price": {"EUR": {"default": 301}},
                            "product_usps": ["Koelvermogen: 9000 BTU"],
                        },
                        {
                            "name": "DeLonghi PAC 12000 BTU",
                            "url": "https://www.electroworld.nl/delonghi-pac",
                            "in_stock_frontend": False,
                            "price": {"EUR": {"default": 799}},
                        },
                    ]
                }
            ]
        }
        fetcher = CatalogFetcher(page, payload)
        products = ElectroWorldAdapter(fetcher).fetch_products()
        self.assertEqual([product.available for product in products], [True, False])
        self.assertEqual(products[0].price_eur, 301.0)
        self.assertEqual(products[0].btu, 9000)
        request = fetcher.session.post_calls[0]
        self.assertEqual(request[1]["json"]["requests"][0]["indexName"], "prd_electro_world_products")
        self.assertIn("categories.level2", request[1]["json"]["requests"][0]["params"])

    def test_wehkamp_reads_only_primary_portable_aircos(self) -> None:
        data = {
            "products": [
                {
                    "originalTitle": "Inventum mobiele airco 9000 BTU",
                    "pdpUrl": "/inventum-airco-123/",
                    "availabilityText": "morgen in huis",
                    "itemsInStock": 0,
                    "pricing": {"price": 30999},
                },
                {
                    "originalTitle": "Mini aircooler 2000 BTU",
                    "pdpUrl": "/mini-aircooler-456/",
                    "availabilityText": "morgen in huis",
                    "itemsInStock": 2,
                    "pricing": {"price": 4999},
                },
            ],
            "total": 2,
            "optional": None,
        }
        raw = json.dumps(data, separators=(",", ":")).replace("null", "undefined")
        html = f"<script>window.__INITIAL_DATA__={raw};</script>"
        products = WehkampAdapter(DummyFetcher()).parse(
            BeautifulSoup(html, "html.parser"),
            "https://www.wehkamp.nl/huishoudelijke-apparatuur-aircos/",
        )
        self.assertEqual(len(products), 1)
        self.assertTrue(products[0].available)
        self.assertEqual(products[0].price_eur, 309.99)
        self.assertEqual(products[0].btu, 9000)

    def test_wehkamp_explicit_empty_category_is_valid(self) -> None:
        html = '<script>window.__INITIAL_DATA__={"products":[],"total":0};</script>'
        products = WehkampAdapter(DummyFetcher()).parse(
            BeautifulSoup(html, "html.parser"),
            "https://www.wehkamp.nl/huishoudelijke-apparatuur-aircos/",
        )
        self.assertEqual(products, [])

    def test_bol_marketing_api_excludes_aircooler_and_reads_offer(self) -> None:
        fetcher = DummyFetcher({
            "totalPages": 1,
            "results": [
                {
                    "title": "Mini Aircooler Mobiele Airco",
                    "url": "https://www.bol.com/nl/nl/p/9300000000001/",
                    "offer": {"price": 49.95, "deliveryDescription": "Op voorraad"},
                },
                {
                    "title": "Echte Mobiele Airco 7000 BTU",
                    "description": "Werkt met afvoerslang naar buiten",
                    "url": "https://www.bol.com/nl/nl/p/9300000000002/",
                    "offer": {"price": 299.0, "deliveryDescription": "Morgen in huis"},
                },
                {
                    "title": "Tweede Mobiele Airco 9000 BTU",
                    "bolProductId": 9300000000003,
                },
            ],
        })
        products = BolAdapter(fetcher, "client", "secret").fetch_products()
        self.assertEqual(len(products), 2)
        self.assertTrue(products[0].available)
        self.assertEqual(products[0].btu, 7000)
        self.assertEqual(products[0].price_eur, 299.0)
        self.assertEqual(products[0].delivery, "Morgen in huis")
        self.assertFalse(products[1].available)
        self.assertEqual(products[1].url, "https://www.bol.com/nl/nl/p/9300000000003/")
        self.assertEqual(fetcher.session.token_calls[0][1]["auth"], ("client", "secret"))
        self.assertEqual(fetcher.session.search_calls[0][1]["params"]["country-code"], "NL")


if __name__ == "__main__":
    unittest.main()
