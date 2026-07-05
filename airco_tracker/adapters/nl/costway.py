from __future__ import annotations

from bs4 import BeautifulSoup

from ...models import Product
from ..base import Adapter, canonical_url, clean_text, parse_btu, parse_price


class CostwayAdapter(Adapter):
    """Costway NL — Magento server-rendered category page."""

    site = "Costway NL"
    urls = (
        "https://nl.costway.com/huishoudelijke-apparaten/klimaatbeheersing/aircos.html",
    )

    def parse(self, soup: BeautifulSoup, page_url: str) -> list[Product]:
        products: dict[str, Product] = {}
        for card in soup.select("li.item.product"):
            link = card.select_one("a.product-item-link")
            if link is None:
                continue
            href = str(link.get("href", ""))
            name = clean_text(link)
            if not href or not _is_portable_airco(name):
                continue
            url = canonical_url(page_url, href)
            if url in products:
                continue
            text = clean_text(card)
            available = _in_stock(card, text)
            products[url] = Product(
                site=self.site,
                name=name,
                url=url,
                available=available,
                price_eur=parse_price(text),
                delivery="Op voorraad" if available else "Uitverkocht",
                btu=parse_btu(text),
            )
        return list(products.values())


def _in_stock(card: BeautifulSoup, text: str) -> bool:
    """Costway marks the product photo with a ``qty-N`` class; N>0 means in stock."""
    photo = card.select_one(".product-item-photo")
    if photo is not None:
        for cls in photo.get("class", []):
            if cls.startswith("qty-"):
                try:
                    return int(cls[4:]) > 0
                except ValueError:
                    break
    # Fall back to the visible out-of-stock label.
    return "uitverkocht" not in text.lower()


def _is_portable_airco(name: str) -> bool:
    lower = name.lower()
    excluded = (
        "aircooler",
        "luchtkoeler",
        "ventilator",
        "split-airconditioner",
        "split airco",
        "mini-split",
        "mini split",
        "raamafdichting",
    )
    if any(term in lower for term in excluded):
        return False
    return "airconditioning" in lower or "airconditioner" in lower or "airco" in lower
