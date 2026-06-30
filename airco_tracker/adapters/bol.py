from __future__ import annotations

import re

from bs4 import BeautifulSoup

from ..models import Product
from .base import Adapter, canonical_url, clean_text, parse_btu, parse_price, product_context, product_name


DELIVERY_RE = re.compile(
    r"(Voor \d{1,2}:\d{2} uur besteld, [^.]{0,35}in huis|Uiterlijk [^.]{0,30}in huis|Morgen in huis|Vandaag bezorgd)",
    re.I,
)


class BolAdapter(Adapter):
    site = "bol.com"
    urls = ("https://www.bol.com/nl/nl/s/?searchtext=mobiele+airco",)
    _markers = ("op voorraad", "in huis", "niet leverbaar")
    _excluded = (
        "aircooler",
        "ventilator",
        "raamafdichting",
        "raamkit",
        "afvoerslang",
        "beschermhoes",
        "add-on battery",
        "onderdeel",
    )

    def parse(self, soup: BeautifulSoup, page_url: str) -> list[Product]:
        products: dict[str, Product] = {}
        for link in soup.select('a[href*="/nl/nl/p/"]'):
            href = str(link.get("href", ""))
            if not re.search(r"/\d{10,}/?$", href.split("?", 1)[0]):
                continue
            url = canonical_url(page_url, href)
            if url in products:
                continue
            card = product_context(link, "/nl/nl/p/", self._markers)
            text = clean_text(card)
            name = product_name(card, href)
            btu = parse_btu(text)
            if not self._is_real_airco(name, text, btu):
                continue
            lower = text.lower()
            delivery_match = DELIVERY_RE.search(text)
            available = "niet leverbaar" not in lower and (
                "op voorraad" in lower or delivery_match is not None
            )
            products[url] = Product(
                site=self.site,
                name=name,
                url=url,
                available=available,
                price_eur=parse_price(text),
                delivery=delivery_match.group(1).strip() if delivery_match else ("Op voorraad" if available else None),
                btu=btu,
            )
        return list(products.values())

    def _is_real_airco(self, name: str, text: str, btu: int | None) -> bool:
        lower_name = name.lower()
        if any(term in lower_name for term in self._excluded):
            return False
        named_as_airco = any(
            term in lower_name
            for term in ("mobiele airco", "mobiele airconditioner", "portable airco", "air conditioner")
        )
        has_exhaust = "werkt met afvoerslang naar buiten" in text.lower()
        return named_as_airco and (has_exhaust or (btu is not None and btu >= 2000))
