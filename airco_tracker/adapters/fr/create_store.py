from __future__ import annotations

import re

from bs4 import Tag

from ...models import Product
from ..base import canonical_url, clean_text, is_presale_delivery, parse_btu
from ..shared.create_store import CreateCategoryAdapter
from .common import is_real_air_conditioner_fr, parse_french_price


class CreateFranceAdapter(CreateCategoryAdapter):
    site = "Create France"
    category_url = "https://www.create-store.com/fr/3939-acheter-climatiseur-mobile"
    empty_message = "Create France category contained no portable air conditioners"

    def parse_card(self, card: Tag, page_url: str) -> Product | None:
        return _parse_card(card, page_url)


def _parse_card(card: Tag, page_url: str) -> Product | None:
    title = card.select_one(".c-product-card__title")
    link = title.find("a", href=True) if title else None
    if title is None or link is None:
        return None
    name = clean_text(title)
    text = clean_text(card)
    if not is_real_air_conditioner_fr(name, text):
        return None
    delivery = _delivery(text)
    lower = text.casefold()
    presale = is_presale_delivery(f"{delivery} {text}") or "pre-order" in lower
    available = presale or "expédition" in lower or "expedition" in lower or "livraison" in lower
    price_node = card.select_one(".c-product-card__price--final")
    return Product(
        site="Create France",
        name=name,
        url=canonical_url(page_url, str(link.get("href", ""))),
        available=available,
        price_eur=parse_french_price(clean_text(price_node) if price_node else text),
        delivery=delivery or ("Disponible" if available else "Indisponible"),
        btu=parse_btu(name),
        presale=presale,
    )


def _delivery(text: str) -> str:
    match = re.search(
        r"(?:Expédition|Expedition|Livraison)\s+(?:à partir|a partir|sous|en|dès|des)\s+[^€]+",
        text,
        re.I,
    )
    return match.group(0).strip() if match else ""
