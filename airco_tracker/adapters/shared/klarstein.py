from __future__ import annotations

from abc import abstractmethod

from bs4 import BeautifulSoup

from ...models import Product
from ..base import Adapter, canonical_url, clean_text


class KlarsteinCardAdapter(Adapter):
    """Shared parser for Klarstein's Oxid product teaser cards."""

    def parse(self, soup: BeautifulSoup, page_url: str) -> list[Product]:
        products: dict[str, Product] = {}
        for card in soup.select("form.productTeaser"):
            link = card.select_one("a.card-product__content-title[href]")
            if link is None:
                continue
            name = clean_text(link)
            text = clean_text(card)
            if not self.is_air_conditioner(name, text):
                continue
            url = canonical_url(page_url, str(link.get("href", "")))
            stock = str(card.get("data-stock", "")).strip().casefold()
            delivery_node = card.select_one(".card-product__content-label")
            delivery = clean_text(delivery_node) if delivery_node else ""
            available, presale = self.availability(stock=stock, delivery=delivery, text=text)
            products[url] = Product(
                site=self.site,
                name=name,
                url=url,
                available=available,
                price_eur=self.price(text),
                delivery=self.delivery_text(delivery=delivery, available=available),
                btu=self.btu(name, text),
                presale=presale,
            )
        return list(products.values())

    @abstractmethod
    def is_air_conditioner(self, name: str, text: str) -> bool:
        """Return whether a teaser represents a real portable AC."""

    @abstractmethod
    def availability(self, *, stock: str, delivery: str, text: str) -> tuple[bool, bool]:
        """Return ``(available, presale)`` for one teaser."""

    @abstractmethod
    def price(self, text: str) -> float | None:
        """Parse the current product price."""

    @abstractmethod
    def delivery_text(self, *, delivery: str, available: bool) -> str:
        """Return displayable delivery text."""

    @abstractmethod
    def btu(self, name: str, text: str) -> int | None:
        """Parse the cooling capacity."""

