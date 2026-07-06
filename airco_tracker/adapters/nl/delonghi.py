from __future__ import annotations

import json
import logging
from urllib.parse import urlsplit, urlunsplit

from bs4 import BeautifulSoup

from ...fetch import Fetcher
from ...models import Product
from ..shared.delonghi import parse_delonghi_product_page


LOG = logging.getLogger(__name__)


class DelonghiAdapter:
    site = "De'Longhi NL"
    category_url = (
        "https://www.delonghi.com/nl-nl/c/meer-apparaten/klimaat/"
        "draagbare-airconditioners/draagbare-airconditioners"
    )

    def __init__(self, fetcher: Fetcher) -> None:
        self.fetcher = fetcher

    def fetch_products(self) -> list[Product]:
        urls = _product_urls(self.fetcher.get(self.category_url))
        if not urls:
            raise RuntimeError("De'Longhi category contained no portable air conditioners")
        products: dict[str, Product] = {}
        failures: list[str] = []
        for url in urls:
            try:
                product = _parse_product_page(self.fetcher.get(url), url)
            except Exception as exc:
                failures.append(f"{url}: {exc}")
                LOG.warning("De'Longhi product check failed for %s: %s", url, exc)
                continue
            products[product.url] = product
        if not products:
            raise RuntimeError("De'Longhi product pages could not be parsed: " + "; ".join(failures))
        return list(products.values())


def _product_urls(page: str) -> list[str]:
    soup = BeautifulSoup(page, "html.parser")
    urls: list[str] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or script.get_text())
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(data, dict) or data.get("@type") != "ItemList":
            continue
        for entry in data.get("itemListElement", []):
            if not isinstance(entry, dict):
                continue
            url = str(entry.get("url", "")).strip()
            if url:
                urls.append(_dutch_url(url))
    return list(dict.fromkeys(urls))


def _dutch_url(url: str) -> str:
    parts = urlsplit(url)
    path = parts.path if parts.path.startswith("/nl-nl/") else "/nl-nl" + parts.path
    return urlunsplit((parts.scheme or "https", parts.netloc or "www.delonghi.com", path, parts.query, ""))


def _parse_product_page(page: str, page_url: str) -> Product:
    return parse_delonghi_product_page(
        page,
        page_url,
        site="De'Longhi NL",
        unavailable_markers=("breng mij op de hoogte",),
        available_delivery="Levering binnen 2-4 werkdagen",
        unavailable_delivery="Niet op voorraad",
    )
