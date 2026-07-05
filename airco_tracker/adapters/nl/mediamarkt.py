from __future__ import annotations

from bs4 import BeautifulSoup

from ...models import Product
from ..base import Adapter, canonical_url, clean_text, parse_btu, parse_price, product_context, product_name


class MediaMarktAdapter(Adapter):
    site = "MediaMarkt"
    urls = ("https://www.mediamarkt.nl/nl/search.html?query=mobiele%20airco",)
    _markers = (
        "online op voorraad",
        "helaas geen bezorging mogelijk",
        "morgen in huis",
        "ik wil bestellen",
    )

    def parse(self, soup: BeautifulSoup, page_url: str) -> list[Product]:
        products: dict[str, Product] = {}
        for link in soup.select('a[href*="/nl/product/"]'):
            href = str(link.get("href", ""))
            if not href.endswith(".html"):
                continue
            url = canonical_url(page_url, href)
            if url in products:
                continue
            card = product_context(link, "/nl/product/", self._markers)
            text = clean_text(card)
            name = product_name(card, href)
            if "add-on battery" in name.lower():
                continue
            lower = text.lower()
            available = (
                "online op voorraad" in lower
                and "helaas geen bezorging mogelijk" not in lower
            )
            delivery = "Online op voorraad" if available else "Geen online bezorging"
            products[url] = Product(
                site=self.site,
                name=name,
                url=url,
                available=available,
                price_eur=parse_price(text),
                delivery=delivery,
                btu=parse_btu(text),
            )
        return list(products.values())
