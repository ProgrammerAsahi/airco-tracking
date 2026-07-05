from __future__ import annotations

from bs4 import BeautifulSoup

from ...models import Product
from ..base import Adapter, canonical_url, clean_text, parse_btu, parse_watt_rating_btu


class DiyStoreAdapter(Adapter):
    """Shared parser for GAMMA and KARWEI's server-rendered product tiles."""

    def parse(self, soup: BeautifulSoup, page_url: str) -> list[Product]:
        products: dict[str, Product] = {}
        for card in soup.select("article.js-product-tile"):
            link = card.select_one("a.click-mask[href]")
            if link is None:
                continue
            name = str(link.get("title", "")).strip()
            if not _is_portable_airco(name):
                continue
            href = str(link.get("href", ""))
            state = str(card.get("data-state", "")).strip().upper()
            available = state == "ONLINE_AVAILABLE"
            price = card.select_one('[itemprop="price"][content]')
            try:
                price_eur = float(str(price.get("content"))) if price is not None else None
            except ValueError:
                price_eur = None
            text = clean_text(card)
            url = canonical_url(page_url, href)
            products[url] = Product(
                site=self.site,
                name=name,
                url=url,
                available=available,
                price_eur=price_eur,
                delivery=_delivery(state),
                btu=parse_btu(text) or parse_watt_rating_btu(name),
            )
        return list(products.values())


class GammaAdapter(DiyStoreAdapter):
    site = "GAMMA"
    urls = (
        "https://www.gamma.nl/assortiment/l/"
        "verwarming-isolatie-ventilatie/airco-ventilatoren/airco",
    )


class KarweiAdapter(DiyStoreAdapter):
    site = "KARWEI"
    urls = ("https://www.karwei.nl/assortiment/l/ventilatie-verwarming/airco",)


def _is_portable_airco(name: str) -> bool:
    lower = name.lower()
    excluded = (
        "aircooler",
        "luchtkoeler",
        "ventilator",
        "split airco",
        "split-unit",
        "raamafdichting",
        "afvoer",
        "slang",
    )
    if any(term in lower for term in excluded):
        return False
    return "mobiele airco" in lower or "mobiele airconditioner" in lower


def _delivery(state: str) -> str:
    return {
        "ONLINE_AVAILABLE": "Online beschikbaar",
        "HAS_STORE_STOCK": "Alleen in de bouwmarkt",
        "CLICK_AND_COLLECT": "Alleen afhalen",
        "HAS_NO_ONLINE_AND_STORE_STOCK": "Niet beschikbaar",
    }.get(state, "Niet online beschikbaar")
