from __future__ import annotations

from ...models import Product
from ..shared.lidl import LidlSitemapAdapter, parse_lidl_product_page, product_urls_from_sitemap


class LidlFranceAdapter(LidlSitemapAdapter):
    site = "Lidl France"
    sitemap_url = "https://www.lidl.fr/p/export/FR/fr/product_sitemap.xml.gz"
    include_url_terms = ("climatiseur",)
    exclude_url_terms = ("rafraichisseur", "refroidisseur", "ventilateur")
    invalid_sitemap_message = "Lidl France product sitemap was invalid"
    parse_failure_message = "Lidl France product pages could not be parsed"
    available_delivery = "En ligne"
    unavailable_delivery = "Épuisé en ligne"


def _product_urls(content: bytes) -> list[str]:
    return product_urls_from_sitemap(
        content,
        include_terms=LidlFranceAdapter.include_url_terms,
        exclude_terms=LidlFranceAdapter.exclude_url_terms,
        invalid_message=LidlFranceAdapter.invalid_sitemap_message,
        site=LidlFranceAdapter.site,
    )


def _parse_product_page(page: str, page_url: str) -> Product:
    return parse_lidl_product_page(
        page,
        page_url,
        site=LidlFranceAdapter.site,
        available_delivery=LidlFranceAdapter.available_delivery,
        unavailable_delivery=LidlFranceAdapter.unavailable_delivery,
    )
