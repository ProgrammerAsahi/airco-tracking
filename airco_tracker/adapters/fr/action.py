from __future__ import annotations

from bs4 import BeautifulSoup, Tag

from ...fetch import Fetcher
from ...models import Product
from ..base import canonical_url, clean_text, parse_btu
from .common import is_real_air_conditioner_fr, parse_french_price


class ActionFranceAdapter:
    """Action France search page.

    Action currently lists coolers/fans for this query rather than compressor
    air conditioners. A successful zero-product result is useful: the site is
    monitored without polluting stock with evaporative coolers.
    """

    site = "Action France"
    search_url = "https://www.action.com/fr-fr/search/?q=climatiseur"

    def __init__(self, fetcher: Fetcher) -> None:
        self.fetcher = fetcher

    def fetch_products(self) -> list[Product]:
        soup = BeautifulSoup(self.fetcher.get(self.search_url), "html.parser")
        products: dict[str, Product] = {}
        for card in soup.select('[data-testid="product-card"]'):
            product = _parse_card(card, self.search_url)
            if product is not None:
                products[product.url] = product
        return list(products.values())


def _parse_card(card: Tag, page_url: str) -> Product | None:
    link = card.select_one('[data-testid="product-card-link"][href]')
    if link is None:
        return None
    text = clean_text(card)
    name = _name_from_text(text)
    if not name or not is_real_air_conditioner_fr(name, text):
        return None
    return Product(
        site="Action France",
        name=name,
        url=canonical_url(page_url, str(link.get("href", ""))),
        # Fail closed: the search page only shows catalogue cards whose store
        # stock must be verified ("disponibilité magasin à vérifier"). Online
        # orderability cannot be confirmed here, so this must not be available.
        available=False,
        price_eur=parse_french_price(text),
        delivery="Catalogue Action France — disponibilité magasin à vérifier",
        btu=parse_btu(text),
    )


def _name_from_text(text: str) -> str:
    if "€" not in text:
        return text.strip()
    return text.split("€", 1)[0].strip()
