from __future__ import annotations

import json
import re
from typing import Any

from bs4 import BeautifulSoup

from ..models import Product
from .base import Adapter, canonical_url, clean_text, parse_btu


class TrotecAdapter(Adapter):
    site = "Trotec"
    urls = ("https://nl.trotec.com/shop/mobiele-airco",)

    def parse(self, soup: BeautifulSoup, page_url: str) -> list[Product]:
        products: dict[str, Product] = {}
        for node in soup.select('[x-data*="availability_message"]'):
            data = _product_data(str(node.get("x-data", "")))
            if not data:
                continue
            name = str(data.get("name", "")).strip()
            if not _is_airconditioner(name):
                continue
            link = node.select_one("a.product-item-link[href]")
            if link is None:
                continue
            url = canonical_url(page_url, str(link.get("href", "")))
            delivery = str(data.get("availability_message", "")).strip()
            price = _nested_float(data, "price_range", "minimum_price", "final_price", "value")
            text = clean_text(node)
            delivery_lower = delivery.lower()
            in_stock = delivery_lower == "op voorraad"
            sold_out = "uitverkocht" in delivery_lower or "niet leverbaar" in delivery_lower
            # Products with multi-week lead times are retained as presale
            # rather than dropped, so the frontend can show them separately.
            # Sold-out products are excluded entirely (not presale, not in stock).
            presale = bool(delivery) and not in_stock and not sold_out
            products[url] = Product(
                site=self.site,
                name=name,
                url=url,
                available=in_stock or presale,
                price_eur=price,
                delivery=delivery or None,
                btu=parse_btu(text) or _model_btu(name),
                presale=presale,
            )
        return list(products.values())


def _product_data(value: str) -> dict[str, Any]:
    match = re.search(r"^\s*\{\s*product\s*:\s*(\{.*\})\s*\}\s*$", value, re.S)
    if not match:
        return {}
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _is_airconditioner(name: str) -> bool:
    lower = name.lower()
    excluded = ("wandairconditioner", "raamafdichting", "airlock", "accessoire")
    return "aircondition" in lower and not any(term in lower for term in excluded)


_MODEL_CAPACITIES = (
    (re.compile(r"\bPAC-C\s*1500\b", re.I), 5000),
    (re.compile(r"\bPAC\s*(?:2015|2016|2020|2100)\b", re.I), 7000),
    (re.compile(r"\bPAC\s*(?:2600|2620)\b", re.I), 9000),
    (re.compile(r"\bPAC\s*3000\b", re.I), 10000),
    (re.compile(r"\bPAC(?:-S)?\s*(?:3500|3501|3510)\b", re.I), 12000),
    (re.compile(r"\bPAC\s*(?:3910|4100)\b", re.I), 14000),
)


def _model_btu(name: str) -> int | None:
    for pattern, btu in _MODEL_CAPACITIES:
        if pattern.search(name):
            return btu
    return None


def _nested_float(data: dict[str, Any], *keys: str) -> float | None:
    value: Any = data
    try:
        for key in keys:
            value = value[key]
        return round(float(value), 2)
    except (KeyError, TypeError, ValueError):
        return None
