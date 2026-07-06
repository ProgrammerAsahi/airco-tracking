from __future__ import annotations

from bs4 import BeautifulSoup, Tag

from ...fetch import Fetcher
from ...models import Product
from ..base import canonical_url, clean_text, is_presale_delivery, parse_btu, parse_cooling_watts_btu
from .common import is_real_air_conditioner_fr, parse_french_price


class H2REquipementsAdapter:
    """H2R Équipements — camper/van portable air-conditioner category.

    The generic site search returns unrelated marine products for
    ``climatiseur``. Use the server-rendered "climatisation nomade" category
    instead, which exposes stable product cards and explicit availability text.
    """

    site = "H2R Équipements"
    category_url = "https://www.h2r-equipements.com/1923-climatisation-nomade-van-amenage"

    def __init__(self, fetcher: Fetcher) -> None:
        self.fetcher = fetcher

    def fetch_products(self) -> list[Product]:
        soup = BeautifulSoup(self.fetcher.get(self.category_url), "html.parser")
        products = [_parse_card(card, self.category_url) for card in soup.select(".product-miniature")]
        products = [product for product in products if product is not None]
        if not products:
            raise RuntimeError(f"{self.site}: parser found no products; site markup may have changed")
        return products


def _parse_card(card: Tag, base_url: str) -> Product | None:
    link = card.select_one(".product-name a[href]") or card.select_one("a.product-cover-link[href]")
    if link is None:
        return None
    name = clean_text(link)
    if not name:
        image = card.select_one("img[alt]")
        name = str(image.get("alt", "")).strip() if image else ""
    text = clean_text(card)
    if not name or not _is_h2r_air_conditioner(name, text):
        return None

    delivery = clean_text(card.select_one(".product-availability") or card)
    lower_delivery = delivery.casefold()
    in_stock = "en stock" in lower_delivery and "retour en stock" not in lower_delivery
    orderable_later = "sur commande" in lower_delivery
    unavailable = "épuisé" in lower_delivery or "epuise" in lower_delivery or "retour en stock" in lower_delivery
    presale = orderable_later or is_presale_delivery(delivery)
    available = in_stock or orderable_later

    if unavailable and not orderable_later:
        available = False

    return Product(
        site="H2R Équipements",
        name=name,
        url=canonical_url(base_url, str(link.get("href"))),
        available=available,
        price_eur=_card_price(card) or parse_french_price(text),
        delivery=delivery or ("En stock" if available else "Indisponible"),
        btu=parse_btu(text) or parse_cooling_watts_btu(text),
        presale=presale and available,
    )


def _is_h2r_air_conditioner(name: str, text: str) -> bool:
    if is_real_air_conditioner_fr(name, text):
        return True
    lower = f"{name} {text}".casefold()
    known_models = (
        "ecoflow wave",
        "mestic split-unit",
        "mestic spa-",
        "eurom ac",
        "carbest climatiseur",
        "brunner polarys",
    )
    excluded = (
        "batterie seule",
        "housse",
        "filtre",
        "diffuseur",
        "tuyau",
        "kit ",
        "bac à condensats",
        "bac a condensats",
    )
    return any(model in lower for model in known_models) and not any(term in lower for term in excluded)


def _card_price(card: Tag) -> float | None:
    node = card.select_one(".price.product-price") or card.select_one(".product-price")
    return parse_french_price(clean_text(node)) if node else None
