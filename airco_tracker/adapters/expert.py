from __future__ import annotations

import html
import json
from typing import Any

from bs4 import BeautifulSoup

from ..fetch import Fetcher
from ..models import Product
from .base import enrich_available_btu, parse_btu, parse_cooling_watts_btu


class ExpertAdapter:
    """Read Expert's server-rendered category payload.

    Expert separates local-shop availability from online ordering.  The
    ``not_saleable`` flag is therefore intentionally decisive: shop-only stock
    must never trigger a delivery alert.
    """

    site = "Expert.nl"
    category_url = (
        "https://www.expert.nl/wonen/klimaat/airconditioners.html/"
        "_type-airco-Airco-mobiel"
    )

    def __init__(self, fetcher: Fetcher) -> None:
        self.fetcher = fetcher

    def fetch_products(self) -> list[Product]:
        soup = BeautifulSoup(self.fetcher.get(self.category_url), "html.parser")
        category = soup.find("catalog-category-view")
        raw = category.get(":catalog-data") if category else None
        if not isinstance(raw, str) or not raw:
            raise RuntimeError("Expert category did not contain catalog data")
        try:
            payload = json.loads(html.unescape(raw))
        except json.JSONDecodeError as exc:
            raise RuntimeError("Expert catalog data was invalid") from exc
        items = payload.get("items")
        if not isinstance(items, list):
            raise RuntimeError("Expert catalog data did not contain products")
        products = [_parse_product(item) for item in items]
        parsed = [product for product in products if product is not None]
        return enrich_available_btu(self.fetcher, parsed)


def _parse_product(item: Any) -> Product | None:
    if not isinstance(item, dict):
        return None
    name = str(item.get("name", "")).strip()
    details = " ".join(
        str(item.get(key, ""))
        for key in ("display_name", "description", "specifications", "usps")
    )
    lower = f"{name} {details}".lower()
    if not name or not _is_portable_airco(lower):
        return None
    url = str(item.get("url") or item.get("url_key") or "").strip()
    if not url:
        return None
    if url.startswith("/"):
        url = "https://www.expert.nl" + url
    available = (
        item.get("not_saleable") is False
        and _truthy(item.get("status_in_stock"))
        and _truthy(item.get("in_stock"))
    )
    return Product(
        site="Expert.nl",
        name=name,
        url=url,
        available=available,
        price_eur=_float(item.get("final_price_incl_tax") or item.get("price")),
        delivery="Online bestelbaar" if available else "Niet online bestelbaar",
        btu=parse_btu(f"{name} {details}") or parse_cooling_watts_btu(details),
    )


def _is_portable_airco(text: str) -> bool:
    excluded = (
        "aircooler",
        "luchtkoeler",
        "window-way",
        "raamkit",
        "raamafdichting",
        "afvoerslang",
    )
    if any(term in text for term in excluded):
        return False
    return "mobiele airco" in text or "mobiele aircondition" in text


def _truthy(value: Any) -> bool:
    return value is True or value == 1 or str(value).lower() in {"1", "true"}


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
