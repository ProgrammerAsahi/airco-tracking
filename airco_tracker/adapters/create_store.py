from __future__ import annotations

import re

from bs4 import BeautifulSoup

from ..fetch import Fetcher
from ..models import Product
from .base import canonical_url, clean_text, enrich_available_btu, parse_btu, parse_price


class CreateStoreAdapter:
    site = "Create NL"
    category_url = "https://www.create-store.com/nl/3939-kopen-mobiele-airco"

    def __init__(self, fetcher: Fetcher) -> None:
        self.fetcher = fetcher

    def fetch_products(self) -> list[Product]:
        soup = BeautifulSoup(self.fetcher.get(self.category_url), "html.parser")
        cards = soup.select(".c-product-card")
        if not cards:
            raise RuntimeError("Create category contained no portable air conditioners")
        products = [_parse_card(card, self.category_url) for card in cards]
        unique: dict[str, Product] = {}
        for product in products:
            if product is None:
                continue
            previous = unique.get(product.url)
            if previous is None or _lower_price(product, previous):
                unique[product.url] = product
        return enrich_available_btu(self.fetcher, list(unique.values()))


def _parse_card(card: BeautifulSoup, page_url: str) -> Product | None:
    title = card.select_one(".c-product-card__title")
    link = title.find("a") if title else None
    if title is None or link is None:
        return None
    name = clean_text(title)
    lower_name = name.lower()
    if "mobiele airco" not in lower_name or "set afzuiging" in lower_name:
        return None
    text = clean_text(card)
    lower = text.lower()
    delivery_match = re.search(r"Verzending\s+(?:binnen|vanaf)\s+[^\n]+", text, re.I)
    delivery = delivery_match.group(0).strip() if delivery_match else "Voorraadstatus onbekend"
    presale = "presale" in lower or "verzending vanaf" in lower
    available = not presale and "verzending binnen" in lower
    price_node = card.select_one(".c-product-card__price--final")
    return Product(
        site="Create NL",
        name=name,
        url=canonical_url(page_url, str(link.get("href", ""))),
        available=available,
        price_eur=parse_price(clean_text(price_node)) if price_node else None,
        delivery=delivery,
        btu=parse_btu(name),
    )


def _lower_price(candidate: Product, current: Product) -> bool:
    if candidate.price_eur is None:
        return False
    return current.price_eur is None or candidate.price_eur < current.price_eur
