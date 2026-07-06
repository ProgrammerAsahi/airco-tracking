from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


DEFAULT_COUNTRY = "nl"


def normalize_country(country: str | None) -> str:
    code = (country or DEFAULT_COUNTRY).strip().lower()
    return code or DEFAULT_COUNTRY


def site_id_for(country: str | None, site: str) -> str:
    return f"{normalize_country(country)}:{site.strip()}"


def product_state_key(country: str | None, url: str) -> str:
    return f"{normalize_country(country)}:{url}"


@dataclass(frozen=True)
class Product:
    site: str
    name: str
    url: str
    available: bool
    price_eur: float | None = None
    delivery: str | None = None
    btu: int | None = None
    presale: bool = False
    country: str = DEFAULT_COUNTRY

    @property
    def site_id(self) -> str:
        return site_id_for(self.country, self.site)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["country"] = normalize_country(self.country)
        data["site_id"] = self.site_id
        return data
