from __future__ import annotations

from bs4 import BeautifulSoup

from ...models import Product
from ..base import (
    Adapter,
    canonical_url,
    clean_text,
    enrich_available_btu,
    parse_btu,
    parse_watt_rating_btu,
)


class DiyStoreAdapter(Adapter):
    """Shared parser for GAMMA and KARWEI's server-rendered product tiles."""

    def is_portable_airco(self, name: str) -> bool:
        return _is_portable_airco(name)

    def parse(self, soup: BeautifulSoup, page_url: str) -> list[Product]:
        products: dict[str, Product] = {}
        for card in soup.select("article.js-product-tile"):
            link = card.select_one("a.click-mask[href]")
            if link is None:
                continue
            name = str(link.get("title", "")).strip()
            if not self.is_portable_airco(name):
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
                btu=parse_btu(text) or parse_btu(name) or parse_watt_rating_btu(name),
            )
        return list(products.values())


class GammaAdapter(DiyStoreAdapter):
    site = "GAMMA"
    urls = (
        "https://www.gamma.nl/assortiment/l/"
        "verwarming-isolatie-ventilatie/airco-ventilatoren/airco",
    )

    def fetch_products(self) -> list[Product]:
        products: dict[str, Product] = {}
        for url in self.urls:
            soup = BeautifulSoup(self.fetcher.get(url), "html.parser")
            if not _has_supported_product_tiles(soup):
                raise RuntimeError(
                    f"{self.site}: parser found no supported product tiles; "
                    "site markup may have changed"
                )
            for product in self.parse(soup, url):
                products[product.url] = product
        # GAMMA can legitimately have no portable units in its current
        # assortment.  A structurally valid category page is therefore a
        # successful empty check, not parser drift that should retain stale
        # stock from an earlier scan.
        return enrich_available_btu(self.fetcher, list(products.values()))

    def is_portable_airco(self, name: str) -> bool:
        return _is_portable_airco(
            name,
            extra_terms=("draagbare airco", "draagbare airconditioner"),
        )


class KarweiAdapter(DiyStoreAdapter):
    site = "KARWEI"
    urls = ("https://www.karwei.nl/assortiment/l/ventilatie-verwarming/airco",)

    def fetch_products(self) -> list[Product]:
        products: dict[str, Product] = {}
        for url in self.urls:
            soup = BeautifulSoup(self.fetcher.get(url), "html.parser")
            if not _has_supported_product_tiles(soup):
                raise RuntimeError(
                    f"{self.site}: parser found no supported product tiles; "
                    "site markup may have changed"
                )
            for product in self.parse(soup, url):
                products[product.url] = product
        # KARWEI removes discontinued seasonal portable units from its
        # category.  A structurally valid split/accessory-only category is a
        # successful empty check and must clear stale inventory from summer.
        return enrich_available_btu(self.fetcher, list(products.values()))


def _is_portable_airco(name: str, *, extra_terms: tuple[str, ...] = ()) -> bool:
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
    return any(
        term in lower
        for term in ("mobiele airco", "mobiele airconditioner", *extra_terms)
    )


def _delivery(state: str) -> str:
    return {
        "ONLINE_AVAILABLE": "Online beschikbaar",
        "HAS_STORE_STOCK": "Alleen in de bouwmarkt",
        "CLICK_AND_COLLECT": "Alleen afhalen",
        "HAS_NO_ONLINE_AND_STORE_STOCK": "Niet beschikbaar",
    }.get(state, "Niet online beschikbaar")


def _has_supported_product_tiles(soup: BeautifulSoup) -> bool:
    cards = soup.select("article.js-product-tile")
    return bool(cards) and all(
        card.get("data-state")
        and (link := card.select_one("a.click-mask[href]")) is not None
        and str(link.get("title", "")).strip()
        for card in cards
    )
