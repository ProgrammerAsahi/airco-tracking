from __future__ import annotations

from bs4 import BeautifulSoup, Tag

from ...models import Product
from ..base import Adapter, canonical_url, clean_text, is_presale_delivery, parse_btu, parse_cooling_watts_btu
from .common import first_text, is_real_air_conditioner_fr, meta_price, parse_french_price


class MaisonEnergyAdapter(Adapter):
    """Maison Energy — Prestashop search results with schema availability."""

    site = "Maison Energy"
    urls = ("https://www.maison-energy.com/recherche?search_query=climatiseur%20mobile",)

    def parse(self, soup: BeautifulSoup, page_url: str) -> list[Product]:
        products: dict[str, Product] = {}
        for card in soup.select("article"):
            product = _parse_article(card, page_url)
            if product is not None:
                products[product.url] = product
        return list(products.values())


def _parse_article(card: Tag, page_url: str) -> Product | None:
    title = first_text(card, ".product-title", '[itemprop="name"]')
    link = card.select_one(".product-title")
    parent_link = link.find_parent("a", href=True) if link else None
    if parent_link is None:
        parent_link = card.select_one("a.product-item-photo[href], a[href]")
    if parent_link is None:
        return None

    text = clean_text(card)
    if not title or not _is_mobile_air_conditioner(title, text):
        return None

    availability = _schema_availability(card)
    lower = text.casefold()
    unavailable = any(term in lower for term in ("non disponible", "demande de devis", "rupture", "épuisé", "epuise"))
    schema_presale = "preorder" in availability
    presale = (schema_presale or is_presale_delivery(text)) and not unavailable
    in_stock = "instock" in availability or "ajouter au panier" in lower or "en stock" in lower
    available = not unavailable and (in_stock or presale)

    return Product(
        site="Maison Energy",
        name=title,
        url=canonical_url(page_url, str(parent_link.get("href") or "")),
        available=available,
        price_eur=meta_price(card) or parse_french_price(first_text(card, ".price") or text),
        delivery=_delivery_text(text, availability, available=available, presale=presale, unavailable=unavailable),
        btu=parse_btu(f"{title} {text}") or parse_cooling_watts_btu(text),
        presale=presale,
    )


def _schema_availability(card: Tag) -> str:
    node = card.select_one('[itemprop="availability"][content]')
    return str(node.get("content") or "").casefold() if node else ""


def _delivery_text(text: str, availability: str, *, available: bool, presale: bool, unavailable: bool) -> str:
    lower = text.casefold()
    if unavailable:
        if "non disponible" in lower and "demande de devis" in lower:
            return "Non disponible / demande de devis"
        if "demande de devis" in lower:
            return "Demande de devis"
        return "Non disponible"
    if presale:
        return "Précommande"
    for marker in ("Livraison gratuite estimée", "Livraison estimée", "En stock", "Disponible"):
        if marker.casefold() in lower:
            return marker
    if available:
        return "En stock" if "instock" in availability else "Disponible"
    return "Disponibilité inconnue"


def _is_mobile_air_conditioner(name: str, text: str) -> bool:
    lower = f"{name} {text}".casefold()
    if not is_real_air_conditioner_fr(name, text):
        return False
    fixed_excluded = (
        "mono-split",
        "mono split",
        "bi-split",
        "bi split",
        "tri-split",
        "tri split",
        "multi-split",
        "multi split",
        "climatiseur mural",
        "mural ",
        "cassette",
        "gainable",
        "unité intérieure",
        "unité extérieure",
        "unite interieure",
        "unite exterieure",
    )
    if any(term in lower for term in fixed_excluded):
        return False
    return any(term in lower for term in ("mobile", "portable", "monobloc", "pinguino"))
