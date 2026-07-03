from __future__ import annotations

import logging

from bs4 import BeautifulSoup

from ..fetch import Fetcher
from ..models import Product
from .base import (
    clean_text,
    parse_btu,
    parse_cooling_watts_btu,
    parse_price,
    parse_product_page_btu,
)


LOG = logging.getLogger(__name__)


class KampeerwereldAdapter:
    site = "Kampeerwereld"
    # The category intentionally hides sold-out products, so known seasonal
    # product URLs are retained to detect a future restock.
    product_urls = (
        "https://www.kampeerwereld.nl/eurom-ac-7001-mobiele-airco/2591",
        "https://www.kampeerwereld.nl/eurom-pac-9.2-mobiele-airco-380385/",
        "https://www.kampeerwereld.nl/mestic-spa-3000-split-airco-caravan-1503030/",
        "https://www.kampeerwereld.nl/mestic-spa-3100-split-airco-caravan-1518040/",
        "https://www.kampeerwereld.nl/mestic-spa-5000-split-unit-airco-caravan-1518030/",
        "https://www.kampeerwereld.nl/eurom-ac4201-split-airco-voor-caravan-en-thuisgebruik-382440/",
        "https://www.kampeerwereld.nl/eurom-ac5201-split-airco-voor-caravan-en-thuisgebruik-382532/",
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
                LOG.warning("Kampeerwereld product check failed for %s: %s", url, exc)
                continue
            products[product.url] = product
        if not products:
            raise RuntimeError("Kampeerwereld product pages could not be parsed: " + "; ".join(failures))
        return list(products.values())


def _parse_product_page(page: str, page_url: str) -> Product:
    soup = BeautifulSoup(page, "html.parser")
    heading = soup.select_one(".product-detail-name")
    price_node = soup.select_one(".product-detail-price")
    stock_node = soup.select_one(".product-detail-stock-container")
    description_node = soup.select_one(".product-detail-description-text")
    if heading is None or price_node is None or stock_node is None:
        raise RuntimeError("Kampeerwereld page did not contain product data")
    name = clean_text(heading)
    stock = clean_text(stock_node)
    page_text = clean_text(soup)
    lower = f"{stock} {page_text}".lower()
    store_only = "exclusief in winkel" in lower
    unavailable = "niet beschikbaar" in stock.lower() or soup.select_one(".form-stocksubscribe") is not None
    available = not store_only and not unavailable and (
        "op voorraad" in stock.lower() or "thuis binnen" in stock.lower()
    )
    description = clean_text(description_node) if description_node else ""
    return Product(
        site="Kampeerwereld",
        name=name,
        url=page_url,
        available=available,
        price_eur=parse_price(clean_text(price_node)),
        delivery=stock or ("Online op voorraad" if available else "Niet beschikbaar"),
        btu=(
            parse_btu(f"{name} {description}")
            or parse_cooling_watts_btu(description)
            or parse_product_page_btu(page)
        ),
    )
