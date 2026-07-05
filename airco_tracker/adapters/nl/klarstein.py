from __future__ import annotations

from bs4 import BeautifulSoup

from ...models import Product
from ..base import Adapter, canonical_url, clean_text, parse_btu, parse_price


class KlarsteinAdapter(Adapter):
    site = "Klarstein"
    urls = (
        "https://www.klarstein.nl/Airconditioning/Airco/Mobiele-airco/"
        "?ldtype=infogrid&_artperpage=96",
    )

    def parse(self, soup: BeautifulSoup, page_url: str) -> list[Product]:
        products: dict[str, Product] = {}
        for card in soup.select("form.productTeaser"):
            link = card.select_one("a.card-product__content-title[href]")
            if link is None:
                continue
            name = clean_text(link)
            if not _is_airconditioner(name):
                continue
            url = canonical_url(page_url, str(link.get("href", "")))
            stock = str(card.get("data-stock", "")).strip().lower()
            delivery_node = card.select_one(".card-product__content-label")
            delivery = clean_text(delivery_node) if delivery_node else ""
            available = stock in {"in-stock", "instock", "available"}
            products[url] = Product(
                site=self.site,
                name=name,
                url=url,
                available=available,
                price_eur=parse_price(clean_text(card)),
                delivery=delivery or ("Direct leverbaar" if available else "Niet beschikbaar"),
                btu=parse_btu(name),
            )
        return list(products.values())


def _is_airconditioner(name: str) -> bool:
    lower = name.lower()
    excluded = ("aircooler", "luchtkoeler", "raamafdichting", "slang", "afstandsbediening")
    return (
        "mobiele airco" in lower or "mobiele airconditioner" in lower
    ) and not any(term in lower for term in excluded)
