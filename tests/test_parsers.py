from __future__ import annotations

import gzip
import json
import unittest

from bs4 import BeautifulSoup

from airco_tracker.adapters.action import ActionAdapter, _parse_product_page as parse_action_page
from airco_tracker.adapters.alternate import _parse_product_page as parse_alternate_page
from airco_tracker.adapters.bol import BolAdapter
from airco_tracker.adapters.coolblue import CoolblueAdapter
from airco_tracker.adapters.diy import GammaAdapter, KarweiAdapter
from airco_tracker.adapters.electroworld import ElectroWorldAdapter
from airco_tracker.adapters.ep import EpAdapter
from airco_tracker.adapters.flinq import _parse_product_page as parse_flinq_page
from airco_tracker.adapters.klarstein import KlarsteinAdapter
from airco_tracker.adapters.lidl import LidlAdapter
from airco_tracker.adapters.mediamarkt import MediaMarktAdapter
from airco_tracker.adapters.praxis import PraxisAdapter
from airco_tracker.adapters.trotec import TrotecAdapter
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


class BinaryResponse:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        return None


class SitemapSession:
    def __init__(self, content):
        self.content = content

    def get(self, url, **kwargs):
        return BinaryResponse(self.content)


class SitemapFetcher:
    def __init__(self, sitemap, pages):
        self.timeout = 25
        self.session = SitemapSession(sitemap)
        self.pages = pages

    def get(self, url):
        return self.pages[url]


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

    def test_lidl_uses_sitemap_and_product_json_ld(self) -> None:
        product_url = "https://www.lidl.nl/p/test-mobiele-airco-9000-btu/p1001"
        sitemap = gzip.compress(
            f"""<?xml version="1.0" encoding="UTF-8"?>
            <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
              <url><loc>{product_url}</loc></url>
              <url><loc>https://www.lidl.nl/p/test-mobiele-aircooler/p1002</loc></url>
            </urlset>""".encode()
        )
        product_data = {
            "@type": "Product",
            "name": "Mobiele airco 9000 BTU",
            "brand": {"name": "TRONIC"},
            "offers": [
                {
                    "price": 249.99,
                    "availability": "https://schema.org/InStock",
                    "url": product_url,
                }
            ],
        }
        page = f'<script type="application/ld+json">{json.dumps(product_data)}</script>'
        products = LidlAdapter(SitemapFetcher(sitemap, {product_url: page})).fetch_products()
        self.assertEqual(len(products), 1)
        self.assertTrue(products[0].available)
        self.assertEqual(products[0].price_eur, 249.99)
        self.assertEqual(products[0].btu, 9000)
        self.assertEqual(products[0].name, "TRONIC Mobiele airco 9000 BTU")

    def test_gamma_and_karwei_require_online_availability(self) -> None:
        html = """
        <article class="js-product-tile" data-state="ONLINE_AVAILABLE">
          <a class="click-mask" href="/assortiment/one/p/B1"
             title="Handson mobiele airco 9000 BTU"></a>
          <meta itemprop="price" content="299.00">
          <span>9000 BTU</span>
        </article>
        <article class="js-product-tile" data-state="HAS_STORE_STOCK">
          <a class="click-mask" href="/assortiment/two/p/B2"
             title="Qlima mobiele airconditioner 12000 BTU"></a>
          <meta itemprop="price" content="499.00">
        </article>
        <article class="js-product-tile" data-state="ONLINE_AVAILABLE">
          <a class="click-mask" href="/assortiment/accessory/p/B3"
             title="Raamafdichting voor mobiele airco"></a>
          <meta itemprop="price" content="29.00">
        </article>"""
        soup = BeautifulSoup(html, "html.parser")
        gamma = GammaAdapter(DummyFetcher()).parse(soup, "https://www.gamma.nl/")
        karwei = KarweiAdapter(DummyFetcher()).parse(soup, "https://www.karwei.nl/")
        for products in (gamma, karwei):
            self.assertEqual(len(products), 2)
            self.assertEqual([product.available for product in products], [True, False])
            self.assertEqual(products[0].price_eur, 299.0)
            self.assertEqual(products[0].btu, 9000)

    def test_praxis_requires_current_home_delivery(self) -> None:
        state = {
            "translations": {"html": "<b>test</b>"},
            "products": {
                "quantity": 3,
                "collection": [
                    {
                        "title": "Sencys Mobiele airco 9000 BTU",
                        "link": "/mobiele-airco/1",
                        "regular": {"price": 319},
                        "deliveryModes": [{"code": "SHDPOSTNLPRAXIS"}],
                        "availabilityStatus": "Thuisbezorgd",
                        "availabilityStatusMultiple": ["Online op voorraad"],
                        "disableStatus": {"isDisabled": False},
                    },
                    {
                        "title": "Sencys Mobiele airco 12000 BTU",
                        "link": "/mobiele-airco/2",
                        "regular": {"price": 449},
                        "deliveryModes": [{"code": "PICKUP"}],
                        "availabilityStatus": "Bestel & Haal op",
                        "availabilityStatusMultiple": ["Bezorging niet beschikbaar"],
                        "disableStatus": {"isDisabled": False},
                    },
                    {
                        "title": "Qlima mini-split airconditioning",
                        "link": "/split/3",
                        "regular": {"price": 699},
                        "deliveryModes": [{"code": "SHDPOSTNLPRAXIS"}],
                        "availabilityStatus": "Thuisbezorgd",
                    },
                ],
            },
        }
        raw = json.dumps(state, separators=(",", ":")).replace("<", r"\x3c")
        html = f'<script>window["__PRELOADED_STATE_listerFragment__"] = {raw};</script>'
        products = PraxisAdapter(DummyFetcher()).parse(
            BeautifulSoup(html, "html.parser"),
            "https://www.praxis.nl/verwarmingen-airco-s/airco-s/mobiele-airco-s/he057/",
        )
        self.assertEqual(len(products), 2)
        self.assertEqual([product.available for product in products], [True, False])
        self.assertEqual(products[0].price_eur, 319.0)
        self.assertEqual(products[0].btu, 9000)

    def test_alternate_reads_schema_product_stock(self) -> None:
        product = {
            "@type": "Product",
            "name": "Bestron AAC9000 Mobiele Airconditioner 9000 BTU",
            "description": "Mobiele airco met afvoerslang",
            "offers": {
                "price": "279.00",
                "availability": "https://schema.org/InStock",
                "url": "https://www.alternate.nl/Bestron/AAC9000/html/product/1",
            },
        }
        page = f'<script type="application/ld+json">{json.dumps(product)}</script>'
        result = parse_alternate_page(page, "https://www.alternate.nl/product/1")
        self.assertTrue(result.available)
        self.assertEqual(result.price_eur, 279.0)
        self.assertEqual(result.btu, 9000)

    def test_trotec_only_counts_immediate_stock(self) -> None:
        def card(name, url, availability, price, btu):
            data = {
                "name": name,
                "availability_message": availability,
                "price_range": {"minimum_price": {"final_price": {"value": price}}},
            }
            return (
                f"<div x-data='{json.dumps({'product': data})}'>"
                f'<a class="product-item-link" href="{url}">{name}</a><span>{btu} BTU</span></div>'
            ).replace('{"product":', '{ product:')

        html = "".join(
            [
                card("Lokale airconditioner PAC 2010", "/shop/pac-2010.html", "Op voorraad", 349.99, 7000),
                card("Mobiele split-airconditioner PAC-S", "/shop/pac-s.html", "Levertijd: 3-4 weken", 999, 12000),
                card("Wandairconditioner PAC-W", "/shop/pac-w.html", "Op voorraad", 799, 9000),
            ]
        )
        products = TrotecAdapter(DummyFetcher()).parse(
            BeautifulSoup(html, "html.parser"), "https://nl.trotec.com/shop/mobiele-airco"
        )
        self.assertEqual(len(products), 2)
        self.assertEqual([product.available for product in products], [True, False])
        self.assertEqual(products[0].price_eur, 349.99)
        self.assertEqual(products[0].btu, 7000)

    def test_klarstein_uses_server_rendered_stock_attribute(self) -> None:
        html = """
        <form class="productTeaser" data-stock="in-stock">
          <a class="card-product__content-title" href="/Airconditioning/Airco/Mobiele-airco/one.html">
            Grandbreeze Smart 14000 BTU Mobiele airconditioner Zwart</a>
          <span class="card-product__content-label">Direct leverbaar</span>
          <span>849,99 €</span>
        </form>
        <form class="productTeaser" data-stock="out-of-stock">
          <a class="card-product__content-title" href="/Airconditioning/Airco/Mobiele-airco/two.html">
            Kraftwerk Smart 12000 BTU Mobiele airconditioner Wit</a>
          <span class="card-product__content-label">Niet beschikbaar</span>
          <span>729,99 €</span>
        </form>"""
        products = KlarsteinAdapter(DummyFetcher()).parse(
            BeautifulSoup(html, "html.parser"), "https://www.klarstein.nl/Airconditioning/Airco/Mobiele-airco/"
        )
        self.assertEqual([product.available for product in products], [True, False])
        self.assertEqual(products[0].price_eur, 849.99)
        self.assertEqual(products[0].btu, 14000)

    def test_flinq_reads_graph_product_and_current_price(self) -> None:
        data = {
            "@context": "https://schema.org",
            "@graph": [
                {
                    "@type": "Product",
                    "name": "FlinQ Slimme Mobiele Airco 15000 BTU",
                    "url": "https://www.flinqproducts.nl/product/flinq-airco/",
                    "offers": [{
                        "availability": "https://schema.org/OutOfStock",
                        "url": "https://www.flinqproducts.nl/product/flinq-airco/",
                        "priceSpecification": [
                            {"price": "599.99", "priceCurrency": "EUR"},
                            {"price": "749.99", "priceType": "https://schema.org/ListPrice"},
                        ],
                    }],
                }
            ],
        }
        page = f'<script type="application/ld+json">{json.dumps(data)}</script>'
        result = parse_flinq_page(page, "https://www.flinqproducts.nl/product/flinq-airco/")
        self.assertFalse(result.available)
        self.assertEqual(result.price_eur, 599.99)
        self.assertEqual(result.btu, 15000)

    def test_action_treats_expired_deal_as_unavailable(self) -> None:
        data = {
            "@type": "Product",
            "name": "Mobiele smart airco - wit",
            "description": "14.000 BTU compressor-airconditioner",
            "url": "https://shop.action.com/nl-nl/p/1/mobiele-smart-airco-wit",
            "offers": [{
                "price": 299,
                "availability": "https://schema.org/OutOfStock",
                "url": "https://shop.action.com/nl-nl/p/1/mobiele-smart-airco-wit",
            }],
        }
        page = (
            f'<script type="application/ld+json">{json.dumps(data)}</script>'
            "<main><h1>Mobiele smart airco</h1><p>Deal verlopen</p></main>"
        )
        result = parse_action_page(page, "https://shop.action.com/nl-nl/p/1/mobiele-smart-airco-wit")
        self.assertFalse(result.available)
        self.assertEqual(result.delivery, "Deal verlopen")
        self.assertEqual(result.price_eur, 299.0)
        self.assertEqual(result.btu, 14000)

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

    def test_wehkamp_long_lead_time_is_unavailable(self) -> None:
        data = {
            "products": [
                {
                    "originalTitle": "Inventum mobiele airco 9000 BTU",
                    "pdpUrl": "/inventum-airco-123/",
                    "availabilityText": "Binnen 3-5 weken leverbaar",
                    "itemsInStock": 0,
                    "pricing": {"price": 30999},
                },
            ],
            "total": 1,
        }
        raw = json.dumps(data, separators=(",", ":")).replace("null", "undefined")
        html = f"<script>window.__INITIAL_DATA__={raw};</script>"
        products = WehkampAdapter(DummyFetcher()).parse(
            BeautifulSoup(html, "html.parser"),
            "https://www.wehkamp.nl/huishoudelijke-apparatuur-aircos/",
        )
        self.assertEqual(len(products), 1)
        self.assertFalse(products[0].available)

    def test_wehkamp_keeps_monoblock_portable_airco(self) -> None:
        # "monoblock" (single-unit) is the genuine portable compressor form
        # factor and must NOT be excluded (only "split" is fixed-installation).
        data = {
            "products": [
                {
                    "originalTitle": "Qlima monoblock airconditioner 12000 BTU",
                    "pdpUrl": "/qlima-monoblock-1/",
                    "availabilityText": "morgen in huis",
                    "itemsInStock": 5,
                    "pricing": {"price": 49900},
                },
            ],
            "total": 1,
        }
        raw = json.dumps(data, separators=(",", ":")).replace("null", "undefined")
        html = f"<script>window.__INITIAL_DATA__={raw};</script>"
        products = WehkampAdapter(DummyFetcher()).parse(
            BeautifulSoup(html, "html.parser"),
            "https://www.wehkamp.nl/huishoudelijke-apparatuur-aircos/",
        )
        self.assertEqual(len(products), 1)
        self.assertTrue(products[0].available)
        self.assertEqual(products[0].btu, 12000)

    def test_lidl_parses_graph_wrapped_product(self) -> None:
        # Lidl now reuses schema.product_json_ld which supports @graph nesting.
        product_url = "https://www.lidl.nl/p/test-mobiele-airco-7000-btu/p2002"
        sitemap = gzip.compress(
            f"""<?xml version="1.0" encoding="UTF-8"?>
            <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
              <url><loc>{product_url}</loc></url>
            </urlset>""".encode()
        )
        product_data = {
            "@context": "https://schema.org",
            "@graph": [
                {
                    "@type": "Product",
                    "name": "Mobiele airco 7000 BTU",
                    "brand": {"name": "TRONIC"},
                    "offers": {
                        "price": 199.00,
                        "availability": "https://schema.org/InStock",
                        "url": product_url,
                    },
                }
            ],
        }
        page = f'<script type="application/ld+json">{json.dumps(product_data)}</script>'
        products = LidlAdapter(SitemapFetcher(sitemap, {product_url: page})).fetch_products()
        self.assertEqual(len(products), 1)
        self.assertTrue(products[0].available)
        self.assertEqual(products[0].price_eur, 199.0)
        self.assertEqual(products[0].btu, 7000)


if __name__ == "__main__":
    unittest.main()
