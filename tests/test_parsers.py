from __future__ import annotations

import gzip
import html as html_module
import json
import unittest

from bs4 import BeautifulSoup

from airco_tracker.adapters.action import ActionAdapter, _parse_product_page as parse_action_page
from airco_tracker.adapters.aircovoorinhuis import AircoVoorInHuisAdapter
from airco_tracker.adapters.alternate import _parse_product_page as parse_alternate_page
from airco_tracker.adapters.coolblue import CoolblueAdapter
from airco_tracker.adapters.costway import CostwayAdapter
from airco_tracker.adapters.create_store import CreateStoreAdapter, _parse_card as parse_create_card
from airco_tracker.adapters.delonghi import _parse_product_page as parse_delonghi_page
from airco_tracker.adapters.diy import GammaAdapter, KarweiAdapter
from airco_tracker.adapters.electroworld import ElectroWorldAdapter
from airco_tracker.adapters.ep import EpAdapter
from airco_tracker.adapters.evolarshop import EvolarshopAdapter, _parse_hit as parse_evolar_hit
from airco_tracker.adapters.expert import ExpertAdapter
from airco_tracker.adapters.flinq import _parse_product_page as parse_flinq_page
from airco_tracker.adapters.hubo import _parse_product_page as parse_hubo_page
from airco_tracker.adapters.klarstein import KlarsteinAdapter
from airco_tracker.adapters.kampeerwereld import _parse_product_page as parse_kampeerwereld_page
from airco_tracker.adapters.lidl import LidlAdapter
from airco_tracker.adapters.mediamarkt import MediaMarktAdapter
from airco_tracker.adapters.obelink import _parse_product_page as parse_obelink_page
from airco_tracker.adapters.praxis import PraxisAdapter
from airco_tracker.adapters.solago import _parse_product_page as parse_solago_page
from airco_tracker.adapters.trotec import TrotecAdapter
from airco_tracker.adapters.vrijbuiter import _parse_product_page as parse_vrijbuiter_page
from airco_tracker.adapters.wehkamp import WehkampAdapter
from airco_tracker.adapters.base import (
    enrich_available_btu,
    parse_btu,
    parse_cooling_watts_btu,
    parse_price,
    parse_product_page_btu,
)
from airco_tracker.models import Product


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
        self.assertEqual(parse_btu("10,500 BTU/u"), 10500)
        self.assertEqual(parse_btu("Koelcapaciteit (BTU/u) 10 000"), 10000)

    def test_explicit_cooling_watts_are_converted_but_input_power_is_not(self) -> None:
        self.assertEqual(parse_cooling_watts_btu("Koelcapaciteit 1495 W"), 5101)
        self.assertEqual(parse_cooling_watts_btu("Met 3,5 kW koelvermogen"), 11942)
        self.assertIsNone(parse_cooling_watts_btu("Stroomverbruik 1500 W"))

    def test_known_low_capacity_models_are_inferred(self) -> None:
        self.assertEqual(parse_btu("Obelink ArcticMove 1500 tentairco"), 5118)
        self.assertEqual(parse_btu("Qlima P 3020 Mobiele Airco"), 6824)
        self.assertEqual(parse_btu("COMFEE Mobiele airco 9000 Pro met APP"), 9000)
        self.assertEqual(parse_btu("COMFEE Mobiele aircoSmart Cool 12.000 Plus"), 12000)

    def test_product_page_parser_reads_labelled_specs(self) -> None:
        page = """
        <main><dl><dt>Maximaal koelvermogen (BTU)</dt><dd>9400 BTU</dd></dl></main>
        """
        self.assertEqual(parse_product_page_btu(page), 9400)

    def test_btu_enrichment_fetches_only_available_unknown_products(self) -> None:
        class DetailFetcher:
            def __init__(self) -> None:
                self.urls = []

            def get(self, url):
                self.urls.append(url)
                return "<main>Koelcapaciteit 2000 Watt</main>"

        fetcher = DetailFetcher()
        available = Product("Shop", "Airco", "https://shop.test/available", True)
        unavailable = Product("Shop", "Airco", "https://shop.test/unavailable", False)
        known = Product("Shop", "Airco", "https://shop.test/known", True, btu=9000)
        products = enrich_available_btu(fetcher, [available, unavailable, known])
        self.assertEqual(fetcher.urls, [available.url])
        self.assertEqual(products[0].btu, 6824)
        self.assertIsNone(products[1].btu)
        self.assertEqual(products[2].btu, 9000)

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

    def test_gamma_and_karwei_convert_qlima_title_watts(self) -> None:
        html = """
        <article class="js-product-tile" data-state="ONLINE_AVAILABLE">
          <a class="click-mask" href="/assortiment/qlima/p/B1"
             title="Qlima mobiele airconditioner P 3020 wit 2000W"></a>
          <meta itemprop="price" content="279.00">
        </article>"""
        soup = BeautifulSoup(html, "html.parser")
        for adapter in (GammaAdapter(DummyFetcher()), KarweiAdapter(DummyFetcher())):
            products = adapter.parse(soup, "https://shop.test/")
            self.assertEqual(products[0].btu, 6824)

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

    def test_praxis_converts_watt_rating_to_btu(self) -> None:
        # Praxis titles often state cooling capacity in watts (e.g. 3500W)
        # instead of BTU. The adapter should convert so MIN_BTU filtering works.
        state = {
            "translations": {"html": "<b>test</b>"},
            "products": {
                "quantity": 2,
                "collection": [
                    {
                        "title": "Sencys mobiele airconditioner MPPD-12 3500W",
                        "link": "/mobiele-airco/1",
                        "regular": {"price": 560},
                        "deliveryModes": [{"code": "SHDPOSTNLPRAXIS"}],
                        "availabilityStatus": "Thuisbezorgd",
                        "availabilityStatusMultiple": ["Online op voorraad"],
                        "disableStatus": {"isDisabled": False},
                    },
                    {
                        "title": "Sencys mobiele airco 7000 BTU",
                        "link": "/mobiele-airco/2",
                        "regular": {"price": 319},
                        "deliveryModes": [{"code": "SHDPOSTNLPRAXIS"}],
                        "availabilityStatus": "Thuisbezorgd",
                        "availabilityStatusMultiple": ["Online op voorraad"],
                        "disableStatus": {"isDisabled": False},
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
        # 3500 W * 3.412 ≈ 11942 BTU
        self.assertEqual(products[0].btu, 11942)
        # Explicit BTU in the title wins over the watt fallback.
        self.assertEqual(products[1].btu, 7000)

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

    def test_trotec_infers_verified_model_capacity_when_card_omits_btu(self) -> None:
        data = {
            "name": "Camping-airconditioner PAC-C 1500 SH WiFi",
            "availability_message": "Op voorraad",
            "price_range": {"minimum_price": {"final_price": {"value": 579.99}}},
        }
        html = (
            f"<div x-data='{json.dumps({'product': data})}'>"
            '<a class="product-item-link" href="/shop/pac-c-1500.html">Airco</a></div>'
        ).replace('{"product":', "{ product:")
        products = TrotecAdapter(DummyFetcher()).parse(
            BeautifulSoup(html, "html.parser"),
            "https://nl.trotec.com/shop/mobiele-airco",
        )
        self.assertEqual(products[0].btu, 5000)

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

    def test_expert_requires_online_saleability(self) -> None:
        payload = {
            "items": [
                {
                    "name": "Inventum mobiele airco 9000 BTU",
                    "url": "https://www.expert.nl/inventum-airco",
                    "final_price_incl_tax": 399,
                    "not_saleable": False,
                    "status_in_stock": 1,
                    "in_stock": 1,
                    "display_name": "Mobiele airco",
                },
                {
                    "name": "Eurom mobiele airco 12000 BTU",
                    "url": "https://www.expert.nl/eurom-airco",
                    "final_price_incl_tax": 499,
                    "not_saleable": True,
                    "status_in_stock": 1,
                    "in_stock": 1,
                    "display_name": "Mobiele airco",
                },
                {
                    "name": "Eurom Window-Way out",
                    "url": "https://www.expert.nl/window-kit",
                    "final_price_incl_tax": 49.95,
                    "not_saleable": False,
                    "status_in_stock": 1,
                    "in_stock": 1,
                    "description": "Raamkit voor mobiele airco",
                },
            ]
        }
        raw = html_module.escape(json.dumps(payload), quote=True)
        page = f'<catalog-category-view :catalog-data="{raw}"></catalog-category-view>'
        products = ExpertAdapter(CatalogFetcher(page, {})).fetch_products()
        self.assertEqual(len(products), 2)
        self.assertEqual([product.available for product in products], [True, False])
        self.assertEqual(products[0].btu, 9000)

    def test_delonghi_uses_schema_stock_and_notify_state(self) -> None:
        data = {
            "@type": "Product",
            "name": "Pinguino mobiele airconditioner 12000 BTU",
            "offers": {
                "price": 999.90,
                "availability": "https://schema.org/InStock",
                "url": "https://www.delonghi.com/nl-nl/p/pinguino.html",
            },
        }
        page = (
            f'<script type="application/ld+json">{json.dumps(data)}</script>'
            "<main><p>Breng mij op de hoogte</p></main>"
        )
        product = parse_delonghi_page(page, "https://www.delonghi.com/nl-nl/p/pinguino.html")
        self.assertFalse(product.available)
        self.assertEqual(product.price_eur, 999.9)
        self.assertEqual(product.btu, 12000)

    def test_obelink_reads_product_graph_offer(self) -> None:
        data = {
            "@context": "https://schema.org",
            "@graph": [
                {
                    "@type": "Product",
                    "name": "Inventum AC901 mobiele airco",
                    "description": "9000 BTU met afvoerslang",
                    "url": "https://www.obelink.nl/inventum-ac-901-mobiele-airco.html",
                    "offers": {
                        "price": 319,
                        "availability": "https://schema.org/InStock",
                    },
                }
            ],
        }
        page = f'<script type="application/ld+json">{json.dumps(data)}</script>'
        product = parse_obelink_page(page, "https://www.obelink.nl/inventum-ac-901-mobiele-airco.html")
        self.assertTrue(product.available)
        self.assertEqual(product.price_eur, 319.0)
        self.assertEqual(product.btu, 9000)

    def test_obelink_infers_arcticmove_capacity_on_second_chance_page(self) -> None:
        data = {
            "@type": "Product",
            "name": "Tweedekans Obelink ArcticMove 1500 tentairco",
            # The real second-chance page omits the 5100 BTU specification.
            "description": "Een tweede kans tentairco met drie ventilatorstanden.",
            "url": "https://www.obelink.nl/tweedekans-arcticmove-1500w.html",
            "offers": {
                "price": 319,
                "availability": "https://schema.org/InStock",
            },
        }
        page = f'<script type="application/ld+json">{json.dumps(data)}</script>'
        product = parse_obelink_page(
            page,
            "https://www.obelink.nl/tweedekans-arcticmove-1500w.html",
        )
        self.assertEqual(product.btu, 5118)

    def test_obelink_converts_explicit_cooling_capacity_watts(self) -> None:
        data = {
            "@type": "Product",
            "name": "Mestic SPA-5000 split airco",
            "description": "Met een koelcapaciteit van 1495 Watt.",
            "offers": {
                "price": 539,
                "availability": "https://schema.org/OutOfStock",
            },
        }
        page = f'<script type="application/ld+json">{json.dumps(data)}</script>'
        product = parse_obelink_page(page, "https://www.obelink.nl/mestic-spa-5000.html")
        self.assertEqual(product.btu, 5101)

    def test_obelink_reads_cooling_capacity_from_specification_table(self) -> None:
        data = {
            "@type": "Product",
            "name": "Eurom AC7000 split airco",
            "description": "Een split airco voor de caravan.",
            "offers": {
                "price": 599,
                "availability": "https://schema.org/OutOfStock",
            },
        }
        page = (
            f'<script type="application/ld+json">{json.dumps(data)}</script>'
            "<main><table><tr><th>Koelcapaciteit</th><td>2000 W</td></tr></table></main>"
        )
        product = parse_obelink_page(page, "https://www.obelink.nl/eurom-ac7000.html")
        self.assertEqual(product.btu, 6824)

    def test_kampeerwereld_rejects_store_only_stock(self) -> None:
        page = """
        <h1 class="product-detail-name">Eurom AC 7001 Mobiele Airco</h1>
        <div class="product-detail-price">€ 189,00</div>
        <div class="product-detail-stock-container"><span>Op voorraad</span></div>
        <div class="product-detail-description-text">Koelvermogen 7000 BTU</div>
        <p>Exclusief in winkel</p>
        """
        product = parse_kampeerwereld_page(page, "https://www.kampeerwereld.nl/airco/1")
        self.assertFalse(product.available)
        self.assertEqual(product.price_eur, 189.0)
        self.assertEqual(product.btu, 7000)

    def test_create_presale_is_not_available(self) -> None:
        page = """
        <div class="c-product-card">
          <span class="c-product-tag__label">Presale</span>
          <h2 class="c-product-card__title">
            <a href="/nl/product.html">SILKAIR Mobiele airco 9000 BTU</a>
          </h2>
          <div class="c-product-card__price--final">309,95</div>
          <span>Verzending vanaf 26/07/2026</span>
        </div>
        """
        card = BeautifulSoup(page, "html.parser").select_one(".c-product-card")
        product = parse_create_card(card, "https://www.create-store.com/nl/3939-kopen-mobiele-airco")
        self.assertIsNotNone(product)
        self.assertFalse(product.available)
        self.assertEqual(product.price_eur, 309.95)
        self.assertEqual(product.btu, 9000)

    def test_create_deduplicates_responsive_product_cards(self) -> None:
        page = """
        <div class="c-product-card">
          <h2 class="c-product-card__title"><a href="/nl/product.html">SILKAIR Mobiele airco 9000 BTU</a></h2>
          <div class="c-product-card__price--final">339,95</div>
          <span>Verzending binnen 48 uur</span>
        </div>
        <div class="c-product-card">
          <h2 class="c-product-card__title"><a href="/nl/product.html">SILKAIR Mobiele airco 9000 BTU</a></h2>
          <div class="c-product-card__price--final">309,95</div>
          <span>Verzending binnen 48 uur</span>
        </div>
        """
        products = CreateStoreAdapter(CatalogFetcher(page, {})).fetch_products()
        self.assertEqual(len(products), 1)
        self.assertEqual(products[0].price_eur, 309.95)

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

    # --- Costway NL ---

    def test_costway_uses_qty_class_for_stock(self) -> None:
        html = """
        <ul class="products-grid">
          <li class="item product">
            <div class="product-item-photo qty-10"></div>
            <a class="product-item-link" href="/mobiele-airconditioning-12000-btu.html">
              Mobiele Airconditioning 12000 BTU met Afvoerslang</a>
            <div class="price-box">€ 499,00</div>
          </li>
          <li class="item product">
            <div class="product-item-photo qty-0"></div>
            <a class="product-item-link" href="/split-airconditioner.html">
              Split-airconditioner 9000 BTU</a>
            <div class="price-box">UITVERKOCHT € 399,00</div>
          </li>
          <li class="item product">
            <div class="product-item-photo qty-5"></div>
            <a class="product-item-link" href="/luchtkoeler.html">
              Luchtkoeler aircooler</a>
            <div class="price-box">€ 99,00</div>
          </li>
        </ul>"""
        products = CostwayAdapter(DummyFetcher()).parse(
            BeautifulSoup(html, "html.parser"),
            "https://nl.costway.com/huishoudelijke-apparaten/klimaatbeheersing/aircos.html",
        )
        # Split-airconditioner is excluded (fixed-installation); luchtkoeler is excluded.
        self.assertEqual(len(products), 1)
        self.assertTrue(products[0].available)
        self.assertEqual(products[0].btu, 12000)
        self.assertEqual(products[0].price_eur, 499.0)

    # --- Evolarshop ---

    def test_evolarshop_excludes_hoseless_and_reads_nosto_hit(self) -> None:
        hit = {
            "productId": "1",
            "name": "TCL TAC 12CPB Mobiele Airco 12000 BTU",
            "url": "https://www.evolarshop.nl/tac-12cpb-mobiele-airco",
            "price": 779.0,
            "available": True,
            "availability": "InStock",
        }
        product = parse_evolar_hit(hit)
        self.assertIsNotNone(product)
        self.assertTrue(product.available)
        self.assertEqual(product.btu, 12000)
        self.assertEqual(product.price_eur, 779.0)

        # "Zonder afvoerslang" (no exhaust hose) is not a compressor unit.
        hoseless = {**hit, "name": "Evolar EVO-ES1800W Mobiele Airco Zonder afvoerslang"}
        self.assertIsNone(parse_evolar_hit(hoseless))

    def test_evolarshop_queries_public_nosto_api(self) -> None:
        page = '<script src="//connect.nosto.com/include/epk2p6xv"></script>'
        hits = [
            {
                "name": "Sinclair AMC 14P Mobiele Airco 14000 BTU",
                "url": "https://www.evolarshop.nl/sinclair-amc-14p",
                "price": 599.0,
                "available": True,
                "availability": "InStock",
            },
            {
                "name": "Evolar luchtkoeler",
                "url": "https://www.evolarshop.nl/luchtkoeler",
                "price": 199.0,
                "available": False,
                "availability": "OutOfStock",
            },
        ]

        class _NostoSession:
            def __init__(self, hits):
                self.hits = hits
                self.post_url = None

            def post(self, url, **kwargs):
                self.post_url = url
                return DummyResponse({"data": {"search": {"products": {"hits": self.hits}}}})

        class _NostoFetcher:
            timeout = 25

            def __init__(self, hits):
                self.session = _NostoSession(hits)

            def get(self, url):
                return page

        fetcher = _NostoFetcher(hits)
        products = EvolarshopAdapter(fetcher).fetch_products()
        self.assertEqual(len(products), 1)
        self.assertTrue(products[0].available)
        self.assertEqual(products[0].btu, 14000)
        self.assertIn("search.nosto.com", fetcher.session.post_url)

    # --- Airco voor in huis ---

    def test_aircovoorinhuis_uses_woocommerce_stock_class(self) -> None:
        html = """
        <ul class="products">
          <li class="product type-product instock product_cat-mobiele-airco-systemen">
            <a class="ct-media-container" aria-label="Climate King A011D1 Mobiele airco [2,9KW]"
               href="https://www.aircovoorinhuis.nl/climate-king-a011d1">
               <span class="woocommerce-Price-amount amount">€599,00</span></a>
          </li>
          <li class="product type-product outofstock product_cat-mobiele-airco-systemen">
            <a class="ct-media-container" aria-label="Climate King A011C2 Mobiele airco [2,6KW]"
               href="https://www.aircovoorinhuis.nl/climate-king-a011c2">
               <span class="woocommerce-Price-amount amount">€549,00</span></a>
          </li>
          <li class="product type-product instock product_cat-luchtkoelers">
            <a class="ct-media-container" aria-label="Luchtkoeler aircooler"
               href="https://www.aircovoorinhuis.nl/luchtkoeler">
               <span class="woocommerce-Price-amount amount">€99,00</span></a>
          </li>
        </ul>"""
        products = AircoVoorInHuisAdapter(DummyFetcher()).parse(
            BeautifulSoup(html, "html.parser"),
            "https://www.aircovoorinhuis.nl/airco/mobiele-airco/mobiele-airco-systemen/",
        )
        self.assertEqual(len(products), 2)
        self.assertTrue(products[0].available)
        self.assertFalse(products[1].available)
        self.assertEqual(products[0].price_eur, 599.0)

    # --- Solago ---

    def test_solago_preorder_overrides_instock_schema(self) -> None:
        data = {
            "@type": "Product",
            "name": "Midea PortaSplit-airconditioner",
            "description": "Portable split airco 8000 BTU",
            "offers": {
                "price": 1699.99,
                "availability": "https://schema.org/InStock",
                "url": "https://solago.nl/products/midea-portasplit-airconditioning",
            },
        }
        page = (
            f'<script type="application/ld+json">{json.dumps(data)}</script>'
            "<main><p>Voorbestelling – Levering vanaf eind juli</p></main>"
        )
        product = parse_solago_page(page, "https://solago.nl/products/midea-portasplit-airconditioning")
        self.assertIsNotNone(product)
        self.assertFalse(product.available)
        self.assertEqual(product.delivery, "Voorbestelling")
        self.assertEqual(product.btu, 8000)

    def test_solago_instock_without_preorder_is_available(self) -> None:
        data = {
            "@type": "Product",
            "name": "Midea PortaSplit-airconditioner",
            "offers": {
                "price": 1699.99,
                "availability": "https://schema.org/InStock",
                "url": "https://solago.nl/products/midea-portasplit",
            },
        }
        page = f'<script type="application/ld+json">{json.dumps(data)}</script>'
        product = parse_solago_page(page, "https://solago.nl/products/midea-portasplit")
        self.assertTrue(product.available)

    # --- Hubo ---

    def test_hubo_reads_shopify_json_ld(self) -> None:
        data = {
            "@type": "Product",
            "name": "Qlima P 522 mobiele airconditioner met verwarming 7000BTU",
            "description": "Mobiele airco met koelvermogen 7000 BTU.",
            "offers": {
                "@type": "Offer",
                "availability": "https://schema.org/InStock",
                "price": "399.00",
                "priceCurrency": "EUR",
                "url": "https://www.hubo.nl/products/qlima-p-522",
            },
        }
        page = f'<script type="application/ld+json">{json.dumps(data)}</script>'
        product = parse_hubo_page(page, "https://www.hubo.nl/products/qlima-p-522-mobiele-airconditioner")
        self.assertIsNotNone(product)
        self.assertTrue(product.available)
        self.assertEqual(product.btu, 7000)
        self.assertEqual(product.price_eur, 399.0)

    def test_hubo_out_of_stock_is_unavailable(self) -> None:
        data = {
            "@type": "Product",
            "name": "Mobiele airco PAC 9.3 compact",
            "offers": {
                "@type": "Offer",
                "availability": "https://schema.org/OutOfStock",
                "price": "179.00",
                "priceCurrency": "EUR",
            },
        }
        page = f'<script type="application/ld+json">{json.dumps(data)}</script>'
        product = parse_hubo_page(page, "https://www.hubo.nl/products/mobiele-airco-pac-9-3-compact")
        self.assertIsNotNone(product)
        self.assertFalse(product.available)

    # --- Vrijbuiter ---

    def test_vrijbuiter_reads_graph_product_offer(self) -> None:
        data = {
            "@context": "https://schema.org",
            "@graph": [
                {
                    "@type": "Product",
                    "name": "MS-AC 5001 Airco",
                    "description": "Mini split airco met koelcapaciteit 5000 BTU voor caravan.",
                    "offers": {
                        "@type": "Offer",
                        "availability": "https://schema.org/OutOfStock",
                        "price": "698.99",
                        "priceCurrency": "EUR",
                        "url": "https://www.vrijbuiter.nl/p/qlima-ms-ac-5001",
                    },
                }
            ],
        }
        page = f'<script type="application/ld+json">{json.dumps(data)}</script>'
        product = parse_vrijbuiter_page(page, "https://www.vrijbuiter.nl/p/qlima-ms-ac-5001-airco-cdhe44840")
        self.assertIsNotNone(product)
        self.assertFalse(product.available)
        self.assertEqual(product.price_eur, 698.99)
        self.assertEqual(product.btu, 5000)

    def test_vrijbuiter_excludes_aircooler(self) -> None:
        data = {
            "@context": "https://schema.org",
            "@graph": [
                {
                    "@type": "Product",
                    "name": "Luchtkoeler aircooler",
                    "offers": {"availability": "https://schema.org/InStock", "price": "99.00"},
                }
            ],
        }
        page = f'<script type="application/ld+json">{json.dumps(data)}</script>'
        product = parse_vrijbuiter_page(page, "https://www.vrijbuiter.nl/p/luchtkoeler-1")
        self.assertIsNone(product)


if __name__ == "__main__":
    unittest.main()
