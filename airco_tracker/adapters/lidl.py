from __future__ import annotations

import gzip
import logging
from typing import Any
from xml.etree import ElementTree

from bs4 import BeautifulSoup

from ..fetch import Fetcher
from ..models import Product
from .base import canonical_url, parse_btu
from .schema import first_offer, offer_price, product_json_ld, schema_in_stock


LOG = logging.getLogger(__name__)


class LidlAdapter:
    """Discover Lidl products through its robots-advertised product sitemap."""

    site = "Lidl"
    sitemap_url = "https://www.lidl.nl/p/export/NL/nl/product_sitemap.xml.gz"

    def __init__(self, fetcher: Fetcher) -> None:
        self.fetcher = fetcher

    def fetch_products(self) -> list[Product]:
        response = self.fetcher.session.get(self.sitemap_url, timeout=self.fetcher.timeout)
        response.raise_for_status()
        urls = _product_urls(response.content)
        if not urls:
            raise RuntimeError("Lidl sitemap contained no portable air conditioners")

        products: dict[str, Product] = {}
        failures: list[str] = []
        for url in urls:
            try:
                product = _parse_product_page(self.fetcher.get(url), url)
            except Exception as exc:
                failures.append(f"{url}: {exc}")
                LOG.warning("Lidl product check failed for %s: %s", url, exc)
                continue
            products[product.url] = product
        if not products:
            raise RuntimeError("Lidl product pages could not be parsed: " + "; ".join(failures))
        return list(products.values())


def _product_urls(content: bytes) -> list[str]:
    try:
        raw = gzip.decompress(content) if content.startswith(b"\x1f\x8b") else content
        root = ElementTree.fromstring(raw)
    except (OSError, ElementTree.ParseError) as exc:
        raise RuntimeError("Lidl product sitemap was invalid") from exc
    urls: list[str] = []
    for node in root.findall(".//{*}loc"):
        url = (node.text or "").strip()
        lower = url.lower()
        if not url or not any(term in lower for term in ("airco", "aircondition")):
            continue
        if any(term in lower for term in ("aircooler", "luchtkoeler", "ventilator")):
            continue
        urls.append(url)
    return urls


def _parse_product_page(page: str, page_url: str) -> Product:
    soup = BeautifulSoup(page, "html.parser")
    data = product_json_ld(soup)
    name = str(data.get("name", "")).strip()
    brand = data.get("brand")
    if isinstance(brand, dict):
        brand = brand.get("name")
    brand_name = str(brand or "").strip()
    if brand_name and brand_name.lower() not in name.lower():
        name = f"{brand_name} {name}".strip()
    offer = first_offer(data)
    if not name or not offer:
        raise RuntimeError("Lidl product data did not contain a name and offer")
    available = schema_in_stock(offer)
    description = str(data.get("description", ""))
    return Product(
        site="Lidl",
        name=name,
        url=canonical_url(page_url, str(offer.get("url") or page_url)),
        available=available,
        price_eur=offer_price(offer),
        delivery="Online op voorraad" if available else "Online uitverkocht",
        btu=parse_btu(f"{name} {description}"),
    )
