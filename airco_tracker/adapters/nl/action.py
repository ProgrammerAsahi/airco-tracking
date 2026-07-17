from __future__ import annotations

import logging

from bs4 import BeautifulSoup

from ...fetch import Fetcher
from ...models import Product
from ..base import (
    canonical_url,
    clean_text,
    parse_btu,
    parse_cooling_watts_btu,
    parse_product_page_btu,
)
from ..schema import first_offer, offer_price, product_json_ld, schema_in_stock
from ..sitemap import sitemap_locations


LOG = logging.getLogger(__name__)


class ActionAdapter:
    site = "Action Webshop"
    sitemap_url = "https://shop.action.com/sitemaps/nl-nl/product-sitemap.xml"
    # Action removes expired weekly deals from the sitemap. Retaining known airco
    # URLs lets the tracker notice if the same deal is reactivated.
    known_urls = (
        "https://shop.action.com/nl-nl/p/8712836991743/mobiele-smart-airco-wit",
    )

    def __init__(self, fetcher: Fetcher) -> None:
        self.fetcher = fetcher

    def fetch_products(self) -> list[Product]:
        response = self.fetcher.session.get(self.sitemap_url, timeout=self.fetcher.timeout)
        response.raise_for_status()
        urls = set(self.known_urls)
        urls.update(url for url in sitemap_locations(response.content) if _is_product_url(url))
        products: dict[str, Product] = {}
        failures: list[str] = []
        for url in sorted(urls):
            try:
                product = _parse_product_page(self.fetcher.get(url), url)
            except Exception as exc:
                failures.append(f"{url}: {exc}")
                LOG.warning("Action product check failed for %s: %s", url, exc)
                continue
            products[product.url] = product
        if not products:
            raise RuntimeError("Action product pages could not be parsed: " + "; ".join(failures))
        return list(products.values())


def _is_product_url(url: str) -> bool:
    lower = url.lower()
    excluded = ("aircooler", "luchtkoeler", "ventilator", "raamafdichting", "filter")
    return "/p/" in lower and ("airco" in lower or "aircondition" in lower) and not any(
        term in lower for term in excluded
    )


def _parse_product_page(page: str, page_url: str) -> Product:
    soup = BeautifulSoup(page, "html.parser")
    data = product_json_ld(soup)
    name = str(data.get("name", "")).strip()
    offer = first_offer(data)
    if not name or not offer:
        raise RuntimeError("Action product data did not contain a name and offer")
    available = schema_in_stock(offer)
    text = clean_text(soup)
    # An expired deal is never orderable, even when the schema offer still
    # claims the product is in stock.
    if "deal verlopen" in text.lower():
        available = False
    delivery = _delivery(text, available)
    description = str(data.get("description", ""))
    return Product(
        site="Action Webshop",
        name=name,
        url=canonical_url(page_url, str(offer.get("url") or data.get("url") or page_url)),
        available=available,
        price_eur=offer_price(offer),
        delivery=delivery,
        btu=(
            parse_btu(f"{name} {description} {text}")
            or parse_cooling_watts_btu(f"{description} {text}")
            or parse_product_page_btu(page)
        ),
    )


def _delivery(text: str, available: bool) -> str:
    lower = text.lower()
    if "deal verlopen" in lower:
        return "Deal verlopen"
    if available and "thuisbezorgd binnen 3 werkdagen" in lower:
        return "Thuisbezorgd binnen 3 werkdagen"
    return "Op voorraad" if available else "Niet op voorraad"
