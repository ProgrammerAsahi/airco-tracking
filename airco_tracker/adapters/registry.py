"""Country-based adapter registry.

Each country subpackage (e.g. ``nl``, ``fr``) exposes an ``ADAPTERS`` list of
adapter classes in its ``__init__``. This module aggregates them and exposes
:func:`load_adapter_classes`, which the CLI calls to instantiate the adapters
for the configured countries.

Adding a country only requires creating ``adapters/<country>/__init__.py`` with
an ``ADAPTERS`` list and registering it here; the CLI and tests do not change.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from ..models import normalize_country, site_id_for
from .fr import ADAPTERS as _FR_ADAPTERS
from .nl import ADAPTERS as _NL_ADAPTERS

REGION_DELIVERY_TOKENS = frozenset({"eu", "eea", "nordics", "benelux", "dach"})
_ISO2_RE = re.compile(r"^[a-z]{2}$")

# Map of country code -> ordered list of adapter classes.
_ADAPTERS_BY_COUNTRY: dict[str, list[type]] = {
    "fr": _FR_ADAPTERS,
    "nl": _NL_ADAPTERS,
}

# Explicit delivery coverage for registered sites. Tokens are lower-case ISO 3166-1 alpha-2
# country codes plus a small set of region aliases defined in
# REGION_DELIVERY_TOKENS. Keep entries conservative: only widen beyond the
# adapter's market country when delivery to that destination has been verified.
# Country-selector links alone are not enough evidence; they usually point to
# separate storefronts whose inventory and availability may differ.
_DELIVERY_COVERAGE_BY_SITE_ID: dict[str, frozenset[str]] = {
    "fr:Castorama": frozenset({"fr"}),
    "fr:Auchan": frozenset({"fr"}),
    "fr:Rue du Commerce": frozenset({"fr"}),
    "fr:Electro Dépôt France": frozenset({"fr"}),
    "fr:Costway France": frozenset({"fr"}),
    "fr:Maison Energy": frozenset({"fr"}),
    "fr:Create France": frozenset({"fr"}),
    "fr:Evolarshop France": frozenset({"fr"}),
    "fr:Klarstein France": frozenset({"fr"}),
    "fr:Trotec France": frozenset({"fr"}),
    "fr:De'Longhi France": frozenset({"fr"}),
    "fr:Lidl France": frozenset({"fr"}),
    "fr:Action France": frozenset({"fr"}),
    "nl:Coolblue": frozenset({"nl"}),
    "nl:MediaMarkt": frozenset({"nl"}),
    "nl:EP.nl": frozenset({"nl"}),
    "nl:Electro World": frozenset({"nl"}),
    "nl:Wehkamp": frozenset({"nl"}),
    "nl:Lidl": frozenset({"nl"}),
    "nl:GAMMA": frozenset({"nl"}),
    "nl:KARWEI": frozenset({"nl"}),
    "nl:Praxis": frozenset({"nl"}),
    "nl:Alternate.nl": frozenset({"nl"}),
    "nl:Trotec": frozenset({"nl"}),
    "nl:Klarstein": frozenset({"nl"}),
    "nl:FlinQ": frozenset({"nl"}),
    "nl:Action Webshop": frozenset({"nl"}),
    "nl:Expert.nl": frozenset({"nl"}),
    "nl:De'Longhi NL": frozenset({"nl"}),
    "nl:Obelink": frozenset({"nl"}),
    "nl:Kampeerwereld": frozenset({"nl", "be"}),
    "nl:Create NL": frozenset({"nl"}),
    "nl:Costway NL": frozenset({"nl"}),
    "nl:Evolarshop": frozenset({"nl"}),
    "nl:Airco voor in huis": frozenset({"nl"}),
    "nl:Solago": frozenset({"nl", "be"}),
    "nl:Hubo": frozenset({"nl"}),
    "nl:Vrijbuiter": frozenset({"nl", "be", "de"}),
    "nl:Klimaatshop": frozenset({"nl"}),
    "nl:Airco-Webwinkel": frozenset({"nl", "be", "lu", "de"}),
    "nl:Bostools": frozenset({"nl"}),
}


@dataclass(frozen=True)
class AdapterSpec:
    """A retailer adapter bound to an explicit country.

    Keeping country assignment in the registry avoids relying on module-name
    inference in the runtime path, which becomes fragile once adapters are
    shared across countries or generated dynamically in tests.
    """

    country: str
    adapter_class: type

    @property
    def site(self) -> str:
        return str(getattr(self.adapter_class, "site", "")).strip()

    @property
    def site_id(self) -> str:
        return site_id_for(self.country, self.site)

    @property
    def delivery_coverage(self) -> frozenset[str]:
        raw = _DELIVERY_COVERAGE_BY_SITE_ID.get(self.site_id)
        if raw is None:
            raw = getattr(self.adapter_class, "delivery_coverage", None)
        return normalize_delivery_coverage(raw, default_country=self.country)


def normalize_delivery_coverage(
    coverage: Iterable[str] | None,
    *,
    default_country: str,
) -> frozenset[str]:
    if coverage is None:
        return frozenset({normalize_country(default_country)})

    tokens = frozenset(str(token).strip().lower() for token in coverage if str(token).strip())
    if not tokens:
        return frozenset({normalize_country(default_country)})

    invalid = sorted(
        token
        for token in tokens
        if token not in REGION_DELIVERY_TOKENS and _ISO2_RE.fullmatch(token) is None
    )
    if invalid:
        raise ValueError(
            "Invalid delivery coverage token(s): "
            + ", ".join(invalid)
            + f"; expected ISO-2 countries or one of {', '.join(sorted(REGION_DELIVERY_TOKENS))}"
        )
    return tokens


def load_adapter_specs(countries: list[str]) -> list[AdapterSpec]:
    """Return country-bound adapter specs for the given country codes.

    The returned list is fail-fast validated so two adapters cannot silently
    collapse into the same inventory/state site key.
    """
    specs: list[AdapterSpec] = []
    seen_site_ids: dict[str, str] = {}
    for raw_country in countries:
        country = normalize_country(raw_country)
        adapters = _ADAPTERS_BY_COUNTRY.get(country)
        if adapters is None:
            raise ValueError(
                f"Unknown country {country!r}; registered: "
                f"{', '.join(sorted(_ADAPTERS_BY_COUNTRY))}"
            )
        for adapter_class in adapters:
            spec = AdapterSpec(country=country, adapter_class=adapter_class)
            if not spec.site:
                raise ValueError(f"Adapter {adapter_class.__name__} is missing a non-empty site name")
            _ = spec.delivery_coverage
            if spec.site_id in seen_site_ids:
                raise ValueError(
                    f"Duplicate adapter site_id {spec.site_id!r}: "
                    f"{seen_site_ids[spec.site_id]} and {adapter_class.__name__}"
                )
            seen_site_ids[spec.site_id] = adapter_class.__name__
            specs.append(spec)
    return specs


def load_adapter_classes(countries: list[str]) -> list[type]:
    """Return the ordered adapter classes for the given country codes."""
    return [spec.adapter_class for spec in load_adapter_specs(countries)]
