from __future__ import annotations

import logging
from typing import Any

from bs4 import BeautifulSoup

from ...fetch import Fetcher
from ...models import Product
from ...url_security import validate_discovered_merchant_url
from ..base import canonical_url, clean_text, parse_btu
from ..schema import first_offer, offer_price, product_json_ld, schema_in_stock


LOG = logging.getLogger(__name__)


class VrijbuiterAdapter:
    """Vrijbuiter — camping/outdoor retailer with a small airco category.

    The category page exposes server-rendered product links. Each product detail
    page contains @graph-wrapped JSON-LD with offer and availability data.
    Portable split units for caravan/camper use (e.g. Mestic SPA) are tracked;
    fixed-installation split is excluded.
    """

    site = "Vrijbuiter"
    category_url = "https://www.vrijbuiter.nl/kampeerartikelen/koeling/aircos"

    def __init__(self, fetcher: Fetcher) -> None:
        self.fetcher = fetcher

    def fetch_products(self) -> list[Product]:
        page = self.fetcher.get(self.category_url)
        soup = BeautifulSoup(page, "html.parser")
        urls = {
            validate_discovered_merchant_url(url, site=self.site)
            for url in _product_urls(soup, self.category_url)
        }
        if not urls:
            raise RuntimeError("Vrijbuiter category contained no product links")
        products: dict[str, Product] = {}
        failures: list[str] = []
        for url in sorted(urls):
            try:
                product = _parse_product_page(self.fetcher.get(url), url)
            except Exception as exc:
                failures.append(f"{url}: {exc}")
                LOG.warning("Vrijbuiter product check failed for %s: %s", url, exc)
                continue
            if product is not None:
                products[product.url] = product
        if not products:
            raise RuntimeError("Vrijbuiter product pages could not be parsed: " + "; ".join(failures))
        return list(products.values())


def _product_urls(soup: BeautifulSoup, page_url: str) -> set[str]:
    urls: set[str] = set()
    for link in soup.select('a[href*="/p/"]'):
        href = str(link.get("href", ""))
        if "/p/" in href:
            urls.add(canonical_url(page_url, href))
    return urls


def _parse_product_page(page: str, page_url: str) -> Product | None:
    soup = BeautifulSoup(page, "html.parser")
    data = product_json_ld(soup)
    name = str(data.get("name", "")).strip()
    if not name or not _is_portable_airco(name):
        return None
    offer = first_offer(data)
    if not offer:
        raise RuntimeError("Vrijbuiter product data did not contain an offer")
    available = schema_in_stock(offer)
    description = str(data.get("description", ""))
    text = clean_text(soup)
    return Product(
        site="Vrijbuiter",
        name=name,
        url=canonical_url(page_url, str(offer.get("url") or data.get("url") or page_url)),
        available=available,
        price_eur=offer_price(offer),
        delivery="Op voorraad" if available else "Niet op voorraad",
        btu=parse_btu(f"{name} {description} {text}"),
    )


def _is_portable_airco(name: str) -> bool:
    lower = name.lower()
    excluded = (
        "aircooler",
        "luchtkoeler",
        "ventilator",
        "raamafdichting",
    )
    if any(term in lower for term in excluded):
        return False
    # Split-unit airco for caravan/camper (Mestic SPA, Qlima MS-AC) is portable.
    return "airco" in lower or "aircondition" in lower
