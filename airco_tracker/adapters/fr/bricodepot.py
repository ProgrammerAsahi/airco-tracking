from __future__ import annotations

import json
import re
from typing import Any

from bs4 import BeautifulSoup

from ...models import Product
from ..base import Adapter, enrich_available_btu, parse_btu, parse_cooling_watts_btu, parse_watt_rating_btu
from .common import is_real_air_conditioner_fr, parse_float


class BricoDepotFranceAdapter(Adapter):
    """Brico Dépôt France — Nuxt category with server-rendered JSON-LD products."""

    site = "Brico Dépôt France"
    urls = (
        "https://www.bricodepot.fr/produits/chauffage-clim-et-ventilation/"
        "climatisation-et-confort-thermique/climatiseur/climatiseur-mobile"
        "?frz-smartcache-fragment=true&frz-timeout=5000&frz-smartcache-v=2&frz-smartcache-placeholders-number=12",
    )

    def fetch_products(self) -> list[Product]:
        products: dict[str, Product] = {}
        for url in self.urls:
            page = self.fetcher.get(url)
            for item in _raw_json_ld_products(page):
                product = _parse_product(item)
                if product is not None:
                    products[product.url] = product
        if not products:
            raise RuntimeError(f"{self.site}: parser found no products; site markup may have changed")
        return enrich_available_btu(self.fetcher, list(products.values()))

    def parse(self, soup: BeautifulSoup, page_url: str) -> list[Product]:
        products: dict[str, Product] = {}
        for item in _json_ld_products(soup):
            product = _parse_product(item)
            if product is not None:
                products[product.url] = product
        return list(products.values())


def _parse_product(item: dict[str, Any]) -> Product | None:
    name = str(item.get("name") or "").strip()
    url = str(item.get("url") or "").strip()
    if not name or not url or not is_real_air_conditioner_fr(name):
        return None

    offers = item.get("offers")
    if not isinstance(offers, dict):
        offers = {}
    availability = str(offers.get("availability") or "").casefold()
    available = availability.endswith("/instock") or "instock" in availability
    price = parse_float(offers.get("price"))
    category = str(item.get("category") or "").strip()
    text = " ".join(part for part in (name, category, str(item.get("description") or "")) if part)

    return Product(
        site="Brico Dépôt France",
        name=name,
        url=url,
        available=available,
        price_eur=price,
        delivery="Disponible selon dépôt" if available else "Indisponible",
        btu=parse_btu(text) or parse_cooling_watts_btu(text) or parse_watt_rating_btu(name),
    )


def _raw_json_ld_products(page: str) -> list[dict[str, Any]]:
    products: list[dict[str, Any]] = []
    products.extend(_script_json_ld_products(BeautifulSoup(page, "html.parser")))
    products.extend(_fasterize_fragment_products(page))
    return products


def _json_ld_products(soup: BeautifulSoup) -> list[dict[str, Any]]:
    products: list[dict[str, Any]] = []
    products.extend(_script_json_ld_products(soup))
    products.extend(_fasterize_fragment_products(str(soup)))
    return products


def _script_json_ld_products(soup: BeautifulSoup) -> list[dict[str, Any]]:
    products: list[dict[str, Any]] = []
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.get_text(strip=True))
        except json.JSONDecodeError:
            continue
        products.extend(_products_from_json_ld(data))
    return products


def _fasterize_fragment_products(text: str) -> list[dict[str, Any]]:
    match = re.search(r"fasterizeNs\.processFragments\((?P<payload>.*)\);?\s*$", text, re.S)
    if match is None:
        return []
    try:
        fragments = json.loads(match.group("payload"))
    except json.JSONDecodeError:
        return []
    if not isinstance(fragments, dict):
        return []

    products: list[dict[str, Any]] = []
    for fragment in fragments.values():
        if not isinstance(fragment, dict):
            continue
        content = fragment.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            products.extend(_script_json_ld_products(BeautifulSoup(content, "html.parser")))
        else:
            products.extend(_products_from_json_ld(data))
    return products


def _products_from_json_ld(data: Any) -> list[dict[str, Any]]:
    products: list[dict[str, Any]] = []
    for node in _walk_json(data):
        if not isinstance(node, dict):
            continue
        if node.get("@type") != "ItemList":
            continue
        for element in node.get("itemListElement", []):
            if not isinstance(element, dict):
                continue
            item = element.get("item")
            if isinstance(item, dict) and item.get("@type") == "Product":
                products.append(item)
    return products


def _walk_json(value: Any):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)
