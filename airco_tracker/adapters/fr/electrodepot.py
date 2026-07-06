from __future__ import annotations

import json
from typing import Any

from bs4 import BeautifulSoup

from ...models import Product
from ..base import Adapter, canonical_url, is_presale_delivery, parse_btu, parse_cooling_watts_btu
from .common import parse_float


class ElectroDepotFranceAdapter(Adapter):
    """Electro Dépôt France — product list embedded as Vue JSON props."""

    site = "Electro Dépôt France"
    urls = ("https://www.electrodepot.fr/maison-entretien-beaute/climatisation-ventilation/climatiseur.html",)

    def parse(self, soup: BeautifulSoup, page_url: str) -> list[Product]:
        products: dict[str, Product] = {}
        for item in _embedded_products(soup):
            product = _parse_item(item, page_url)
            if product is not None:
                products[product.url] = product
        return list(products.values())


def _parse_item(raw: dict[str, Any], page_url: str) -> Product | None:
    item = raw.get("item")
    if not isinstance(item, dict):
        return None
    attrs = _attributes(item)
    name = str(attrs.get("name") or item.get("name") or "").strip()
    if not _is_air_conditioner(name, attrs):
        return None

    slug = str(attrs.get("itemUrl") or "").strip()
    url = str(item.get("itemUrl") or "").strip()
    if not url and slug:
        url = canonical_url(page_url, f"/{slug}.html")
    if not url:
        return None

    stock = parse_float(attrs.get("stock") or item.get("stock"))
    details = " ".join([name, *attrs.values()])
    capacity_details = _capacity_text(name, attrs)
    presale = is_presale_delivery(details)
    available = presale or (stock is not None and stock > 0)

    return Product(
        site="Electro Dépôt France",
        name=name,
        url=canonical_url(page_url, url),
        available=available,
        price_eur=parse_float(item.get("price") or attrs.get("price")),
        delivery=_delivery_text(stock, presale=presale),
        btu=parse_btu(capacity_details) or parse_cooling_watts_btu(capacity_details),
        presale=presale,
    )


def _embedded_products(soup: BeautifulSoup) -> list[dict[str, Any]]:
    products: list[dict[str, Any]] = []
    for node in soup.select(".productlist-wrapper[data-vue-props]"):
        try:
            data = json.loads(str(node.get("data-vue-props") or ""))
        except json.JSONDecodeError:
            continue
        initial = data.get("initialProducts", [])
        if isinstance(initial, list):
            products.extend(item for item in initial if isinstance(item, dict))
    return products


def _attributes(item: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for attr in item.get("attributeInfo", []):
        if not isinstance(attr, dict):
            continue
        key = str(attr.get("attributeName") or attr.get("attributeLabel") or "").strip()
        vals = attr.get("vals", [])
        value_parts: list[str] = []
        if isinstance(vals, list):
            for val in vals:
                if isinstance(val, dict):
                    value = val.get("label") or val.get("value")
                else:
                    value = val
                if value is not None:
                    value_parts.append(str(value).strip())
        if key and value_parts:
            result[key] = " ".join(part for part in value_parts if part)
    return result


def _is_air_conditioner(name: str, attrs: dict[str, str]) -> bool:
    lower = _capacity_text(name, attrs).casefold()
    all_text = f"{name} {' '.join(attrs.values())}".casefold()
    name_lower = name.casefold()
    if "climatiseur" not in all_text:
        return False
    has_capacity = (
        parse_btu(lower) is not None
        or parse_cooling_watts_btu(lower) is not None
        or "monobloc" in name_lower
    )
    excluded = (
        "kit ",
        "accessoire",
        "fenêtre",
        "fenetre",
        "tuyau",
        "gaine",
        "filtre",
        "housse",
        "adaptateur",
        "rafraîchisseur d'air",
        "rafraichisseur d'air",
    )
    if any(term in name_lower for term in excluded) and not has_capacity:
        return False
    return has_capacity


def _capacity_text(name: str, attrs: dict[str, str]) -> str:
    parts = [name]
    for key, value in attrs.items():
        key_lower = key.casefold()
        value_lower = value.casefold()
        if key_lower.startswith("cle_attribut") or "puissance frigorifique" in value_lower:
            parts.append(value)
    return " ".join(parts)


def _delivery_text(stock: float | None, *, presale: bool) -> str:
    if presale:
        return "Précommande"
    if stock is not None and stock > 0:
        return "Stock national Electro Dépôt"
    if stock == 0:
        return "Indisponible / stock national 0"
    return "Disponibilité inconnue"
