from __future__ import annotations

from ..base import parse_btu, parse_price
from ..shared.klarstein import KlarsteinCardAdapter


class KlarsteinAdapter(KlarsteinCardAdapter):
    site = "Klarstein"
    urls = (
        "https://www.klarstein.nl/Airconditioning/Airco/Mobiele-airco/"
        "?ldtype=infogrid&_artperpage=96",
    )

    def is_air_conditioner(self, name: str, text: str) -> bool:
        return _is_airconditioner(name)

    def availability(self, *, stock: str, delivery: str, text: str) -> tuple[bool, bool]:
        return stock in {"in-stock", "instock", "available"}, False

    def price(self, text: str) -> float | None:
        return parse_price(text)

    def delivery_text(self, *, delivery: str, available: bool) -> str:
        return delivery or ("Direct leverbaar" if available else "Niet beschikbaar")

    def btu(self, name: str, text: str) -> int | None:
        return parse_btu(name)


def _is_airconditioner(name: str) -> bool:
    lower = name.lower()
    excluded = ("aircooler", "luchtkoeler", "raamafdichting", "slang", "afstandsbediening")
    return (
        "mobiele airco" in lower or "mobiele airconditioner" in lower
    ) and not any(term in lower for term in excluded)
