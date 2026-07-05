from __future__ import annotations

from bs4 import BeautifulSoup

from ...models import Product
from ..base import canonical_url, clean_text, parse_btu
from ..schema import first_offer, offer_price, product_json_ld, schema_in_stock


class SolagoAdapter:
    """Solago — Shopify storefront; discover products from the collection page and
    read stock/price from each product page's JSON-LD, while treating pre-orders
    and future ship dates as unavailable (AGENTS.md)."""

    site = "Solago"
    collection_url = "https://solago.nl/collections/airconditioningsystemen"

    def __init__(self, fetcher) -> None:
        self.fetcher = fetcher

    def fetch_products(self) -> list[Product]:
        page = self.fetcher.get(self.collection_url)
        soup = BeautifulSoup(page, "html.parser")
        urls = _product_urls(soup, self.collection_url)
        if not urls:
            raise RuntimeError("Solago collection did not contain product links")
        products: dict[str, Product] = {}
        for url in sorted(urls):
            try:
                product = _parse_product_page(self.fetcher.get(url), url)
            except Exception as exc:  # Keep going if one product page fails.
                continue
            if product is not None:
                products[product.url] = product
        if not products:
            raise RuntimeError("Solago product pages could not be parsed")
        return list(products.values())


def _product_urls(soup: BeautifulSoup, page_url: str) -> set[str]:
    urls: set[str] = set()
    for link in soup.select('a[href*="/products/"]'):
        href = str(link.get("href", ""))
        if "/products/" in href:
            urls.add(canonical_url(page_url, href))
    return urls


def _parse_product_page(page: str, page_url: str) -> Product | None:
    soup = BeautifulSoup(page, "html.parser")
    data = product_json_ld(soup)
    name = str(data.get("name", "")).strip()
    if not name or not _is_portable_airco(name):
        return None
    offer = first_offer(data)
    if not offer:
        raise RuntimeError("Solago product data did not contain an offer")
    schema_available = schema_in_stock(offer)
    text = clean_text(soup)
    # Shopify themes may mark a pre-order or future ship date while JSON-LD
    # still reports InStock; the page text is authoritative for availability.
    lower = text.lower()
    preorder = any(
        marker in lower
        for marker in ("voorbestelling", "pre-order", "levering vanaf", "verzending vanaf")
    )
    available = schema_available
    presale = preorder
    description = str(data.get("description", ""))
    return Product(
        site="Solago",
        name=name,
        url=canonical_url(page_url, str(offer.get("url") or data.get("url") or page_url)),
        available=available,
        price_eur=offer_price(offer),
        delivery="Voorbestelling" if presale else ("Op voorraad" if available else "Niet op voorraad"),
        btu=parse_btu(f"{name} {description} {text}"),
        presale=presale,
    )


def _is_portable_airco(name: str) -> bool:
    lower = name.lower()
    # "PortaSplit" is a portable split unit (compressor + portable indoor unit)
    # and is a genuine tracked form factor; only fixed split is excluded.
    excluded = (
        "aircooler",
        "luchtkoeler",
        "ventilator",
        "split airco",
        "split-unit",
        "mini-split",
        "raamafdichting",
    )
    if any(term in lower for term in excluded):
        return False
    return "airconditioner" in lower or "airco" in lower or "portasplit" in lower
