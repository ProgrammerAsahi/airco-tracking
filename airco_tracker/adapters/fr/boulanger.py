from __future__ import annotations

import logging

import requests
from bs4 import BeautifulSoup, Tag

from ...models import Product
from ..base import Adapter, canonical_url, clean_text, is_presale_delivery, parse_btu, parse_cooling_watts_btu
from .common import is_real_air_conditioner_fr, parse_float, parse_french_price


LOG = logging.getLogger(__name__)


class BoulangerAdapter(Adapter):
    site = "Boulanger"
    urls = ("https://www.boulanger.com/resultats?tr=climatiseur%20mobile",)
    timeout = 60

    def fetch_products(self) -> list[Product]:
        """Fetch Boulanger with a single longer request.

        Boulanger is fast from local residential networks but can hold Azure
        datacenter connections open long enough to exhaust the shared
        Fetcher's 25s read timeout three times. A single longer request is
        less noisy and keeps the whole scheduled run comfortably within the
        container job timeout.
        """
        products: dict[str, Product] = {}
        headers = dict(self.fetcher.session.headers)
        headers.update(
            {
                "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.7",
                "Referer": "https://www.boulanger.com/",
            }
        )
        for url in self.urls:
            LOG.info("Fetching %s", url)
            response = requests.get(url, headers=headers, timeout=max(self.fetcher.timeout, self.timeout))
            response.raise_for_status()
            if len(response.content) < 10_000:
                raise RuntimeError(f"Suspiciously small response from {url}")
            soup = BeautifulSoup(response.text, "html.parser")
            for product in self.parse(soup, url):
                products[product.url] = product
        if not products:
            raise RuntimeError(f"{self.site}: parser found no products; site markup may have changed")
        return list(products.values())

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
