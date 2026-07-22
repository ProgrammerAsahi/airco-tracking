from __future__ import annotations

import logging
from typing import Any
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup

from ...fetch import Fetcher
from ...models import Product
from ...url_security import validate_discovered_merchant_url
from ..base import canonical_url, clean_text, parse_btu, verified_empty
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
        # Hubo removes seasonal aircos from its sitemaps. A healthy sitemap
        # without airco candidates is a legitimate empty snapshot: a restock
        # will reappear and be alerted as first-seen stock.
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
        if product_urls and not products:
            raise RuntimeError("Hubo product pages could not be parsed: " + "; ".join(failures))
        if not product_urls:
            return verified_empty(
                self,
                source="official_product_sitemaps",
                signal="healthy product sitemaps contained zero portable-airco candidates",
            )
        return list(products.values())

    def _discover_airco_urls(self) -> set[str]:
        sitemap_urls = [
            validate_discovered_merchant_url(url, site=self.site)
            for url in _sitemap_locs(
                self.fetcher.get_bytes(
                    self.sitemap_index_url,
                    allowed_content_types=("application/xml", "text/xml", "text/plain"),
                    maximum_response_bytes=4 * 1024 * 1024,
                )
            )
        ]
        product_sitemaps = [u for u in sitemap_urls if "sitemap_products_" in u]
        if not product_sitemaps:
            raise RuntimeError("Hubo sitemap index listed no product sitemaps")
        airco_urls: set[str] = set()
        product_url_count = 0
        for sitemap_url in product_sitemaps:
            content = self.fetcher.get_bytes(
                sitemap_url,
                allowed_content_types=("application/xml", "text/xml", "text/plain"),
                maximum_response_bytes=16 * 1024 * 1024,
            )
            locs = [
                validate_discovered_merchant_url(url, site=self.site)
                for url in _sitemap_locs(content)
            ]
            product_url_count += len(locs)
            for loc in locs:
                if _is_airco_url(loc):
                    airco_urls.add(loc)
        if not product_url_count:
            # Sitemaps that once listed products no longer do: the sitemap
            # contract changed, so fail loudly instead of looking legitimately
            # empty.
            raise RuntimeError("Hubo product sitemaps contained no product URLs")
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
    # Availability markers must come from the product area only: the cart
    # drawer/footer always contains order-button text such as "in
    # winkelwagen"/"bezorgen", and related-product sections can carry their
    # own "uitverkocht" labels.
    scoped = clean_text(_product_area(soup))
    available, delivery = _availability_from_page(offer, scoped)
    return Product(
        site="Hubo",
        name=name,
        url=canonical_url(page_url, str(offer.get("url") or data.get("url") or page_url)),
        available=available,
        price_eur=offer_price(offer),
        delivery=delivery,
        btu=parse_btu(f"{name} {description} {text}"),
    )


def _product_area(soup: BeautifulSoup) -> Any:
    """Return the main product section, falling back to <main> then the page."""
    section = soup.select_one('[id^="shopify-section-"][id$="__product"]')
    if section is not None:
        return section
    return soup.find("main") or soup


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
