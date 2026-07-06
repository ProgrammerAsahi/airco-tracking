from __future__ import annotations

from abc import abstractmethod

from bs4 import BeautifulSoup, Tag

from ...fetch import Fetcher
from ...models import Product
from ..base import enrich_available_btu


class CreateCategoryAdapter:
    """Shared Create storefront flow.

    Create's NL and FR storefronts use the same product-card DOM and the same
    "fetch category -> parse cards -> deduplicate variants -> enrich BTU"
    lifecycle. Language-specific product filtering and delivery parsing stay in
    the country adapter.
    """

    site: str
    category_url: str
    empty_message: str

    def __init__(self, fetcher: Fetcher) -> None:
        self.fetcher = fetcher

    def fetch_products(self) -> list[Product]:
        soup = BeautifulSoup(self.fetcher.get(self.category_url), "html.parser")
        cards = soup.select(".c-product-card")
        if not cards:
            raise RuntimeError(self.empty_message)

        products: dict[str, Product] = {}
        for card in cards:
            product = self.parse_card(card, self.category_url)
            if product is None:
                continue
            previous = products.get(product.url)
            if previous is None or lower_price(product, previous):
                products[product.url] = product
        return enrich_available_btu(self.fetcher, list(products.values()))

    @abstractmethod
    def parse_card(self, card: Tag, page_url: str) -> Product | None:
        """Return a product for one Create card, or None if out of scope."""


def lower_price(candidate: Product, current: Product) -> bool:
    if candidate.price_eur is None:
        return False
    return current.price_eur is None or candidate.price_eur < current.price_eur

