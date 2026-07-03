from __future__ import annotations

import json
import re
from typing import Any

from bs4 import BeautifulSoup

from ..models import Product
from .base import Adapter, canonical_url, enrich_available_btu, parse_btu


class WehkampAdapter(Adapter):
    site = "Wehkamp"
    urls = ("https://www.wehkamp.nl/huishoudelijke-apparatuur-aircos/",)

    def fetch_products(self) -> list[Product]:
        # Wehkamp removes sold-out products from the category. An explicit
        # products=[] and total=0 therefore is a valid, useful response: a
        # restock will reappear as a first-seen available product.
        page_url = self.urls[0]
        soup = BeautifulSoup(self.fetcher.get(page_url), "html.parser")
        return enrich_available_btu(self.fetcher, self.parse(soup, page_url))

    def parse(self, soup: BeautifulSoup, page_url: str) -> list[Product]:
        data = _initial_data(soup)
        items = data.get("products")
        total = data.get("total")
        if not isinstance(items, list) or not isinstance(total, int):
            raise RuntimeError("Wehkamp category did not contain a product result")
        products: dict[str, Product] = {}
        for item in items:
            product = _parse_product(item, page_url)
            if product is not None:
                products[product.url] = product
        return list(products.values())


def _initial_data(soup: BeautifulSoup) -> dict[str, Any]:
    prefix = "window.__INITIAL_DATA__="
    for script in soup.find_all("script"):
        text = script.string or script.get_text()
        if not text.startswith(prefix):
            continue
        raw = text[len(prefix) :].rstrip(";")
        try:
            data = json.loads(re.sub(r"\bundefined\b", "null", raw))
        except json.JSONDecodeError as exc:
            raise RuntimeError("Wehkamp category data was invalid") from exc
        if isinstance(data, dict):
            return data
    raise RuntimeError("Wehkamp category did not contain initial product data")


def _parse_product(item: Any, page_url: str) -> Product | None:
    if not isinstance(item, dict):
        return None
    name = str(item.get("originalTitle") or item.get("title") or "").strip()
    href = str(item.get("pdpUrl", "")).strip()
    btu = parse_btu(name)
    if not name or not href or not _is_portable_airco(name, btu):
        return None
    delivery = str(item.get("availabilityText") or "").strip()
    stock = _nonnegative_int(item.get("itemsInStock"))
    delivery_lower = delivery.lower()
    unavailable = "uitverkocht" in delivery_lower
    # Multi-week lead times (e.g. "Binnen 3-5 weken leverbaar") are preorders,
    # not orderable stock, and must not trigger an alert (AGENTS.md).
    long_lead_time = "weken" in delivery_lower
    available = not unavailable and not long_lead_time and (stock > 0 or bool(delivery))
    return Product(
        site="Wehkamp",
        name=name,
        url=canonical_url(page_url, href),
        available=available,
        price_eur=_price_in_cents(item.get("pricing")),
        delivery=delivery or ("Op voorraad" if available else "Uitverkocht"),
        btu=btu,
    )


def _is_portable_airco(name: str, btu: int | None) -> bool:
    lower = name.lower()
    # "split" units are fixed-installation; "monoblock" (single-unit) is the
    # genuine portable compressor form factor and must NOT be excluded.
    if any(term in lower for term in ("aircooler", "luchtkoeler", "ventilator", "split")):
        return False
    return (
        "airconditioner" in lower
        or "mobiele airco" in lower
        or ("airco" in lower and btu is not None)
    )


def _price_in_cents(value: Any) -> float | None:
    if not isinstance(value, dict):
        return None
    try:
        return int(value["price"]) / 100
    except (KeyError, TypeError, ValueError):
        return None


def _nonnegative_int(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, parsed)
