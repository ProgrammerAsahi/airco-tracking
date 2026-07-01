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
            products[url] = Product(
                site=self.site,
                name=name,
                url=url,
                # A product that can be ordered for delivery in several weeks
                # is retained in output, but is not treated as immediate stock.
                available=delivery.lower() == "op voorraad",
                price_eur=price,
                delivery=delivery or None,
                btu=parse_btu(text),
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


def _nested_float(data: dict[str, Any], *keys: str) -> float | None:
    value: Any = data
    try:
        for key in keys:
            value = value[key]
        return round(float(value), 2)
    except (KeyError, TypeError, ValueError):
        return None
