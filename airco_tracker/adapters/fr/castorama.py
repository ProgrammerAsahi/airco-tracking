from __future__ import annotations

from bs4 import BeautifulSoup, Tag

from ...models import Product
from ..base import Adapter, canonical_url, clean_text, is_presale_delivery, parse_btu, parse_cooling_watts_btu
from .common import first_text, is_real_air_conditioner_fr, parse_french_price


class CastoramaAdapter(Adapter):
    site = "Castorama"
    urls = (
        "https://www.castorama.fr/chauffage-climatisation-et-ventilation/"
        "climatiseur-ventilateur/climatiseur-mobile/cat_id_411.cat",
    )

    def parse(self, soup: BeautifulSoup, page_url: str) -> list[Product]:
        products: dict[str, Product] = {}
        for card in soup.select('[data-testid="product"]'):
            product = _parse_card(card, page_url)
            if product is not None:
                products[product.url] = product
        return list(products.values())


def _parse_card(card: Tag, page_url: str) -> Product | None:
    link = card.select_one('[data-testid="product-link"][href]')
    if link is None:
        return None
    name = first_text(card, '[data-testid="product-name"]')
    if not name:
        image = card.select_one("img[alt]")
        name = str(image.get("alt", "")).strip() if image else ""
    text = clean_text(card)
    if not name or not is_real_air_conditioner_fr(name, text):
        return None
    lower = text.casefold()
    presale = is_presale_delivery(text)
    unavailable = any(term in lower for term in ("rupture", "indisponible", "non disponible"))
    available = presale or ("ajouter au panier" in lower or "en stock" in lower) and not unavailable
    delivery = _delivery(text, available, presale)
    return Product(
        site="Castorama",
        name=name,
        url=canonical_url(page_url, str(link.get("href", ""))),
        available=available,
        price_eur=parse_french_price(text),
        delivery=delivery,
        btu=parse_btu(text) or parse_cooling_watts_btu(text),
        presale=presale,
    )


def _delivery(text: str, available: bool, presale: bool) -> str:
    if presale:
        return "Précommande / délai annoncé"
    lower = text.casefold()
    if "vérifiez sa disponibilité" in lower or "verifiez sa disponibilite" in lower:
        return "Disponibilité magasin à vérifier"
    if available:
        return "En stock / achat possible"
    if "rupture" in lower or "indisponible" in lower:
        return "Indisponible"
    return "Disponibilité inconnue"
