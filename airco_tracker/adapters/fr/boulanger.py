from __future__ import annotations

from bs4 import BeautifulSoup, Tag

from ...models import Product
from ..base import Adapter, canonical_url, clean_text, is_presale_delivery, parse_btu, parse_cooling_watts_btu
from .common import is_real_air_conditioner_fr, parse_float, parse_french_price


class BoulangerAdapter(Adapter):
    site = "Boulanger"
    urls = ("https://www.boulanger.com/resultats?tr=climatiseur%20mobile",)

    def parse(self, soup: BeautifulSoup, page_url: str) -> list[Product]:
        products: dict[str, Product] = {}
        for card in soup.select(".product-list__item-original[data-product-id]"):
            product = _parse_card(card, page_url)
            if product is not None:
                products[product.url] = product
        return list(products.values())


def _parse_card(card: Tag, page_url: str) -> Product | None:
    link = _product_link(card)
    if link is None:
        return None
    name = clean_text(link)
    text = clean_text(card)
    if not name or not is_real_air_conditioner_fr(name, text):
        return None
    attrs = link.attrs
    availability = str(attrs.get("data-analytics_product_availability", "")).casefold()
    lower = text.casefold()
    unavailable = any(term in lower for term in ("indisponible", "rupture", "me prévenir", "m'alerter"))
    presale = is_presale_delivery(text)
    available = presale or availability == "true" or ("ajouter au panier" in lower and not unavailable)
    price = parse_float(attrs.get("data-analytics_product_unitprice_ati")) or parse_french_price(text)
    return Product(
        site="Boulanger",
        name=name,
        url=canonical_url(page_url, str(link.get("href", ""))),
        available=available,
        price_eur=price,
        delivery=_delivery(text, available, presale),
        btu=parse_btu(text) or parse_cooling_watts_btu(text),
        presale=presale,
    )


def _product_link(card: Tag) -> Tag | None:
    for link in card.select('a[href^="/ref/"], a[href*="/ref/"]'):
        text = clean_text(link)
        if "climatiseur" in text.casefold():
            return link
    return None


def _delivery(text: str, available: bool, presale: bool) -> str:
    if presale:
        return "Précommande / délai annoncé"
    if available:
        return "Ajouter au panier"
    lower = text.casefold()
    if "indisponible" in lower or "rupture" in lower:
        return "Indisponible"
    return "Disponibilité inconnue"
