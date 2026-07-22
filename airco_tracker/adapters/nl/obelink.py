from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup

from ...fetch import Fetcher
from ...models import Product
from ...url_security import validate_discovered_merchant_url
from ..base import parse_btu, parse_cooling_watts_btu, parse_product_page_btu
from ..schema import first_offer, offer_price, product_json_ld, schema_in_stock


LOG = logging.getLogger(__name__)

class ObelinkAdapter:
    site = "Obelink"
    category_urls = (
        "https://www.obelink.nl/klimaatbeheersing/airco-s/mobiele-airco-s/",
        "https://www.obelink.nl/klimaatbeheersing/airco-s/split-airco-s/",
    )
    # Sold-out seasonal products can disappear from categories.  Keep known
    # portable models so a later reactivation is still detected.
    known_urls = (
        "https://www.obelink.nl/mestic-spa-3000-split-airco.html",
        "https://www.obelink.nl/eurom-ac7000-split-airco.html",
        "https://www.obelink.nl/tristar-ac-5531-mobiele-airco.html",
        "https://www.obelink.nl/inventum-ac-901-mobiele-airco.html",
        "https://www.obelink.nl/inventum-ac-701-mobiele-airco.html",
        "https://www.obelink.nl/flinq-slimme-mobiele-airco-13000-btu-8720168680402.html",
    )

    def __init__(self, fetcher: Fetcher) -> None:
        self.fetcher = fetcher

    def fetch_products(self) -> list[Product]:
        urls = set(self.known_urls)
        for category_url in self.category_urls:
            urls.update(
                validate_discovered_merchant_url(url, site=self.site)
                for url in _category_product_urls(self.fetcher.get(category_url))
            )
        products: dict[str, Product] = {}
        failures: list[str] = []
        for url in sorted(urls):
            try:
                product = _parse_product_page(self.fetcher.get(url), url)
            except Exception as exc:
                failures.append(f"{url}: {exc}")
                LOG.warning("Obelink product check failed for %s: %s", url, exc)
                continue
            if _is_portable_airco(product.name):
                products[product.url] = product
        if not products:
            raise RuntimeError("Obelink product pages could not be parsed: " + "; ".join(failures))
        return list(products.values())


def _category_product_urls(page: str) -> list[str]:
    paths = re.findall(r'\\?"urlPath\\?":\\?"([^"\\]+airco[^"\\]+\.html)\\?"', page, re.I)
    return ["https://www.obelink.nl/" + path.lstrip("/") for path in dict.fromkeys(paths)]


def _parse_product_page(page: str, page_url: str) -> Product:
    data = product_json_ld(BeautifulSoup(page, "html.parser"))
    name = str(data.get("name", "")).strip()
    description = str(data.get("description", ""))
    offer = first_offer(data)
    if not name or not offer:
        raise RuntimeError("Obelink product data did not contain a name and offer")
    available = schema_in_stock(offer)
    return Product(
        site="Obelink",
        name=name,
        url=str(data.get("url") or page_url),
        available=available,
        price_eur=offer_price(offer),
        delivery="Online op voorraad" if available else "Niet online op voorraad",
        btu=(
            parse_btu(f"{name} {description}")
            or parse_cooling_watts_btu(f"{name} {description}")
            or parse_product_page_btu(page)
        ),
    )


def _is_portable_airco(name: str) -> bool:
    lower = name.lower()
    excluded = ("aircooler", "afdekhoes", "accessoire", "dakairco", "bankairco")
    return "airco" in lower and not any(term in lower for term in excluded)
