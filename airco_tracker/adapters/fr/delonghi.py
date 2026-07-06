from __future__ import annotations

import logging
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from ...fetch import Fetcher
from ...models import Product
from ..shared.delonghi import parse_delonghi_product_page


LOG = logging.getLogger(__name__)


class DelonghiFranceAdapter:
    site = "De'Longhi France"
    search_url = "https://www.delonghi.com/fr-fr/search?q=climatiseur%20mobile"

    def __init__(self, fetcher: Fetcher) -> None:
        self.fetcher = fetcher

    def fetch_products(self) -> list[Product]:
        urls = _product_urls(self.fetcher.get(self.search_url), self.search_url)
        if not urls:
            raise RuntimeError("De'Longhi France search contained no portable air conditioners")
        products: dict[str, Product] = {}
        failures: list[str] = []
        for url in urls:
            try:
                product = _parse_product_page(self.fetcher.get(url), url)
            except Exception as exc:
                failures.append(f"{url}: {exc}")
                LOG.warning("De'Longhi France product check failed for %s: %s", url, exc)
                continue
            products[product.url] = product
        if not products:
            raise RuntimeError("De'Longhi France product pages could not be parsed: " + "; ".join(failures))
        return list(products.values())


def _product_urls(page: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(page, "html.parser")
    urls: list[str] = []
    for link in soup.select('a[href*="/fr-fr/p/"]'):
        url = _canonical_product_url(urljoin(base_url, str(link.get("href", ""))))
        if "/p/climatiseurs-mobiles-" in url.casefold():
            urls.append(url)
    return list(dict.fromkeys(urls))


def _canonical_product_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme or "https", parts.netloc or "www.delonghi.com", parts.path, parts.query, ""))


def _parse_product_page(page: str, page_url: str) -> Product:
    return parse_delonghi_product_page(
        page,
        page_url,
        site="De'Longhi France",
        unavailable_markers=("en rupture de stock",),
        available_delivery="Livraison standard",
        unavailable_delivery="En rupture de stock",
    )
