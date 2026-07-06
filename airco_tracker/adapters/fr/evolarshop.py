from __future__ import annotations

import re
from typing import Any

from ...fetch import Fetcher
from ...models import Product
from ..base import is_presale_delivery, parse_btu, parse_cooling_watts_btu
from .common import custom_fields, is_real_air_conditioner_fr, parse_float


class EvolarshopFranceAdapter:
    """Evolarshop France via the public Nosto category search endpoint."""

    site = "Evolarshop France"
    category_url = "https://www.evolarshop.fr/climatiseurs/climatiseur-mobile"
    search_url = "https://search.nosto.com/v1/graphql"
    category_path = "Climatisation/Climatiseur Portable"
    _account_re = re.compile(r"connect\.nosto\.com/include/([a-z0-9-]+)", re.I)

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
            raise RuntimeError("Evolarshop France: Nosto search returned no products")
        return list(products.values())

    def _account_id(self, page: str) -> str:
        match = self._account_re.search(page)
        if not match:
            raise RuntimeError("Evolarshop France page did not contain a Nosto account id")
        return match.group(1)

    def _search_hits(self, account_id: str) -> list[dict[str, Any]]:
        query = (
            "query ($products: InputSearchProducts) {"
            f'  search (accountId: "{account_id}", products: $products) {{'
            "    products {"
            "      hits { productId name url price available availability customFields { key value } }"
            "    }"
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
            raise RuntimeError("Evolarshop France Nosto search returned an invalid response")


def _parse_hit(hit: dict[str, Any]) -> Product | None:
    if not isinstance(hit, dict):
        return None
    name = str(hit.get("name", "")).strip()
    url = str(hit.get("url", "")).strip()
    fields = custom_fields(hit)
    details = " ".join(fields.get(key, "") for key in ("product_card_subtitle", "product_card_subtitle_ex_html"))
    if not name or not url or not is_real_air_conditioner_fr(name, details):
        return None
    delivery = fields.get("product_card_usp") or str(hit.get("availability", "")).strip()
    presale = is_presale_delivery(delivery)
    available = bool(hit.get("available")) or presale
    return Product(
        site="Evolarshop France",
        name=name,
        url=url,
        available=available,
        price_eur=parse_float(hit.get("price")),
        delivery=delivery or None,
        btu=parse_btu(f"{name} {details}") or parse_cooling_watts_btu(details),
        presale=presale,
    )
