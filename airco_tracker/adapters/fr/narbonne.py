from __future__ import annotations

import logging

from bs4 import BeautifulSoup

from ...fetch import Fetcher
from ...models import Product
from ..base import clean_text, is_presale_delivery, parse_btu, parse_cooling_watts_btu, parse_product_page_btu
from ..schema import first_offer, offer_price, product_json_ld
from .common import is_real_air_conditioner_fr


LOG = logging.getLogger(__name__)


class NarbonneAccessoiresAdapter:
    """Narbonne Accessoires — known camper air-conditioner product pages.

    The category page is mostly editorial content and navigation, but it links
    to a small set of real air-conditioner PDPs. The PDP schema can overstate
    stock when only store pickup is available, so home-delivery availability is
    read from the visible ``Livraison à Domicile`` block.
    """

    site = "Narbonne Accessoires"
    product_urls = (
        "https://www.narbonneaccessoires.fr/fr-fr/climatisation/climatiseur-de-toit-12v-p-18169.htm",
        "https://www.narbonneaccessoires.fr/fr-fr/climatisation/climatiseur-smart-power-p-21895.htm",
        "https://www.narbonneaccessoires.fr/fr-fr/climatiseurs/climatiseur-de-coffre-freshwell-3000-p-31260.htm",
    )

    def __init__(self, fetcher: Fetcher) -> None:
        self.fetcher = fetcher

    def fetch_products(self) -> list[Product]:
        products: dict[str, Product] = {}
        failures: list[str] = []
        for url in self.product_urls:
            try:
                product = _parse_product_page(self.fetcher.get(url), url)
            except Exception as exc:
                failures.append(f"{url}: {exc}")
                LOG.warning("Narbonne Accessoires product check failed for %s: %s", url, exc)
                continue
            if is_real_air_conditioner_fr(product.name, product.delivery or ""):
                products[product.url] = product
        if not products:
            raise RuntimeError("Narbonne Accessoires product pages could not be parsed: " + "; ".join(failures))
        return list(products.values())


def _parse_product_page(page: str, page_url: str) -> Product:
    soup = BeautifulSoup(page, "html.parser")
    data = product_json_ld(soup)
    offer = first_offer(data)
    name = str(data.get("name", "")).strip()
    description = str(data.get("description", ""))
    if not name or not offer:
        raise RuntimeError("Narbonne Accessoires product data did not contain a name and offer")

    delivery = _home_delivery_text(soup)
    delivery_lower = delivery.casefold()
    available = "en stock" in delivery_lower and "indisponible" not in delivery_lower
    presale = available and is_presale_delivery(delivery)

    return Product(
        site="Narbonne Accessoires",
        name=name,
        url=str(offer.get("url") or data.get("url") or page_url),
        available=available,
        price_eur=offer_price(offer),
        delivery=delivery or ("En stock" if available else "Livraison à domicile indisponible"),
        btu=parse_btu(f"{name} {description}") or parse_cooling_watts_btu(description) or parse_product_page_btu(page),
        presale=presale,
    )


def _home_delivery_text(soup: BeautifulSoup) -> str:
    stock_web = soup.select_one(".stock_web")
    if stock_web is not None:
        return clean_text(stock_web)

    # Variant PDPs render availability per row rather than in the main
    # ``stock_web`` block. Keep only rows that explicitly mention home delivery.
    chunks: list[str] = []
    for stock in soup.select(".stock"):
        text = clean_text(stock)
        if "Livraison à Domicile" in text:
            chunks.append(text)
    return " ".join(chunks)
