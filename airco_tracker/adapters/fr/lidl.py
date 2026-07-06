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


class LidlFranceAdapter:
    site = "Lidl France"
    sitemap_url = "https://www.lidl.fr/p/export/FR/fr/product_sitemap.xml.gz"

    def __init__(self, fetcher: Fetcher) -> None:
        self.fetcher = fetcher

    def fetch_products(self) -> list[Product]:
        response = self.fetcher.session.get(self.sitemap_url, timeout=self.fetcher.timeout)
        response.raise_for_status()
        urls = _product_urls(response.content)
        if not urls:
            raise RuntimeError("Lidl France sitemap contained no portable air conditioners")

        products: dict[str, Product] = {}
        failures: list[str] = []
        for url in urls:
            try:
                product = _parse_product_page(self.fetcher.get(url), url)
            except Exception as exc:
                failures.append(f"{url}: {exc}")
                LOG.warning("Lidl France product check failed for %s: %s", url, exc)
                continue
            products[product.url] = product
        if not products:
            raise RuntimeError("Lidl France product pages could not be parsed: " + "; ".join(failures))
        return list(products.values())


def _product_urls(content: bytes) -> list[str]:
    try:
        raw = gzip.decompress(content) if content.startswith(b"\x1f\x8b") else content
        root = ElementTree.fromstring(raw)
    except (OSError, ElementTree.ParseError) as exc:
        raise RuntimeError("Lidl France product sitemap was invalid") from exc
    urls: list[str] = []
    for node in root.findall(".//{*}loc"):
        url = (node.text or "").strip()
        lower = url.casefold()
        if not url or "climatiseur" not in lower:
            continue
        if any(term in lower for term in ("rafraichisseur", "refroidisseur", "ventilateur")):
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
    if brand_name and brand_name.casefold() not in name.casefold():
        name = f"{brand_name} {name}".strip()
    offer = first_offer(data)
    if not name or not offer:
        raise RuntimeError("Lidl France product data did not contain a name and offer")
    available = schema_in_stock(offer)
    description = str(data.get("description", ""))
    return Product(
        site="Lidl France",
        name=name,
        url=canonical_url(page_url, str(offer.get("url") or page_url)),
        available=available,
        price_eur=offer_price(offer),
        delivery="En ligne" if available else "Épuisé en ligne",
        btu=(
            parse_btu(f"{name} {description}")
            or parse_cooling_watts_btu(description)
            or parse_product_page_btu(page)
        ),
    )
