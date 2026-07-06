from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup

from ...fetch import Fetcher
from ...models import Product
from ..base import canonical_url, clean_text, is_presale_delivery, parse_btu, parse_cooling_watts_btu, parse_product_page_btu
from ..schema import first_offer, offer_price, product_json_ld
from .common import is_real_air_conditioner_fr


LOG = logging.getLogger(__name__)


class MonCampingCarAdapter:
    """Mon Camping Car — portable camper/caravan air conditioners."""

    site = "Mon Camping Car"
    category_url = "https://www.mon-camping-car.com/categorie-climatiseurs-portable-1.html"

    def __init__(self, fetcher: Fetcher) -> None:
        self.fetcher = fetcher

    def fetch_products(self) -> list[Product]:
        urls = _category_product_urls(self.fetcher.get(self.category_url), self.category_url)
        if not urls:
            raise RuntimeError(f"{self.site}: category contained no portable air conditioners")

        products: dict[str, Product] = {}
        failures: list[str] = []
        for url in urls:
            try:
                product = _parse_product_page(self.fetcher.get(url), url)
            except Exception as exc:
                failures.append(f"{url}: {exc}")
                LOG.warning("Mon Camping Car product check failed for %s: %s", url, exc)
                continue
            if is_real_air_conditioner_fr(product.name, product.delivery or ""):
                products[product.url] = product
        if not products:
            raise RuntimeError("Mon Camping Car product pages could not be parsed: " + "; ".join(failures))
        return list(products.values())


def _category_product_urls(page: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(page, "html.parser")
    urls: list[str] = []
    for card in soup.select(".desktop-product-list-container"):
        link = card.select_one("a[href]")
        name = clean_text(card.select_one(".product-designation-button") or card)
        text = clean_text(card)
        if link is not None and is_real_air_conditioner_fr(name, text):
            urls.append(canonical_url(base_url, str(link.get("href"))))
    return list(dict.fromkeys(urls))


def _parse_product_page(page: str, page_url: str) -> Product:
    soup = BeautifulSoup(page, "html.parser")
    data = product_json_ld(soup)
    heading = soup.select_one("h1")
    name = clean_text(heading) if heading is not None else str(data.get("name", "")).strip()
    description = str(data.get("description", ""))
    offer = first_offer(data)
    if not name or not offer:
        raise RuntimeError("Mon Camping Car product data did not contain a name and offer")

    availability = str(offer.get("availability", ""))
    delivery = _delivery_text(soup) or _delivery_from_availability(availability)
    orderable = soup.select_one(".add-cart-button") is not None
    in_stock = availability.rstrip("/").casefold().endswith("instock")
    backorder = any(marker in availability.casefold() for marker in ("backorder", "preorder", "presale"))
    presale = backorder or is_presale_delivery(delivery)
    available = in_stock or (orderable and presale)

    return Product(
        site="Mon Camping Car",
        name=name,
        url=str(offer.get("url") or data.get("url") or page_url),
        available=available,
        price_eur=offer_price(offer),
        delivery=delivery,
        btu=parse_btu(f"{name} {description}") or parse_cooling_watts_btu(description) or parse_product_page_btu(page),
        presale=presale and available,
    )


def _delivery_text(soup: BeautifulSoup) -> str:
    page_text = clean_text(soup)
    match = re.search(
        r"(Expédié sous\s+\d+\s+à\s+\d+\s+jours|Disponible à partir du\s+\d{1,2}/\d{1,2}/\d{4})",
        page_text,
        re.I,
    )
    if match:
        return match.group(1)

    node = soup.select_one(".product-shipping-secondary")
    if node is not None:
        return clean_text(node)
    for selector in (".disponible", ".product-infos"):
        node = soup.select_one(selector)
        if node is not None:
            text = clean_text(node)
            if "Expédié" in text or "Disponible" in text:
                return text
    return ""


def _delivery_from_availability(availability: str) -> str:
    lower = availability.casefold()
    if lower.rstrip("/").endswith("instock"):
        return "En stock"
    if "backorder" in lower:
        return "Disponible sur commande"
    if "preorder" in lower or "presale" in lower:
        return "Pré-commande"
    return "Indisponible"
