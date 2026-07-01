from __future__ import annotations

import logging

from bs4 import BeautifulSoup

from ..fetch import Fetcher
from ..models import Product
from .base import canonical_url, clean_text, parse_btu, parse_price
from .schema import first_offer, offer_price, product_json_ld, schema_in_stock
from .sitemap import sitemap_locations


LOG = logging.getLogger(__name__)


class AlternateAdapter:
    """Discover current Alternate products through its robots-advertised sitemap."""

    site = "Alternate.nl"
    sitemap_url = "https://www.alternate.nl/sitemap.xml.gz"

    def __init__(self, fetcher: Fetcher) -> None:
        self.fetcher = fetcher

    def fetch_products(self) -> list[Product]:
        product_urls: set[str] = set()
        for sitemap_url in self._child_sitemaps():
            for url in sitemap_locations(self._get_bytes(sitemap_url)):
                lower = url.lower()
                if "/html/product/" in lower and (
                    "airconditioner" in lower or "mobiele-airco" in lower
                ):
                    product_urls.add(url)

        # Alternate removes unavailable seasonal products from the sitemap.
        # An empty result is therefore meaningful: a restock/new product will
        # reappear and be alerted as first-seen stock.
        products: dict[str, Product] = {}
        failures: list[str] = []
        for url in sorted(product_urls):
            try:
                product = _parse_product_page(self.fetcher.get(url), url)
            except Exception as exc:
                failures.append(f"{url}: {exc}")
                LOG.warning("Alternate product check failed for %s: %s", url, exc)
                continue
            products[product.url] = product
        if product_urls and not products:
            raise RuntimeError("Alternate product pages could not be parsed: " + "; ".join(failures))
        return list(products.values())

    def _child_sitemaps(self) -> list[str]:
        locations = sitemap_locations(self._get_bytes(self.sitemap_url))
        return [url for url in locations if "sitemap_article" in url]

    def _get_bytes(self, url: str) -> bytes:
        response = self.fetcher.session.get(url, timeout=self.fetcher.timeout)
        response.raise_for_status()
        return response.content


def _parse_product_page(page: str, page_url: str) -> Product:
    soup = BeautifulSoup(page, "html.parser")
    try:
        data = product_json_ld(soup)
    except RuntimeError:
        return _parse_product_html(soup, page_url)
    name = str(data.get("name", "")).strip()
    offer = first_offer(data)
    if not name or not offer:
        raise RuntimeError("Alternate product data did not contain a name and offer")
    description = str(data.get("description", ""))
    available = schema_in_stock(offer)
    return Product(
        site="Alternate.nl",
        name=name,
        url=canonical_url(page_url, str(offer.get("url") or data.get("url") or page_url)),
        available=available,
        price_eur=offer_price(offer),
        delivery="Op voorraad" if available else "Niet op voorraad",
        btu=parse_btu(f"{name} {description}"),
    )


def _parse_product_html(soup: BeautifulSoup, page_url: str) -> Product:
    heading = soup.find("h1")
    if heading is None:
        raise RuntimeError("Alternate product page did not contain product data")
    text = clean_text(soup)
    name = clean_text(heading)
    lower = text.lower()
    available = "op voorraad" in lower and "niet op voorraad" not in lower
    return Product(
        site="Alternate.nl",
        name=name,
        url=page_url,
        available=available,
        price_eur=parse_price(text),
        delivery="Op voorraad" if available else "Niet op voorraad",
        btu=parse_btu(text),
    )
