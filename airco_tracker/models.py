from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlsplit

from .url_security import normalized_https_url, validate_affiliate_url, validate_product_url


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
    # Keep ``url`` as the canonical merchant URL because it is also the
    # durable inventory/state identity.  Affiliate links can change without
    # creating a false out-of-stock -> in-stock transition.
    affiliate_url: str | None = None

    def __post_init__(self) -> None:
        if not self.site.strip() or not self.name.strip():
            raise ValueError("Product site and name are required")
        object.__setattr__(self, "url", validate_product_url(self.url, site_id=self.site_id))
        if self.affiliate_url:
            try:
                affiliate_url = validate_affiliate_url(self.affiliate_url)
            except ValueError:
                # Affiliate enrichment is optional. A malformed/unapproved
                # redirect must never hide otherwise trustworthy inventory.
                affiliate_url = None
            object.__setattr__(self, "affiliate_url", affiliate_url)

    @property
    def site_id(self) -> str:
        return site_id_for(self.country, self.site)

    @property
    def purchase_url(self) -> str:
        affiliate_url = (self.affiliate_url or "").strip()
        if affiliate_url and _is_https_url(affiliate_url):
            return affiliate_url
        return self.url

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["country"] = normalize_country(self.country)
        data["site_id"] = self.site_id
        if not data.get("affiliate_url"):
            data.pop("affiliate_url", None)
        return data


def _is_https_url(value: str) -> bool:
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        return False
    try:
        parsed = urlsplit(normalized_https_url(value))
    except ValueError:
        return False
    return (
        parsed.scheme.lower() == "https"
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
    )
