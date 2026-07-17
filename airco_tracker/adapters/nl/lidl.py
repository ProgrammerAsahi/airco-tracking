from __future__ import annotations

from ...models import Product
from ..shared.lidl import LidlSitemapAdapter, parse_lidl_product_page, product_urls_from_sitemap


class LidlAdapter(LidlSitemapAdapter):
    """Discover Lidl products through its robots-advertised product sitemap."""

    site = "Lidl"
    sitemap_url = "https://www.lidl.nl/p/export/NL/nl/product_sitemap.xml.gz"
    include_url_terms = ("airco", "aircondition")
    exclude_url_terms = ("aircooler", "luchtkoeler", "ventilator")
    invalid_sitemap_message = "Lidl product sitemap was invalid"
    parse_failure_message = "Lidl product pages could not be parsed"
    available_delivery = "Online op voorraad"
    unavailable_delivery = "Online uitverkocht"


def _product_urls(content: bytes) -> list[str]:
    return product_urls_from_sitemap(
        content,
        include_terms=LidlAdapter.include_url_terms,
        exclude_terms=LidlAdapter.exclude_url_terms,
        invalid_message=LidlAdapter.invalid_sitemap_message,
    )


def _parse_product_page(page: str, page_url: str) -> Product:
    return parse_lidl_product_page(
        page,
        page_url,
        site=LidlAdapter.site,
        available_delivery=LidlAdapter.available_delivery,
        unavailable_delivery=LidlAdapter.unavailable_delivery,
    )
