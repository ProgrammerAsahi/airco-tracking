from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

from bs4 import BeautifulSoup, Tag

from ...models import Product
from ..base import clean_text, is_presale_delivery, parse_btu, parse_product_page_btu
from ..shared.evolarshop import NostoCategoryAdapter


LOG = logging.getLogger(__name__)


class EvolarshopAdapter(NostoCategoryAdapter):
    """Evolarshop — Hyva/Magento storefront rendered through public Nosto search."""

    site = "Evolarshop"
    category_url = "https://www.evolarshop.nl/airco-s/mobiele-airco"
    category_path = "Airco's/Mobiele Airco"

    def parse_hit(self, hit: dict[str, Any]) -> Product | None:
        return _parse_hit(hit)

    def enrich_products(self, products: list[Product]) -> list[Product]:
        enriched: list[Product] = []
        for product in products:
            if not product.available:
                enriched.append(product)
                continue
            try:
                page = self.fetcher.get(product.url)
            except Exception as exc:
                LOG.warning("Evolarshop detail enrichment failed for %s: %s", product.url, exc)
                enriched.append(product)
                continue
            enriched.append(_parse_detail_page(page, product))
        return enriched


def _parse_hit(hit: dict[str, Any]) -> Product | None:
    if not isinstance(hit, dict):
        return None
    name = str(hit.get("name", "")).strip()
    url = str(hit.get("url", "")).strip()
    if not name or not url or not _is_real_airco(name):
        return None
    available = bool(hit.get("available"))
    return Product(
        site="Evolarshop",
        name=name,
        url=url,
        available=available,
        price_eur=_price(hit.get("price")),
        delivery=str(hit.get("availability", "")).strip() or None,
        btu=parse_btu(name),
    )


def _parse_detail_page(page: str, product: Product) -> Product:
    delivery = _product_card_usp(page, product.url) or product.delivery
    btu = product.btu or parse_product_page_btu(page)
    presale = product.presale or bool(delivery and is_presale_delivery(delivery))
    return replace(product, delivery=delivery, btu=btu, presale=presale)


def _product_card_usp(page: str, product_url: str) -> str | None:
    soup = BeautifulSoup(page, "html.parser")
    scopes = _matching_nosto_products(soup, product_url)
    if not scopes:
        scopes = [soup]

    for scope in scopes:
        node = scope.find(class_="product_card_usp")
        if isinstance(node, Tag):
            text = clean_text(node)
            if text:
                return text

        for tag in scope.find_all(class_="tag"):
            text = clean_text(tag)
            if text.lower().startswith("product_card_usp:"):
                return text.split(":", 1)[1].strip()
    return None


def _matching_nosto_products(soup: BeautifulSoup, product_url: str) -> list[Tag]:
    matches: list[Tag] = []
    target = product_url.rstrip("/")
    for node in soup.select(".nosto_product"):
        if not isinstance(node, Tag):
            continue
        url_node = node.find(class_="url")
        if not isinstance(url_node, Tag):
            continue
        url = clean_text(url_node).rstrip("/")
        if url == target:
            matches.append(node)
    return matches


def _is_real_airco(name: str) -> bool:
    lower = name.lower()
    excluded = (
        "aircooler",
        "luchtkoeler",
        "ventilator",
        "zonder afvoerslang",  # no exhaust hose → not a compressor unit
        "raamafdichting",
    )
    return not any(term in lower for term in excluded) and "airco" in lower


def _price(value: Any) -> float | None:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None
