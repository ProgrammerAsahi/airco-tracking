from __future__ import annotations

import json
import logging
from typing import Any, Iterable

from bs4 import BeautifulSoup

from ...fetch import Fetcher
from ...models import Product
from ...url_security import validate_discovered_merchant_url
from ..base import canonical_url, parse_btu, parse_cooling_watts_btu, parse_product_page_btu
from ..schema import first_offer, offer_price, product_json_ld, schema_in_stock
from .common import is_real_air_conditioner_fr


LOG = logging.getLogger(__name__)


class ObelinkFranceAdapter:
    """Obelink France — mobile/split camping air conditioners.

    Category pages expose product URLs through JSON-LD ItemList. Product pages
    expose the final stock state through schema.org Offer availability.
    """

    site = "Obelink France"
    category_urls = (
        "https://www.obelink.fr/climatisations/climatiseurs/climatiseurs-mobiles/",
        "https://www.obelink.fr/climatisations/climatiseurs/climatiseurs-split/",
    )

    def __init__(self, fetcher: Fetcher) -> None:
        self.fetcher = fetcher

    def fetch_products(self) -> list[Product]:
        urls: set[str] = set()
        for category_url in self.category_urls:
            urls.update(
                validate_discovered_merchant_url(url, site=self.site)
                for url in _category_product_urls(self.fetcher.get(category_url))
            )
        if not urls:
            raise RuntimeError(f"{self.site}: category JSON-LD contained no product URLs")

        products: dict[str, Product] = {}
        failures: list[str] = []
        for url in sorted(urls):
            try:
                product = _parse_product_page(self.fetcher.get(url), url)
            except Exception as exc:
                failures.append(f"{url}: {exc}")
                LOG.warning("Obelink France product check failed for %s: %s", url, exc)
                continue
            if is_real_air_conditioner_fr(product.name, product.delivery or ""):
                products[product.url] = product
        if not products:
            raise RuntimeError("Obelink France product pages could not be parsed: " + "; ".join(failures))
        return list(products.values())


def _category_product_urls(page: str) -> list[str]:
    urls: list[str] = []
    soup = BeautifulSoup(page, "html.parser")
    for data in _json_ld_nodes(soup):
        for node in _schema_nodes(data):
            if _type_contains(node, "ItemList"):
                for item in node.get("itemListElement", []):
                    if not isinstance(item, dict):
                        continue
                    product = item.get("item")
                    if not isinstance(product, dict) or not _type_contains(product, "Product"):
                        continue
                    url = str(product.get("url", "")).strip()
                    name = str(product.get("name", "")).strip()
                    description = str(product.get("description", "")).strip()
                    if url and is_real_air_conditioner_fr(name, description):
                        urls.append(_product_url("https://www.obelink.fr/", url))
    return list(dict.fromkeys(urls))


def _parse_product_page(page: str, page_url: str) -> Product:
    soup = BeautifulSoup(page, "html.parser")
    data = product_json_ld(soup)
    name = str(data.get("name", "")).strip()
    description = str(data.get("description", ""))
    offer = first_offer(data)
    if not name or not offer:
        raise RuntimeError("Obelink France product data did not contain a name and offer")
    available = schema_in_stock(offer)
    delivery = "En stock en ligne" if available else "Pas en stock en ligne"
    return Product(
        site="Obelink France",
        name=name,
        url=_product_url(page_url, str(data.get("url") or page_url)),
        available=available,
        price_eur=offer_price(offer),
        delivery=delivery,
        btu=parse_btu(f"{name} {description}") or parse_cooling_watts_btu(description) or parse_product_page_btu(page),
    )


def _json_ld_nodes(soup: BeautifulSoup) -> Iterable[Any]:
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            yield json.loads(script.string or script.get_text())
        except (json.JSONDecodeError, TypeError):
            continue


def _schema_nodes(data: Any) -> Iterable[dict[str, Any]]:
    if isinstance(data, list):
        for item in data:
            yield from _schema_nodes(item)
        return
    if not isinstance(data, dict):
        return
    yield data
    graph = data.get("@graph")
    if isinstance(graph, list):
        for item in graph:
            if isinstance(item, dict):
                yield item


def _type_contains(node: dict[str, Any], value: str) -> bool:
    raw = node.get("@type")
    values = raw if isinstance(raw, list) else [raw]
    return value in values


def _product_url(base_url: str, url: str) -> str:
    normalised = canonical_url(base_url, url)
    return normalised[:-1] if normalised.endswith(".html/") else normalised
