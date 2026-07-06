from __future__ import annotations

from ..base import is_presale_delivery, parse_btu
from ..shared.klarstein import KlarsteinCardAdapter
from .common import is_real_air_conditioner_fr, parse_french_price


class KlarsteinFranceAdapter(KlarsteinCardAdapter):
    site = "Klarstein France"
    urls = ("https://www.klarstein.fr/index.php?cl=search&searchparam=climatiseur%20mobile",)

    def is_air_conditioner(self, name: str, text: str) -> bool:
        return is_real_air_conditioner_fr(name, text)

    def availability(self, *, stock: str, delivery: str, text: str) -> tuple[bool, bool]:
        lower = f"{stock} {delivery} {text}".casefold()
        presale = is_presale_delivery(lower)
        available = presale or stock in {"in-stock", "instock", "available"} or (
            "non disponible" not in lower and "out-of-stock" not in lower and "ajouter au panier" in lower
        )
        return available, presale

    def price(self, text: str) -> float | None:
        return parse_french_price(text)

    def delivery_text(self, *, delivery: str, available: bool) -> str:
        return delivery or ("Disponible" if available else "Non disponible")

    def btu(self, name: str, text: str) -> int | None:
        return parse_btu(name)
