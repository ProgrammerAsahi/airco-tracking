from __future__ import annotations

from bs4 import BeautifulSoup

from ..models import Product
from .base import Adapter, canonical_url, clean_text, parse_btu, parse_price


class AircoVoorInHuisAdapter(Adapter):
    """Airco voor in huis — WooCommerce server-rendered product grid."""

    site = "Airco voor in huis"
    urls = (
        "https://www.aircovoorinhuis.nl/airco/mobiele-airco/mobiele-airco-systemen/",
    )

    def parse(self, soup: BeautifulSoup, page_url: str) -> list[Product]:
        products: dict[str, Product] = {}
        for card in soup.select("ul.products li.product"):
            link = card.select_one("a.ct-media-container[href]")
            if link is None:
                continue
            name = str(link.get("aria-label", "")).strip() or clean_text(link)
            if not name or not _is_portable_airco(name):
                continue
            href = str(link.get("href", ""))
            url = canonical_url(page_url, href)
            if url in products:
                continue
            classes = card.get("class", [])
            available = "instock" in classes and "outofstock" not in classes
            text = clean_text(card)
            products[url] = Product(
                site=self.site,
                name=name,
                url=url,
                available=available,
                price_eur=parse_price(text),
                delivery="Op voorraad" if available else "Uitverkocht",
                btu=parse_btu(name) or parse_btu(text),
            )
        return list(products.values())


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
    )
    if any(term in lower for term in excluded):
        return False
    return "mobiele airco" in lower or "mobiele airconditioner" in lower or "airconditioning" in lower
