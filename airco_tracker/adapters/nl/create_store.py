from __future__ import annotations

import re

from bs4 import Tag

from ...models import Product
from ..base import canonical_url, clean_text, parse_btu, parse_price
from ..shared.create_store import CreateCategoryAdapter


class CreateStoreAdapter(CreateCategoryAdapter):
    site = "Create NL"
    category_url = "https://www.create-store.com/nl/3939-kopen-mobiele-airco"
    empty_message = "Create category contained no portable air conditioners"

    def parse_card(self, card: Tag, page_url: str) -> Product | None:
        return _parse_card(card, page_url)


def _parse_card(card: Tag, page_url: str) -> Product | None:
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
    available = presale or "verzending binnen" in lower
    price_node = card.select_one(".c-product-card__price--final")
    return Product(
        site="Create NL",
        name=name,
        url=canonical_url(page_url, str(link.get("href", ""))),
        available=available,
        price_eur=parse_price(clean_text(price_node)) if price_node else None,
        delivery=delivery,
        btu=parse_btu(name),
        presale=presale,
    )
