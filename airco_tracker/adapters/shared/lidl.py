from __future__ import annotations

import gzip
import logging
from xml.etree import ElementTree

from bs4 import BeautifulSoup

from ...fetch import Fetcher
from ...models import Product
from ..base import canonical_url, parse_btu, parse_cooling_watts_btu, parse_product_page_btu
from ..schema import first_offer, offer_price, product_json_ld, schema_in_stock


LOG = logging.getLogger(__name__)


class LidlSitemapAdapter:
    """Shared Lidl sitemap + JSON-LD PDP adapter."""

    site: str
    sitemap_url: str
    include_url_terms: tuple[str, ...]
    exclude_url_terms: tuple[str, ...]
    empty_message: str
    invalid_sitemap_message: str
    parse_failure_message: str
    available_delivery: str
    unavailable_delivery: str

    def __init__(self, fetcher: Fetcher) -> None:
        self.fetcher = fetcher

    def fetch_products(self) -> list[Product]:
        response = self.fetcher.session.get(self.sitemap_url, timeout=self.fetcher.timeout)
        response.raise_for_status()
        urls = product_urls_from_sitemap(
            response.content,
            include_terms=self.include_url_terms,
            exclude_terms=self.exclude_url_terms,
            invalid_message=self.invalid_sitemap_message,
        )
        if not urls:
            raise RuntimeError(self.empty_message)

        products: dict[str, Product] = {}
        failures: list[str] = []
        for url in urls:
            try:
                product = parse_lidl_product_page(
                    self.fetcher.get(url),
                    url,
                    site=self.site,
                    available_delivery=self.available_delivery,
                    unavailable_delivery=self.unavailable_delivery,
                )
            except Exception as exc:
                failures.append(f"{url}: {exc}")
                LOG.warning("%s product check failed for %s: %s", self.site, url, exc)
                continue
            products[product.url] = product
        if not products:
            raise RuntimeError(self.parse_failure_message + ": " + "; ".join(failures))
        return list(products.values())


def product_urls_from_sitemap(
    content: bytes,
    *,
    include_terms: tuple[str, ...],
    exclude_terms: tuple[str, ...],
    invalid_message: str,
) -> list[str]:
    try:
        raw = gzip.decompress(content) if content.startswith(b"\x1f\x8b") else content
        root = ElementTree.fromstring(raw)
    except (OSError, ElementTree.ParseError) as exc:
        raise RuntimeError(invalid_message) from exc
    urls: list[str] = []
    for node in root.findall(".//{*}loc"):
        url = (node.text or "").strip()
        lower = url.casefold()
        if not url or not any(term in lower for term in include_terms):
            continue
        if any(term in lower for term in exclude_terms):
            continue
        urls.append(url)
    return urls


def parse_lidl_product_page(
    page: str,
    page_url: str,
    *,
    site: str,
    available_delivery: str,
    unavailable_delivery: str,
) -> Product:
    soup = BeautifulSoup(page, "html.parser")
    data = product_json_ld(soup)
    name = str(data.get("name", "")).strip()
    brand = data.get("brand")
    if isinstance(brand, dict):
        brand = brand.get("name")
    brand_name = str(brand or "").strip()
    if brand_name and brand_name.casefold() not in name.casefold():
        name = f"{brand_name} {name}".strip()
    offer = first_offer(data)
    if not name or not offer:
        raise RuntimeError(f"{site} product data did not contain a name and offer")
    available = schema_in_stock(offer)
    description = str(data.get("description", ""))
    return Product(
        site=site,
        name=name,
        url=canonical_url(page_url, str(offer.get("url") or page_url)),
        available=available,
        price_eur=offer_price(offer),
        delivery=available_delivery if available else unavailable_delivery,
        btu=(
            parse_btu(f"{name} {description}")
            or parse_cooling_watts_btu(description)
            or parse_product_page_btu(page)
        ),
    )

