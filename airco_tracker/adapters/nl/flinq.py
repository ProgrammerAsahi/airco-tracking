from __future__ import annotations

import logging

from bs4 import BeautifulSoup

from ...fetch import Fetcher
from ...models import Product
from ..base import canonical_url, parse_btu, parse_cooling_watts_btu, parse_product_page_btu
from ..schema import first_offer, offer_price, product_json_ld, schema_in_stock
from ..sitemap import sitemap_locations


LOG = logging.getLogger(__name__)


class FlinqAdapter:
    site = "FlinQ"
    sitemap_url = "https://www.flinqproducts.nl/product-sitemap.xml"

    def __init__(self, fetcher: Fetcher) -> None:
        self.fetcher = fetcher

    def fetch_products(self) -> list[Product]:
        response = self.fetcher.session.get(self.sitemap_url, timeout=self.fetcher.timeout)
        response.raise_for_status()
        urls = [url for url in sitemap_locations(response.content) if _is_product_url(url)]
        if not urls:
            raise RuntimeError("FlinQ sitemap contained no portable air conditioners")
        products: dict[str, Product] = {}
        failures: list[str] = []
        for url in urls:
            try:
                product = _parse_product_page(self.fetcher.get(url), url)
            except Exception as exc:
                failures.append(f"{url}: {exc}")
                LOG.warning("FlinQ product check failed for %s: %s", url, exc)
                continue
            products[product.url] = product
        if not products:
            raise RuntimeError("FlinQ product pages could not be parsed: " + "; ".join(failures))
        return list(products.values())


def _is_product_url(url: str) -> bool:
    lower = url.lower()
    excluded = ("aircooler", "raamafdichting", "filter", "slang")
    return "/product/" in lower and "mobiele-airco" in lower and not any(
        term in lower for term in excluded
    )


def _parse_product_page(page: str, page_url: str) -> Product:
    soup = BeautifulSoup(page, "html.parser")
    data = product_json_ld(soup)
    name = str(data.get("name", "")).strip()
    offer = first_offer(data)
    if not name or not offer:
        raise RuntimeError("FlinQ product data did not contain a name and offer")
    description = str(data.get("description", ""))
    available = schema_in_stock(offer)
    return Product(
        site="FlinQ",
        name=name,
        url=canonical_url(page_url, str(offer.get("url") or data.get("url") or page_url)),
        available=available,
        price_eur=offer_price(offer),
        delivery="Op voorraad" if available else "Niet op voorraad",
        btu=(
            parse_btu(f"{name} {description}")
            or parse_cooling_watts_btu(description)
            or parse_product_page_btu(page)
        ),
    )
