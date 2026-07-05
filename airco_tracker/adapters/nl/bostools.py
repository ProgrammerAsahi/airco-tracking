from __future__ import annotations

import re

from bs4 import BeautifulSoup, Tag

from ...models import Product
from ..base import Adapter, canonical_url, clean_text, is_presale_delivery, parse_btu, parse_price


class BostoolsAdapter(Adapter):
    """Bostools — WooCommerce mobile and caravan air-conditioner categories."""

    site = "Bostools"
    urls = (
        "https://www.bostools.nl/airconditioning/mobiele-airco",
        "https://www.bostools.nl/airconditioning/caravan-airco",
    )

    def parse(self, soup: BeautifulSoup, page_url: str) -> list[Product]:
        products: dict[str, Product] = {}
        for card in soup.select("ul.products li.product"):
            link = card.select_one("a.woocommerce-loop-product__link[href]")
            heading = card.select_one(".woocommerce-loop-product__title")
            if link is None or heading is None:
                continue
            name = clean_text(heading)
            if not _is_portable_airco(name, page_url):
                continue
            href = str(link.get("href", ""))
            if not href:
                continue
            url = canonical_url(page_url, href)
            if url in products:
                continue

            text = clean_text(card)
            delivery_node = card.select_one(".stock")
            delivery = clean_text(delivery_node) if delivery_node else ""
            available, presale = _availability(card, delivery, text)
            products[url] = Product(
                site=self.site,
                name=name,
                url=url,
                available=available,
                price_eur=_retail_price(card),
                delivery=delivery or ("Op voorraad" if available else "Niet op voorraad"),
                btu=parse_btu(name) or parse_btu(url) or parse_btu(text),
                presale=presale,
            )
        return list(products.values())


def _retail_price(card: Tag) -> float | None:
    price = card.select_one(".price")
    if price is None:
        return parse_price(clean_text(card))
    amount = price.find(class_="woocommerce-Price-amount", recursive=False)
    if isinstance(amount, Tag):
        parsed = parse_price(clean_text(amount))
        if parsed is not None:
            return parsed
    exclusive = price.select_one(".price-ex")
    primary_text = clean_text(price)
    if exclusive is not None:
        primary_text = primary_text.replace(clean_text(exclusive), "", 1).strip()
    return parse_price(primary_text)


def _availability(card: Tag, delivery: str, card_text: str) -> tuple[bool, bool]:
    lower = f"{delivery} {card_text}".lower()
    classes = {str(value).lower() for value in card.get("class", [])}

    pickup_only = (
        "alleen af te halen",
        "alleen ophalen",
        "uitsluitend afhalen",
        "zonder doos",
        "showroommodel",
        "showroom model",
    )
    if any(marker in lower for marker in pickup_only):
        return False, False

    # A dated availability is orderable but not immediate stock. Keep it in the
    # dashboard's presale tab while suppressing stock-alert email.
    presale = "leverbaar vanaf" in lower or is_presale_delivery(delivery)
    if presale:
        return True, True

    sold_out = (
        "tijdelijk uitverkocht",
        "uitverkocht",
        "niet op voorraad",
        "niet leverbaar",
    )
    if any(marker in lower for marker in sold_out) or "outofstock" in classes:
        return False, False

    if "instock" in classes or "op voorraad" in lower:
        return True, False

    # WooCommerce uses onbackorder for short, orderable lead times as well as
    # dated presales. Only bounded business-day delivery is immediate enough.
    if "onbackorder" in classes and re.search(r"\b\d+\s*(?:-|–|tot)\s*\d+\s*werkdagen\b", lower):
        return True, False
    return False, False


def _is_portable_airco(name: str, page_url: str) -> bool:
    lower = name.lower()
    excluded = (
        "aircooler",
        "luchtkoeler",
        "ventilator",
        "wandmodel",
        "cassette",
        "plafond",
        "raamafdichting",
        "raam afsluiting",
        "raamafsluiting",
        "raamkit",
        "houder",
        "montage",
        "afvoerslang",
        "slang",
        "onderdeel",
        "ombouw",
    )
    if any(term in lower for term in excluded):
        return False
    if "/caravan-airco" in page_url:
        return "airco" in lower or "air conditioner" in lower
    return (
        "mobiele airco" in lower
        or "mobiele airconditioner" in lower
        or "portasplit" in lower
        or "porta split" in lower
    )
