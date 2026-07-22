from __future__ import annotations

import re
from abc import abstractmethod
from typing import Any

from ...fetch import Fetcher
from ...models import Product


class NostoCategoryAdapter:
    """Shared public Nosto GraphQL flow used by Evolarshop storefronts."""

    site: str
    category_url: str
    category_path: str
    search_url = "https://search.nosto.com/v1/graphql"
    hit_fields = "productId name url price available availability"
    _account_re = re.compile(r"connect\.nosto\.com/include/([a-z0-9-]+)", re.I)

    def __init__(self, fetcher: Fetcher) -> None:
        self.fetcher = fetcher

    def fetch_products(self) -> list[Product]:
        account_id = self._account_id(self.fetcher.get(self.category_url))
        hits = self._search_hits(account_id)
        products: dict[str, Product] = {}
        for hit in hits:
            product = self.parse_hit(hit)
            if product is not None:
                products[product.url] = product
        if not products:
            raise RuntimeError(f"{self.site}: Nosto search returned no products")
        return self.enrich_products(list(products.values()))

    def enrich_products(self, products: list[Product]) -> list[Product]:
        return products

    @abstractmethod
    def parse_hit(self, hit: dict[str, Any]) -> Product | None:
        """Return a product for a Nosto hit, or None if out of scope."""

    def _account_id(self, page: str) -> str:
        match = self._account_re.search(page)
        if not match:
            raise RuntimeError(f"{self.site} page did not contain a Nosto account id")
        return match.group(1)

    def _search_hits(self, account_id: str) -> list[dict[str, Any]]:
        query = (
            "query ($products: InputSearchProducts) {"
            f'  search (accountId: "{account_id}", products: $products) {{'
            f"    products {{ hits {{ {self.hit_fields} }} }}"
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
        payload = self.fetcher.request_json(
            "POST",
            self.search_url,
            json_body={"query": query, "variables": variables},
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            # Nosto search is a logically read-only GraphQL query, so a
            # bounded retry is safe even though its transport method is POST.
            retry_read_only_post=True,
            maximum_response_bytes=2 * 1024 * 1024,
        )
        try:
            return payload["data"]["search"]["products"]["hits"]
        except (KeyError, TypeError):
            raise RuntimeError(f"{self.site} Nosto search returned an invalid response")
