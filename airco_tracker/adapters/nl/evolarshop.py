from __future__ import annotations

import re
from typing import Any

from ..base import enrich_available_btu, parse_btu
from ...fetch import Fetcher
from ...models import Product


class EvolarshopAdapter:
    """Evolarshop — Hyva/Magento storefront rendered through the public Nosto search API.

    The category page itself is client-rendered via Alpine.js/Nosto, so the tracker
    queries the same public GraphQL endpoint the browser uses. No credentials are
    required; the account id is read from the page's Nosto include script.
    """

    site = "Evolarshop"
    category_url = "https://www.evolarshop.nl/airco-s/mobiele-airco"
    search_url = "https://search.nosto.com/v1/graphql"
    category_path = "Airco's/Mobiele Airco"
    _account_re = re.compile(r"connect\.nosto\.com/include/([a-z0-9]+)", re.I)

    def __init__(self, fetcher: Fetcher) -> None:
        self.fetcher = fetcher

    def fetch_products(self) -> list[Product]:
        account_id = self._account_id(self.fetcher.get(self.category_url))
        hits = self._search_hits(account_id)
        products: dict[str, Product] = {}
        for hit in hits:
            product = _parse_hit(hit)
            if product is not None:
                products[product.url] = product
        if not products:
            raise RuntimeError("Evolarshop: Nosto search returned no products")
        return enrich_available_btu(self.fetcher, list(products.values()))

    def _account_id(self, page: str) -> str:
        match = self._account_re.search(page)
        if not match:
            raise RuntimeError("Evolarshop page did not contain a Nosto account id")
        return match.group(1)

    def _search_hits(self, account_id: str) -> list[dict[str, Any]]:
        query = (
            "query ($products: InputSearchProducts) {"
            f'  search (accountId: "{account_id}", products: $products) {{'
            "    products { hits { productId name url price available availability } }"
            "  }"
            "}"
        )
        variables = {
            "products": {
                "categoryPath": self.category_path,
                "size": 100,
                "from": 0,
                "variationId": "NOT LOGGED IN",
            }
        }
        response = self.fetcher.session.post(
            self.search_url,
            json={"query": query, "variables": variables},
            timeout=self.fetcher.timeout,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        response.raise_for_status()
        payload = response.json()
        try:
            return payload["data"]["search"]["products"]["hits"]
        except (KeyError, TypeError):
            raise RuntimeError("Evolarshop Nosto search returned an invalid response")


def _parse_hit(hit: dict[str, Any]) -> Product | None:
    if not isinstance(hit, dict):
        return None
    name = str(hit.get("name", "")).strip()
    url = str(hit.get("url", "")).strip()
    if not name or not url or not _is_real_airco(name):
        return None
    available = bool(hit.get("available"))
    return Product(
        site="Evolarshop",
        name=name,
        url=url,
        available=available,
        price_eur=_price(hit.get("price")),
        delivery=str(hit.get("availability", "")).strip() or None,
        btu=parse_btu(name),
    )


def _is_real_airco(name: str) -> bool:
    lower = name.lower()
    excluded = (
        "aircooler",
        "luchtkoeler",
        "ventilator",
        "zonder afvoerslang",  # no exhaust hose → not a compressor unit
        "raamafdichting",
    )
    return not any(term in lower for term in excluded) and "airco" in lower


def _price(value: Any) -> float | None:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None
