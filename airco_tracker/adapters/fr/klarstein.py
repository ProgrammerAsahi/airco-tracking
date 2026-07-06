from __future__ import annotations

from bs4 import BeautifulSoup

from ...models import Product
from ..base import Adapter, canonical_url, clean_text, is_presale_delivery, parse_btu
from .common import is_real_air_conditioner_fr, parse_french_price


class KlarsteinFranceAdapter(Adapter):
    site = "Klarstein France"
    urls = ("https://www.klarstein.fr/index.php?cl=search&searchparam=climatiseur%20mobile",)

    def parse(self, soup: BeautifulSoup, page_url: str) -> list[Product]:
        products: dict[str, Product] = {}
        for card in soup.select("form.productTeaser"):
            link = card.select_one("a.card-product__content-title[href]")
            if link is None:
                continue
            name = clean_text(link)
            text = clean_text(card)
            if not is_real_air_conditioner_fr(name, text):
                continue
            url = canonical_url(page_url, str(link.get("href", "")))
            stock = str(card.get("data-stock", "")).strip().casefold()
            delivery_node = card.select_one(".card-product__content-label")
            delivery = clean_text(delivery_node) if delivery_node else ""
            lower = f"{stock} {delivery} {text}".casefold()
            presale = is_presale_delivery(lower)
            available = presale or stock in {"in-stock", "instock", "available"} or (
                "non disponible" not in lower and "out-of-stock" not in lower and "ajouter au panier" in lower
            )
            products[url] = Product(
                site=self.site,
                name=name,
                url=url,
                available=available,
                price_eur=parse_french_price(text),
                delivery=delivery or ("Disponible" if available else "Non disponible"),
                btu=parse_btu(name),
                presale=presale,
            )
        return list(products.values())
