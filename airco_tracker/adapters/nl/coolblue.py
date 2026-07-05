from __future__ import annotations

from bs4 import BeautifulSoup

from ...models import Product
from ..base import Adapter, canonical_url, clean_text, parse_btu, parse_price, product_context, product_name


class CoolblueAdapter(Adapter):
    site = "Coolblue"
    urls = ("https://www.coolblue.nl/mobiele-aircos",)
    _markers = (
        "tijdelijk uitverkocht",
        "niet leverbaar",
        "morgen bezorgd",
        "op voorraad",
        "in winkelwagen",
    )

    def parse(self, soup: BeautifulSoup, page_url: str) -> list[Product]:
        products: dict[str, Product] = {}
        for link in soup.select('a[href*="/product/"]'):
            href = str(link.get("href", ""))
            if not href.endswith(".html"):
                continue
            url = canonical_url(page_url, href)
            if url in products:
                continue
            card = product_context(link, "/product/", self._markers)
            text = clean_text(card)
            lower = text.lower()
            unavailable = "tijdelijk uitverkocht" in lower or "niet leverbaar" in lower
            available = not unavailable and any(
                marker in lower for marker in ("morgen bezorgd", "op voorraad", "in winkelwagen")
            )
            products[url] = Product(
                site=self.site,
                name=product_name(card, href),
                url=url,
                available=available,
                price_eur=parse_price(text),
                delivery="Tijdelijk uitverkocht" if unavailable else _delivery_line(text),
                btu=parse_btu(text),
            )
        return list(products.values())


def _delivery_line(text: str) -> str | None:
    lower = text.lower()
    for phrase in ("Morgen bezorgd", "Op voorraad"):
        if phrase.lower() in lower:
            return phrase
    return None
