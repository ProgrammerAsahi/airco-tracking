from __future__ import annotations

from bs4 import BeautifulSoup

from ...models import Product
from ..base import Adapter, canonical_url, clean_text, parse_btu, parse_price


class EpAdapter(Adapter):
    site = "EP.nl"
    urls = ("https://www.ep.nl/producten/categorie-mobiele-airco/",)

    def parse(self, soup: BeautifulSoup, page_url: str) -> list[Product]:
        products: dict[str, Product] = {}
        for card in soup.select(".lister-card"):
            link = card.select_one("a.lister-card__title[href]")
            if link is None:
                continue
            href = str(link.get("href", ""))
            url = canonical_url(page_url, href)
            text = clean_text(card)
            stock = card.select_one(".stock")
            stock_text = clean_text(stock) if stock is not None else ""
            stock_classes = set(stock.get("class", [])) if stock is not None else set()
            available = "is-green" in stock_classes and "uitverkocht" not in stock_text.lower()
            products[url] = Product(
                site=self.site,
                name=clean_text(link),
                url=url,
                available=available,
                price_eur=parse_price(text),
                delivery=stock_text or None,
                btu=parse_btu(text),
            )
        return list(products.values())
