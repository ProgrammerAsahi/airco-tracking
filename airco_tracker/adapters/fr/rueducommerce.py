from __future__ import annotations

from bs4 import BeautifulSoup, Tag

from ...models import Product
from ..base import Adapter, canonical_url, clean_text, is_presale_delivery, parse_btu, parse_cooling_watts_btu
from .common import first_text, is_real_air_conditioner_fr, parse_french_price


class RueDuCommerceAdapter(Adapter):
    site = "Rue du Commerce"
    urls = ("https://www.rueducommerce.fr/recherche/climatiseur%20mobile/",)

    def parse(self, soup: BeautifulSoup, page_url: str) -> list[Product]:
        products: dict[str, Product] = {}
        for card in soup.select("li.pdt-item"):
            product = _parse_card(card, page_url)
            if product is not None:
                products[product.url] = product
        return list(products.values())


def _parse_card(card: Tag, page_url: str) -> Product | None:
    title_node = card.select_one("h3")
    link = title_node.find_parent("a", href=True) if title_node else None
    if link is None:
        link = card.select_one('a[href^="/p/"][href]')
    if link is None:
        return None
    name = clean_text(title_node) if title_node else clean_text(link)
    description = first_text(card, ".listing-product__desc")
    text = clean_text(card)
    full_text = f"{name} {description} {text}"
    if not name or not is_real_air_conditioner_fr(name, description):
        return None
    stock_text = first_text(card, ".listing-product__stock")
    lower = f"{stock_text} {text}".casefold()
    presale = is_presale_delivery(full_text)
    unavailable = any(term in lower for term in ("rupture", "indisponible", "épuisé", "epuise"))
    available = presale or ("en stock" in lower and not unavailable)
    return Product(
        site="Rue du Commerce",
        name=name,
        url=canonical_url(page_url, str(link.get("href", ""))),
        available=available,
        price_eur=parse_french_price(first_text(card, ".price") or text),
        delivery=stock_text or ("En stock" if available else "Indisponible"),
        btu=parse_btu(full_text) or parse_cooling_watts_btu(full_text),
        presale=presale,
    )
