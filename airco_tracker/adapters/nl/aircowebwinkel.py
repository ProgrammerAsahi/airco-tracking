from __future__ import annotations

import logging

from bs4 import BeautifulSoup

from ...fetch import Fetcher
from ...models import Product
from ..base import canonical_url, clean_text, parse_btu, verified_empty
from ..schema import first_offer, offer_price, product_json_ld, schema_in_stock
from ..sitemap import sitemap_locations


LOG = logging.getLogger(__name__)


class AircoWebwinkelAdapter:
    """Airco-Webwinkel — WooCommerce specialist store discovered via its
    robots-advertised product sitemap."""

    site = "Airco-Webwinkel"
    sitemap_url = "https://www.airco-webwinkel.nl/product-sitemap.xml"

    def __init__(self, fetcher: Fetcher) -> None:
        self.fetcher = fetcher

    def fetch_products(self) -> list[Product]:
        locations = sitemap_locations(
            self.fetcher.get_bytes(
                self.sitemap_url,
                allowed_content_types=("application/xml", "text/xml", "text/plain"),
                maximum_response_bytes=8 * 1024 * 1024,
            ),
            site=self.site,
        )
        if not locations:
            raise RuntimeError("Airco-Webwinkel product sitemap contained no product URLs")
        urls = [u for u in locations if _is_airco_url(u)]
        # Seasonal aircos can disappear from the sitemap. A healthy sitemap
        # without airco candidates is a legitimate empty snapshot: a restock
        # will reappear and be alerted as first-seen stock.
        products: dict[str, Product] = {}
        failures: list[str] = []
        for url in urls:
            try:
                product = _parse_product_page(self.fetcher.get(url), url)
            except Exception as exc:
                failures.append(f"{url}: {exc}")
                LOG.warning("Airco-Webwinkel product check failed for %s: %s", url, exc)
                continue
            if product is not None:
                products[product.url] = product
        if urls and not products:
            raise RuntimeError("Airco-Webwinkel product pages could not be parsed: " + "; ".join(failures))
        if not urls:
            return verified_empty(
                self,
                source="official_product_sitemap",
                signal="healthy sitemap contained zero mobile-airco candidates",
            )
        return list(products.values())


def _is_airco_url(url: str) -> bool:
    lower = url.lower()
    return "mobiele-airco" in lower or "mobiele-aircos" in lower


def _parse_product_page(page: str, page_url: str) -> Product | None:
    soup = BeautifulSoup(page, "html.parser")
    data = product_json_ld(soup)
    name = str(data.get("name", "")).strip()
    if not name:
        raise RuntimeError("Airco-Webwinkel product data did not contain a name")
    offer = first_offer(data)
    if not offer:
        raise RuntimeError("Airco-Webwinkel product data did not contain an offer")
    available = schema_in_stock(offer)
    description = str(data.get("description", ""))
    text = clean_text(soup)
    return Product(
        site="Airco-Webwinkel",
        name=name,
        url=canonical_url(page_url, str(offer.get("url") or data.get("url") or page_url)),
        available=available,
        price_eur=offer_price(offer),
        delivery="Op voorraad" if available else "Uitverkocht",
        btu=parse_btu(f"{name} {description} {text}"),
    )
