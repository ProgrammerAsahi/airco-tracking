from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlencode

from ...fetch import Fetcher
from ...models import Product
from ..base import is_presale_delivery, parse_btu, parse_cooling_watts_btu
from .common import is_real_air_conditioner_fr, parse_float


class TrotecFranceAdapter:
    site = "Trotec France"
    search_url = "https://fr.trotec.com/shop/catalogsearch/result/?q=climatiseur"
    query_url = "https://{app_id}-dsn.algolia.net/1/indexes/*/queries"

    def __init__(self, fetcher: Fetcher) -> None:
        self.fetcher = fetcher

    def fetch_products(self) -> list[Product]:
        config = _algolia_config(self.fetcher.get(self.search_url))
        app_id = _required_string(config, "applicationId")
        api_key = _required_string(config, "apiKey")
        index_name = _required_string(config, "baseIndexName") + "_products"
        params = urlencode({"query": "climatiseur", "hitsPerPage": 150, "page": 0})
        response = self.fetcher.session.post(
            self.query_url.format(app_id=app_id.lower()),
            headers={
                "Content-Type": "application/json",
                "X-Algolia-Application-Id": app_id,
                "X-Algolia-API-Key": api_key,
            },
            json={"requests": [{"indexName": index_name, "params": params}]},
            timeout=self.fetcher.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        try:
            hits = payload["results"][0]["hits"]
        except (KeyError, TypeError, IndexError):
            raise RuntimeError("Trotec France search returned an invalid response")
        products: dict[str, Product] = {}
        for hit in hits:
            product = _parse_hit(hit)
            if product is not None:
                products[product.url] = product
        if not products:
            raise RuntimeError("Trotec France search returned no air conditioners")
        return list(products.values())


def _algolia_config(page: str) -> dict[str, Any]:
    marker = "window.algoliaConfig = "
    start = page.find(marker)
    if start == -1:
        raise RuntimeError("Trotec France page did not contain public search settings")
    start += len(marker)
    end = _json_object_end(page, start)
    try:
        data = json.loads(page[start:end])
    except json.JSONDecodeError as exc:
        raise RuntimeError("Trotec France search settings were invalid") from exc
    if not isinstance(data, dict):
        raise RuntimeError("Trotec France search settings were invalid")
    return data


def _json_object_end(text: str, start: int) -> int:
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index + 1
    raise RuntimeError("Trotec France search settings were incomplete")


def _parse_hit(hit: dict[str, Any]) -> Product | None:
    if not isinstance(hit, dict):
        return None
    name = str(hit.get("name", "")).strip()
    url = str(hit.get("url", "")).strip()
    details = _details(hit)
    if not name or not url or not _is_trotec_air_conditioner(name, details):
        return None
    status = str(hit.get("availability_status", "")).strip()
    sold_out = str(hit.get("sold_out", "")).strip().casefold() == "oui"
    lower_status = status.casefold()
    immediate = not sold_out and lower_status in {"en stock", "stock limité", "stock limite"}
    presale = not sold_out and is_presale_delivery(status)
    return Product(
        site="Trotec France",
        name=name,
        url=url,
        available=immediate or presale,
        price_eur=_price(hit.get("price")),
        delivery=status or None,
        btu=parse_btu(f"{name} {details}") or parse_cooling_watts_btu(details),
        presale=presale,
    )


def _is_trotec_air_conditioner(name: str, details: str) -> bool:
    name_lower = name.casefold()
    if not is_real_air_conditioner_fr(name, details):
        return False
    # Trotec's Algolia result set also contains accessories and spare parts
    # whose category path includes "Climatiseur mobile". Require the product
    # name itself to be an air conditioner; category text alone is not enough.
    if "climatiseur" not in name_lower:
        return False
    accessory_prefixes = (
        "adaptateur",
        "airlock",
        "anneaux",
        "bouchon",
        "buse",
        "câble",
        "cable",
        "capteur",
        "collier",
        "échangeur",
        "echangeur",
        "passage",
        "panneau",
        "rail",
        "rallonge",
        "roue",
        "tuyau",
    )
    return not name_lower.startswith(accessory_prefixes)


def _details(hit: dict[str, Any]) -> str:
    values: list[str] = []
    for key in (
        "main_characteristic_1_value",
        "main_characteristic_2_value",
        "main_characteristic_3_value",
        "cooling_capacity_range_idx",
    ):
        value = hit.get(key)
        if value:
            values.append(str(value))
    categories = hit.get("categories_without_path")
    if isinstance(categories, list):
        values.extend(str(item) for item in categories)
    return " ".join(values)


def _price(value: Any) -> float | None:
    try:
        return parse_float(value["EUR"]["default"])
    except (KeyError, TypeError):
        return None


def _required_string(mapping: dict[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"Trotec France search settings did not contain {key}")
    return value
