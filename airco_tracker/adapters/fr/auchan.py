from __future__ import annotations

from bs4 import BeautifulSoup, Tag

from ...models import Product
from ..base import Adapter, canonical_url, clean_text, is_presale_delivery, parse_btu, parse_cooling_watts_btu
from .common import first_text, is_real_air_conditioner_fr, meta_price, parse_french_price


class AuchanAdapter(Adapter):
    site = "Auchan"
    urls = ("https://www.auchan.fr/electromenager-cuisine/climatisation-chauffage/climatiseur-mobile/ca-7328362",)

    def parse(self, soup: BeautifulSoup, page_url: str) -> list[Product]:
        products: dict[str, Product] = {}
        for card in soup.select('article[itemtype*="Product"], article.product-thumbnail'):
            product = _parse_card(card, page_url)
            if product is not None:
                products[product.url] = product
        return list(products.values())


def _parse_card(card: Tag, page_url: str) -> Product | None:
    link = card.select_one('a[href*="/pr-"][href]')
    if link is None:
        return None
    name = first_text(card, '[itemprop="name"]', ".product-thumbnail__description")
    if not name:
        name = clean_text(link)
    text = clean_text(card)
    if not name or not is_real_air_conditioner_fr(name, text):
        return None
    delivery = first_text(card, ".delivery-promise") or _delivery_from_text(text)
    availability = str((card.select_one('[itemprop="availability"][content]') or {}).get("content", ""))
    lower = f"{text} {availability}".casefold()
    presale = is_presale_delivery(f"{delivery} {text}") or 'data-offer-with-delay="true"' in str(card)
    unavailable = any(term in lower for term in ("outofstock", "rupture", "indisponible"))
    in_stock = "instock" in lower or "ajouter au panier" in lower
    return Product(
        site="Auchan",
        name=name,
        url=canonical_url(page_url, str(link.get("href", ""))),
        available=presale or (in_stock and not unavailable),
        price_eur=meta_price(card) or parse_french_price(text),
        delivery=delivery or ("En stock" if in_stock and not unavailable else "Indisponible"),
        btu=parse_btu(f"{name} {text}") or parse_cooling_watts_btu(text),
        presale=presale,
    )


def _delivery_from_text(text: str) -> str:
    lower = text.casefold()
    if "livraison" in lower:
        start = lower.find("livraison")
        return text[start : start + 80].strip()
    return ""
