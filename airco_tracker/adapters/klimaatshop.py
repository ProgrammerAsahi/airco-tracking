from __future__ import annotations

import re

from bs4 import BeautifulSoup

from ..models import Product
from .base import Adapter, canonical_url, clean_text, parse_btu, parse_price


class KlimaatshopAdapter(Adapter):
    """Klimaatshop — specialist airco dealer with a custom server-rendered grid.

    The "aircos zonder buitenunit" (aircos without outdoor unit) category covers
    portable/monoblock units. Product URLs and names are derived from the card's
    ``data-url`` attribute; stock and price are read from the ``.stock`` span.
    """

    site = "Klimaatshop"
    urls = ("https://www.klimaatshop.nl/aircos/aircos-zonder-buitenunit/",)

    def parse(self, soup: BeautifulSoup, page_url: str) -> list[Product]:
        products: dict[str, Product] = {}
        for card in soup.select(".product"):
            data_url = str(card.get("data-url") or "")
            if not data_url:
                continue
            url = canonical_url(page_url, data_url)
            name = _name_from_url(data_url)
            if not name or not _is_portable_airco(name):
                continue
            if url in products:
                continue
            text = clean_text(card)
            stock_span = card.select_one(".stock")
            stock_text = clean_text(stock_span) if stock_span else ""
            available = _in_stock(stock_text, text)
            products[url] = Product(
                site=self.site,
                name=name,
                url=url,
                available=available,
                price_eur=parse_price(text),
                delivery=stock_text or ("Op voorraad" if available else "Niet op voorraad"),
                btu=parse_btu(name) or parse_btu(text),
            )
        return list(products.values())


def _name_from_url(url: str) -> str:
    """Derive a human-readable product name from the URL slug."""
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    slug = slug.split("?")[0].split(".")[0]
    slug = re.sub(r"-\d+kw$", "", slug, flags=re.I)
    slug = re.sub(r"-(\d+)$", r" \1", slug)
    return slug.replace("-", " ").strip().capitalize()


def _in_stock(stock_text: str, card_text: str) -> bool:
    lower = (stock_text + " " + card_text).lower()
    if "uitverkocht" in lower or "niet op voorraad" in lower or "niet leverbaar" in lower:
        return False
    return "op voorraad" in lower or "leverbaar" in lower


def _is_portable_airco(name: str) -> bool:
    lower = name.lower()
    excluded = (
        "aircooler",
        "luchtkoeler",
        "ventilator",
        "split airco",
        "split-unit",
        "mini-split",
        "raamafdichting",
        "raamafdekkit",
        "accessoire",
        "accessoires",
    )
    if any(term in lower for term in excluded):
        return False
    return "mobiele airco" in lower or "airco" in lower or "aircondition" in lower
