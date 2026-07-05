from __future__ import annotations

import json
import re
from typing import Any

from bs4 import BeautifulSoup

from ...models import Product
from ..base import Adapter, canonical_url, parse_btu, parse_watt_rating_btu


class PraxisAdapter(Adapter):
    site = "Praxis"
    urls = (
        "https://www.praxis.nl/verwarmingen-airco-s/airco-s/"
        "mobiele-airco-s/he057/",
    )

    def parse(self, soup: BeautifulSoup, page_url: str) -> list[Product]:
        state = _preloaded_state(soup)
        product_data = state.get("products")
        if not isinstance(product_data, dict):
            raise RuntimeError("Praxis category did not contain product data")
        items = product_data.get("collection")
        quantity = product_data.get("quantity")
        if not isinstance(items, list) or not isinstance(quantity, int):
            raise RuntimeError("Praxis category returned an invalid product collection")
        products: dict[str, Product] = {}
        for item in items:
            product = _parse_product(item, page_url)
            if product is not None:
                products[product.url] = product
        return list(products.values())


def _preloaded_state(soup: BeautifulSoup) -> dict[str, Any]:
    marker = '__PRELOADED_STATE_listerFragment__'
    for script in soup.find_all("script"):
        text = script.string or script.get_text()
        if marker not in text:
            continue
        match = re.search(r"=\s*(\{.*\})\s*;?\s*$", text, re.DOTALL)
        if match is None:
            break
        # The JavaScript payload is JSON except for safe hexadecimal escapes
        # such as \x3c in translated HTML snippets.
        raw = re.sub(
            r"\\x([0-9a-fA-F]{2})",
            lambda found: chr(int(found.group(1), 16)),
            match.group(1),
        )
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Praxis category data was invalid") from exc
        if isinstance(data, dict):
            return data
    raise RuntimeError("Praxis category did not contain preloaded product data")


def _parse_product(item: Any, page_url: str) -> Product | None:
    if not isinstance(item, dict):
        return None
    name = str(item.get("title", "")).strip()
    href = str(item.get("link", "")).strip()
    if not name or not href or not _is_portable_airco(name):
        return None
    status = str(item.get("availabilityStatus", "")).strip()
    status_multiple = item.get("availabilityStatusMultiple")
    details = [str(value).strip() for value in status_multiple] if isinstance(status_multiple, list) else []
    modes = item.get("deliveryModes")
    mode_codes = [
        str(mode.get("code", "")).upper()
        for mode in modes
        if isinstance(mode, dict)
    ] if isinstance(modes, list) else []
    has_home_delivery = any(
        code and code != "PICKUP" and "PICKUPPOINT" not in code
        for code in mode_codes
    )
    availability_text = " ".join([status, *details]).lower()
    blocked = any(
        marker in availability_text
        for marker in (
            "binnenkort verkrijgbaar",
            "tijdelijk niet beschikbaar",
            "uitverkocht",
            "bezorging niet beschikbaar",
            "alleen beschikbaar in winkels",
            "bestel & haal op",
        )
    )
    disabled = bool(item.get("discontinued")) or bool(
        item.get("disableStatus", {}).get("isDisabled")
        if isinstance(item.get("disableStatus"), dict)
        else False
    )
    available = has_home_delivery and not blocked and not disabled
    return Product(
        site="Praxis",
        name=name,
        url=canonical_url(page_url, href),
        available=available,
        price_eur=_price(item.get("regular")),
        delivery="; ".join([status, *details]) or None,
        btu=parse_btu(name) or parse_watt_rating_btu(name),
    )


def _is_portable_airco(name: str) -> bool:
    lower = name.lower()
    excluded = (
        "aircooler",
        "luchtkoeler",
        "ventilator",
        "split airco",
        "mini-split",
        "mini split",
    )
    positive = ("airco", "airconditioning", "aircondition")
    return not any(term in lower for term in excluded) and any(
        term in lower for term in positive
    )


def _price(value: Any) -> float | None:
    if not isinstance(value, dict):
        return None
    try:
        return float(value["price"])
    except (KeyError, TypeError, ValueError):
        return None
