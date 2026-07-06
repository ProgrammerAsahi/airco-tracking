from __future__ import annotations

from bs4 import BeautifulSoup

from ...models import Product
from ..base import clean_text, parse_btu, parse_cooling_watts_btu, parse_product_page_btu
from ..schema import first_offer, offer_price, product_json_ld, schema_in_stock


def parse_delonghi_product_page(
    page: str,
    page_url: str,
    *,
    site: str,
    unavailable_markers: tuple[str, ...],
    available_delivery: str,
    unavailable_delivery: str,
) -> Product:
    """Parse a De'Longhi PDP with country-specific stock text."""

    soup = BeautifulSoup(page, "html.parser")
    data = product_json_ld(soup)
    name = str(data.get("name", "")).strip()
    description = str(data.get("description", ""))
    offer = first_offer(data)
    if not name or not offer:
        raise RuntimeError(f"{site} product data did not contain a name and offer")
    text = clean_text(soup)
    lower_text = text.casefold()
    available = schema_in_stock(offer) and not any(marker in lower_text for marker in unavailable_markers)
    return Product(
        site=site,
        name=name,
        url=str(offer.get("url") or page_url),
        available=available,
        price_eur=offer_price(offer),
        delivery=available_delivery if available else unavailable_delivery,
        btu=(
            parse_btu(f"{name} {description} {text}")
            or parse_cooling_watts_btu(f"{description} {text}")
            or parse_product_page_btu(page)
        ),
    )

