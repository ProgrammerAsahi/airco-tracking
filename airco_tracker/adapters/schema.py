from __future__ import annotations

import json
from typing import Any, Iterable

from bs4 import BeautifulSoup


def product_json_ld(soup: BeautifulSoup) -> dict[str, Any]:
    """Return the first schema.org Product, including products in @graph."""
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or script.get_text())
        except (json.JSONDecodeError, TypeError):
            continue
        for candidate in _schema_nodes(data):
            product_type = candidate.get("@type")
            types = product_type if isinstance(product_type, list) else [product_type]
            if "Product" in types:
                return candidate
    raise RuntimeError("page did not contain Product JSON-LD")


def first_offer(product: dict[str, Any]) -> dict[str, Any]:
    offers = product.get("offers")
    if isinstance(offers, dict):
        return offers
    if isinstance(offers, list):
        return next((offer for offer in offers if isinstance(offer, dict)), {})
    return {}


def offer_price(offer: dict[str, Any]) -> float | None:
    direct = _float(offer.get("price"))
    if direct is not None:
        return direct
    specifications = offer.get("priceSpecification")
    if isinstance(specifications, dict):
        specifications = [specifications]
    if not isinstance(specifications, list):
        return None
    # The first entry is normally the current unit price; entries marked as
    # ListPrice are the crossed-out comparison price.
    current = next(
        (
            item
            for item in specifications
            if isinstance(item, dict) and "ListPrice" not in str(item.get("priceType", ""))
        ),
        None,
    )
    return _float(current.get("price")) if current else None


def schema_in_stock(offer: dict[str, Any]) -> bool:
    return str(offer.get("availability", "")).rstrip("/").lower().endswith("instock")


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


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
