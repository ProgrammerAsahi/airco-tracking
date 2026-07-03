from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlencode

from .base import enrich_available_btu, parse_btu, parse_cooling_watts_btu
from ..fetch import Fetcher
from ..models import Product


class ElectroWorldAdapter:
    """Read Electro World's public, browser-facing Algolia product index."""

    site = "Electro World"
    category_url = (
        "https://www.electroworld.nl/huishouden-wonen/klimaatbeheersing/"
        "aircos/mobiele-aircos?page=1"
    )
    query_url = "https://{app_id}-dsn.algolia.net/1/indexes/*/queries"

    def __init__(self, fetcher: Fetcher) -> None:
        self.fetcher = fetcher

    def fetch_products(self) -> list[Product]:
        page = self.fetcher.get(self.category_url)
        config = _algolia_config(page)
        app_id = _required_string(config, "applicationId")
        api_key = _required_string(config, "apiKey")
        base_index = _required_string(config, "baseIndexName")
        request = config.get("request")
        if not isinstance(request, dict):
            raise RuntimeError("Electro World page did not contain category search settings")
        category_path = _required_string(request, "path")
        level = _positive_int(request.get("level"), default=3)
        params = urlencode(
            {
                "query": "",
                "hitsPerPage": 100,
                "page": 0,
                "facetFilters": json.dumps(
                    [f"categories.level{level}:{category_path}"],
                    ensure_ascii=False,
                ),
            }
        )
        response = self.fetcher.session.post(
            self.query_url.format(app_id=app_id.lower()),
            headers={
                "Content-Type": "application/json",
                "X-Algolia-Application-Id": app_id,
                "X-Algolia-API-Key": api_key,
            },
            json={
                "requests": [
                    {
                        "indexName": f"{base_index}_products",
                        "params": params,
                    }
                ]
            },
            timeout=self.fetcher.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        results = payload.get("results")
        if not isinstance(results, list) or not results or not isinstance(results[0], dict):
            raise RuntimeError("Electro World search returned an invalid response")
        hits = results[0].get("hits")
        if not isinstance(hits, list):
            raise RuntimeError("Electro World search response did not contain products")
        products = [product for hit in hits if (product := _parse_hit(hit)) is not None]
        unique = list({product.url: product for product in products}.values())
        return enrich_available_btu(self.fetcher, unique)


def _algolia_config(page: str) -> dict[str, Any]:
    match = re.search(
        r"window\.algoliaConfig\s*=\s*JSON\.parse\('(?P<config>.*?)'\)",
        page,
        re.DOTALL,
    )
    if match is None:
        raise RuntimeError("Electro World page did not contain public search settings")
    try:
        decoded = json.loads(f'"{match.group("config")}"')
        config = json.loads(decoded)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Electro World search settings were invalid") from exc
    if not isinstance(config, dict):
        raise RuntimeError("Electro World search settings were invalid")
    return config


def _parse_hit(hit: Any) -> Product | None:
    if not isinstance(hit, dict):
        return None
    name = str(hit.get("name", "")).strip()
    url = str(hit.get("url", "")).strip()
    if not name or not url:
        return None
    usps = hit.get("product_usps")
    details = " ".join(str(item) for item in usps) if isinstance(usps, list) else ""
    available = _as_bool(hit.get("in_stock_frontend", hit.get("in_stock", False)))
    return Product(
        site="Electro World",
        name=name,
        url=url,
        available=available,
        price_eur=_price(hit.get("price")),
        delivery="Online op voorraad" if available else "Niet online op voorraad",
        btu=parse_btu(f"{name} {details}") or parse_cooling_watts_btu(details),
    )


def _price(value: Any) -> float | None:
    try:
        return float(value["EUR"]["default"])
    except (KeyError, TypeError, ValueError):
        return None


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return bool(value)


def _required_string(mapping: dict[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"Electro World search settings did not contain {key}")
    return value


def _positive_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default
