from __future__ import annotations

from typing import Any

from ...models import Product
from ..base import is_presale_delivery, parse_btu, parse_cooling_watts_btu
from ..shared.evolarshop import NostoCategoryAdapter
from .common import custom_fields, is_real_air_conditioner_fr, parse_float


class EvolarshopFranceAdapter(NostoCategoryAdapter):
    """Evolarshop France via the public Nosto category search endpoint."""

    site = "Evolarshop France"
    category_url = "https://www.evolarshop.fr/climatiseurs/climatiseur-mobile"
    category_path = "Climatisation/Climatiseur Portable"
    hit_fields = "productId name url price available availability customFields { key value }"

    def parse_hit(self, hit: dict[str, Any]) -> Product | None:
        return _parse_hit(hit)


def _parse_hit(hit: dict[str, Any]) -> Product | None:
    if not isinstance(hit, dict):
        return None
    name = str(hit.get("name", "")).strip()
    url = str(hit.get("url", "")).strip()
    fields = custom_fields(hit)
    details = " ".join(fields.get(key, "") for key in ("product_card_subtitle", "product_card_subtitle_ex_html"))
    if not name or not url or not is_real_air_conditioner_fr(name, details):
        return None
    delivery = fields.get("product_card_usp") or str(hit.get("availability", "")).strip()
    presale = is_presale_delivery(delivery)
    available = bool(hit.get("available")) or presale
    return Product(
        site="Evolarshop France",
        name=name,
        url=url,
        available=available,
        price_eur=parse_float(hit.get("price")),
        delivery=delivery or None,
        btu=parse_btu(f"{name} {details}") or parse_cooling_watts_btu(details),
        presale=presale,
    )
