from __future__ import annotations

import unittest

from bs4 import BeautifulSoup

from airco_tracker.adapters.bol import BolAdapter
from airco_tracker.adapters.coolblue import CoolblueAdapter
from airco_tracker.adapters.mediamarkt import MediaMarktAdapter
from airco_tracker.adapters.base import parse_btu, parse_price


class DummyFetcher:
    pass


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

    def test_bol_excludes_aircooler(self) -> None:
        html = """
        <div><a href="/nl/nl/p/mini-aircooler-mobiele-airco/9300000000001/">Mini Aircooler Mobiele Airco</a>
        <span>€ 49,95 Op voorraad</span></div>
        <div><a href="/nl/nl/p/echte-mobiele-airco/9300000000002/">Echte Mobiele Airco 7000 BTU</a>
        <span>Werkt met afvoerslang naar buiten € 299,00 Op voorraad Morgen in huis</span></div>"""
        products = BolAdapter(DummyFetcher()).parse(BeautifulSoup(html, "html.parser"), "https://www.bol.com/")
        self.assertEqual(len(products), 1)
        self.assertTrue(products[0].available)
        self.assertEqual(products[0].btu, 7000)


if __name__ == "__main__":
    unittest.main()
