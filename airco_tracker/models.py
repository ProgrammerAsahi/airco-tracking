from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
