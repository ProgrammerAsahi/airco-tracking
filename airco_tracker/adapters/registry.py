"""Country-based adapter registry.

Each country subpackage (e.g. ``nl``, ``fr``) exposes an ``ADAPTERS`` list of
adapter classes in its ``__init__``. This module aggregates them and exposes
:func:`load_adapter_classes`, which the CLI calls to instantiate the adapters
for the configured countries.

Adding a country only requires creating ``adapters/<country>/__init__.py`` with
an ``ADAPTERS`` list and registering it here; the CLI and tests do not change.
"""

from __future__ import annotations

from .nl import ADAPTERS as _NL_ADAPTERS

# Map of country code -> ordered list of adapter classes.
_ADAPTERS_BY_COUNTRY: dict[str, list[type]] = {
    "nl": _NL_ADAPTERS,
}


def load_adapter_classes(countries: list[str]) -> list[type]:
    """Return the ordered adapter classes for the given country codes."""
    classes: list[type] = []
    for country in countries:
        adapters = _ADAPTERS_BY_COUNTRY.get(country)
        if adapters is None:
            raise ValueError(
                f"Unknown country {country!r}; registered: "
                f"{', '.join(sorted(_ADAPTERS_BY_COUNTRY))}"
            )
        classes.extend(adapters)
    return classes
