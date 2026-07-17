from __future__ import annotations

from bs4 import BeautifulSoup

from ...models import Product
from ..base import Adapter, canonical_url, clean_text, parse_btu, parse_price
from ..shared.magento import stock_quantity_from_qty_class


class CostwayAdapter(Adapter):
    """Costway NL — Magento server-rendered category page."""

    site = "Costway NL"
    urls = (
        "https://nl.costway.com/huishoudelijke-apparaten/klimaatbeheersing/aircos.html",
    )

    def parse(self, soup: BeautifulSoup, page_url: str) -> list[Product]:
        products: dict[str, Product] = {}
        unknown_stock = 0
        for card in soup.select("li.item.product"):
            link = card.select_one("a.product-item-link")
            if link is None:
                continue
            href = str(link.get("href", ""))
            name = clean_text(link)
            if not href or not _is_portable_airco(name):
                continue
            url = canonical_url(page_url, href)
            if url in products:
                continue
            text = clean_text(card)
            qty = stock_quantity_from_qty_class(card)
            if qty is None:
                # Fail closed at product level: without the qty-N marker the
                # stock state is unknown and must not be reported as available.
                unknown_stock += 1
            products[url] = Product(
                site=self.site,
                name=name,
                url=url,
                available=qty is not None and qty > 0,
                price_eur=parse_price(text),
                delivery=_delivery(qty),
                btu=parse_btu(text),
            )
        if products and unknown_stock == len(products):
            # Every classified card lacked the qty-N stock marker, so stock can
            # no longer be verified at all: treat the page as markup drift.
            raise RuntimeError(
                f"{self.site}: qty stock marker missing on every product card; "
                "site markup may have changed"
            )
        return list(products.values())


def _delivery(qty: int | None) -> str:
    if qty is None:
        return "Voorraad onbekend"
    return "Op voorraad" if qty > 0 else "Uitverkocht"


def _is_portable_airco(name: str) -> bool:
    lower = name.lower()
    excluded = (
        "aircooler",
        "luchtkoeler",
        "ventilator",
        "split-airconditioner",
        "split airco",
        "mini-split",
        "mini split",
        "raamafdichting",
    )
    if any(term in lower for term in excluded):
        return False
    return "airconditioning" in lower or "airconditioner" in lower or "airco" in lower
