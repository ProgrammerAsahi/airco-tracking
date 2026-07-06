from __future__ import annotations

import re

from bs4 import BeautifulSoup, Tag

from ...models import Product
from ..base import Adapter, canonical_url, clean_text, is_presale_delivery, parse_btu, parse_cooling_watts_btu
from .common import parse_french_price


class CostwayFranceAdapter(Adapter):
    """Costway France — Magento category page with qty-N stock classes."""

    site = "Costway France"
    urls = ("https://www.costway.fr/electromenagers/climatiseurs.html",)

    def parse(self, soup: BeautifulSoup, page_url: str) -> list[Product]:
        products: dict[str, Product] = {}
        for card in soup.select("li.item.product"):
            product = _parse_card(card, page_url)
            if product is not None:
                products[product.url] = product
        return list(products.values())


def _parse_card(card: Tag, page_url: str) -> Product | None:
    link = card.select_one("a.product-item-link")
    if link is None:
        return None
    href = str(link.get("href") or "")
    name = clean_text(link)
    text = clean_text(card)
    if not href or not name or not _is_portable_air_conditioner(name, text):
        return None

    qty = _stock_quantity(card)
    lower = text.casefold()
    presale = "précommande" in lower or "precommande" in lower or is_presale_delivery(text)
    unavailable = any(term in lower for term in ("rupture", "eupture", "épuisé", "epuise", "out of stock"))
    in_stock = qty is None or qty > 0
    available = presale or (in_stock and not unavailable)

    return Product(
        site="Costway France",
        name=name,
        url=canonical_url(page_url, href),
        available=available,
        price_eur=parse_french_price(text),
        delivery=_delivery_text(text, available=available, presale=presale, unavailable=unavailable),
        btu=parse_btu(f"{name} {text}") or parse_cooling_watts_btu(text),
        presale=presale,
    )


def _stock_quantity(card: Tag) -> int | None:
    photo = card.select_one(".product-item-photo")
    if photo is None:
        return None
    classes = photo.get("class", [])
    if isinstance(classes, str):
        classes = classes.split()
    for cls in classes:
        if not str(cls).startswith("qty-"):
            continue
        try:
            return int(str(cls)[4:])
        except ValueError:
            return None
    return None


def _delivery_text(text: str, *, available: bool, presale: bool, unavailable: bool) -> str:
    stock_match = re.search(r"\bStock\s*<\s*\d+\b", text, re.I)
    stock_text = stock_match.group(0) if stock_match else ""
    if presale:
        return "Précommande" + (f" · {stock_text}" if stock_text else "")
    if available:
        return stock_text or "En stock"
    if unavailable:
        return "Rupture de stock"
    return "Disponibilité inconnue"


def _is_portable_air_conditioner(name: str, text: str) -> bool:
    """Costway titles may list 'rafraîchisseur' as a mode of a real compressor AC."""
    name_lower = name.casefold()
    lower = f"{name} {text}".casefold()
    if "climatiseur" not in lower:
        return False
    fixed_excluded = (
        "mini split",
        "mini-split",
        "climatiseur split",
        "unité ac",
        "unité de climatisation",
        "pompe à chaleur",
    )
    if any(term in lower for term in fixed_excluded):
        return False
    hard_excluded = (
        "refroidisseur d'air",
        "refroidisseur d’air",
        "rafraîchisseur d'air",
        "rafraichisseur d'air",
        "aircooler",
        "climatiseur d'air",
        "climatiseur d’air",
        "réservoir d'eau",
        "réservoir d’eau",
        "reservoir d'eau",
        "sans tuyau d'évacuation",
        "sans tuyau d’évacuation",
        "sans tuyau d'evacuation",
    )
    if any(term in lower for term in hard_excluded):
        return False
    if re.search(r"(?<!dés)(?<!des)humidificateur", lower):
        return False
    accessory_terms = (
        "calfeutrage",
        "tuyau",
        "gaine",
        "filtre",
        "housse",
        "télécommande",
        "telecommande",
        "accessoire",
        "adaptateur",
        "support",
        "fenêtre pour climatiseur",
        "fenetre pour climatiseur",
    )
    if (name_lower.startswith(("kit ", "accessoire", "adaptateur", "tuyau", "gaine")) or any(
        term in name_lower for term in accessory_terms
    )) and parse_btu(name) is None:
        return False
    return any(term in lower for term in ("mobile", "portable", "portatif", "monobloc")) or parse_btu(name) is not None
