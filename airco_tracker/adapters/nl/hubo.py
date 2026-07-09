from __future__ import annotations

import logging
from typing import Any
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup

from ...fetch import Fetcher
from ...models import Product
from ..base import canonical_url, clean_text, parse_btu
from ..schema import first_offer, offer_price, product_json_ld, schema_in_stock


LOG = logging.getLogger(__name__)

_SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


class HuboAdapter:
    """Hubo — Shopify storefront discovered via its product sitemaps.

    Hubo does not expose a category page for airco; instead the adapter scans
    the robots-advertised Shopify product sitemaps for portable-airco URLs and
    reads JSON-LD from each product detail page.
    """

    site = "Hubo"
    sitemap_index_url = "https://www.hubo.nl/sitemap.xml"

    def __init__(self, fetcher: Fetcher) -> None:
        self.fetcher = fetcher

    def fetch_products(self) -> list[Product]:
        product_urls = self._discover_airco_urls()
        if not product_urls:
            raise RuntimeError("Hubo sitemap contained no portable air conditioners")
        products: dict[str, Product] = {}
        failures: list[str] = []
        for url in sorted(product_urls):
            try:
                product = _parse_product_page(self.fetcher.get(url), url)
            except Exception as exc:
                failures.append(f"{url}: {exc}")
                LOG.warning("Hubo product check failed for %s: %s", url, exc)
                continue
            if product is not None:
                products[product.url] = product
        if not products:
            raise RuntimeError("Hubo product pages could not be parsed: " + "; ".join(failures))
        return list(products.values())

    def _discover_airco_urls(self) -> set[str]:
        index = self.fetcher.session.get(self.sitemap_index_url, timeout=self.fetcher.timeout)
        index.raise_for_status()
        sitemap_urls = _sitemap_locs(index.content)
        product_sitemaps = [u for u in sitemap_urls if "sitemap_products_" in u]
        airco_urls: set[str] = set()
        for sitemap_url in product_sitemaps:
            resp = self.fetcher.session.get(sitemap_url, timeout=self.fetcher.timeout)
            if resp.status_code != 200:
                continue
            for loc in _sitemap_locs(resp.content):
                if _is_airco_url(loc):
                    airco_urls.add(loc)
        return airco_urls


def _sitemap_locs(content: bytes) -> list[str]:
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise RuntimeError("Hubo sitemap was invalid") from exc
    return [
        (node.text or "").strip()
        for node in root.findall(".//{*}loc")
        if (node.text or "").strip()
    ]


def _is_airco_url(url: str) -> bool:
    lower = url.lower()
    if not any(term in lower for term in ("airco", "aircondition")):
        return False
    return not any(term in lower for term in ("aircooler", "luchtkoeler", "ventilator"))


def _parse_product_page(page: str, page_url: str) -> Product | None:
    soup = BeautifulSoup(page, "html.parser")
    data = product_json_ld(soup)
    name = str(data.get("name", "")).strip()
    if not name or not _is_portable_airco(name):
        return None
    offer = first_offer(data)
    if not offer:
        raise RuntimeError("Hubo product data did not contain an offer")
    description = str(data.get("description", ""))
    text = clean_text(soup)
    available, delivery = _availability_from_page(offer, text)
    return Product(
        site="Hubo",
        name=name,
        url=canonical_url(page_url, str(offer.get("url") or data.get("url") or page_url)),
        available=available,
        price_eur=offer_price(offer),
        delivery=delivery,
        btu=parse_btu(f"{name} {description} {text}"),
    )


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
    return "mobiele airco" in lower or "mobiele airconditioner" in lower or "airco" in lower


def _availability_from_page(offer: dict[str, Any], text: str) -> tuple[bool, str]:
    lower = text.casefold()
    store_only_markers = (
        "alleen verkrijgbaar in de winkel",
    )
    unavailable_markers = (
        "uitverkocht",
        "niet op voorraad",
        "niet beschikbaar",
        "tijdelijk niet leverbaar",
    )
    online_order_markers = (
        "in winkelwagen",
        "toevoegen aan winkelwagen",
        "online op voorraad",
        "thuisbezorgd",
        "bezorgen",
    )
    if any(marker in lower for marker in store_only_markers):
        return False, "Alleen verkrijgbaar in de winkel"
    if any(marker in lower for marker in unavailable_markers):
        return False, "Niet op voorraad"
    if any(marker in lower for marker in online_order_markers):
        return True, "Online op voorraad"
    available = schema_in_stock(offer)
    return available, "Op voorraad" if available else "Niet op voorraad"
