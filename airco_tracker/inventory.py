from __future__ import annotations

from collections.abc import Iterable as IterableABC
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from .adapters.base import with_detected_presale
from .models import Product, DEFAULT_COUNTRY, normalize_country, site_id_for


# Version 1 is the compatible country-aware inventory contract currently
# consumed by airco-tracking-web. Only bump this for a breaking schema change;
# additive fields stay on the current version so frontend/backend deploy order
# cannot briefly break production.
INVENTORY_SCHEMA_VERSION = 1

# Stale retailer snapshots remain available as diagnostic context, but are
# never counted as purchasable stock.  Consumers may use this age threshold to
# hide very old diagnostic product lists while still showing that the retailer
# check is unhealthy.
STALE_DIAGNOSTIC_MAX_AGE_SECONDS = 24 * 60 * 60


EMPTY_INVENTORY: dict[str, Any] = {
    "version": INVENTORY_SCHEMA_VERSION,
    "updated_at": None,
    "refresh_interval_seconds": 600,
    "site_count": 0,
    "verified_site_count": 0,
    "stale_site_count": 0,
    "available_product_count": 0,
    "immediate_product_count": 0,
    "presale_product_count": 0,
    "inventory_confidence": "unavailable",
    "stale_diagnostic_max_age_seconds": STALE_DIAGNOSTIC_MAX_AGE_SECONDS,
    "sites": {},
}


def empty_inventory() -> dict[str, Any]:
    return deepcopy(EMPTY_INVENTORY)


def updated_inventory(
    old_inventory: dict[str, Any],
    products: Iterable[Product],
    *,
    all_sites: set[str] | Mapping[str, Mapping[str, Any]],
    checked_sites: set[str],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build a current available-stock snapshot without alert filters.

    A successfully checked site replaces its previous snapshot, including with
    an empty list. A failed site retains its last successful product list and is
    explicitly marked stale.
    """
    current_time = now or datetime.now(timezone.utc)
    timestamp = current_time.isoformat()
    old_sites = old_inventory.get("sites", {})
    if not isinstance(old_sites, dict):
        old_sites = {}

    site_map = _normalise_site_map(all_sites)
    checked_site_ids = _normalise_checked_sites(checked_sites, site_map)
    available_by_site: dict[str, list[dict[str, Any]]] = {site_id: [] for site_id in checked_site_ids}
    for product in products:
        if not product.available:
            continue
        product = replace(product, country=normalize_country(product.country))
        site_id = product.site_id
        if site_id in checked_site_ids:
            # Centralized presale detection: if the adapter did not already
            # flag the product as presale, check the delivery text for
            # presale markers (multi-week lead times, pre-order, etc.).
            product = with_detected_presale(product)
            available_by_site.setdefault(site_id, []).append(product.to_dict())

    sites: dict[str, dict[str, Any]] = {}
    for site_id, site_identity in sorted(site_map.items(), key=lambda item: (item[1]["site"].casefold(), item[0])):
        if site_id in checked_site_ids:
            site_products = sorted(
                available_by_site.get(site_id, []),
                key=lambda item: (str(item.get("name", "")).casefold(), str(item.get("url", ""))),
            )
            immediate_count = _immediate_count(site_products)
            presale_count = _presale_count(site_products)
            sites[site_id] = {
                "status": "ok",
                "stale": False,
                "freshness": "verified",
                "counts_toward_totals": True,
                "stale_age_seconds": 0,
                "stale_too_old": False,
                "country": site_identity["country"],
                "site": site_identity["site"],
                "site_id": site_id,
                "delivery_coverage": site_identity["delivery_coverage"],
                "last_attempt_at": timestamp,
                "last_success_at": timestamp,
                "available_product_count": len(site_products),
                "immediate_product_count": immediate_count,
                "presale_product_count": presale_count,
                "products": site_products,
            }
            continue

        previous = old_sites.get(site_id)
        if not isinstance(previous, dict):
            # Backward compatibility for snapshots produced before site IDs
            # were introduced, where sites were keyed by display name only.
            previous = old_sites.get(site_identity["site"], {})
        if not isinstance(previous, dict):
            previous = {}
        stale_age_seconds = _age_seconds(previous.get("last_success_at"), now=current_time)
        stale_too_old = stale_age_seconds is None or stale_age_seconds > STALE_DIAGNOSTIC_MAX_AGE_SECONDS
        retained_products = previous.get("products", [])
        if not isinstance(retained_products, list):
            retained_products = []
        retained_products = [
            _normalise_product_record(product, site_identity)
            for product in deepcopy(retained_products)
            if isinstance(product, dict)
        ]
        # A stale snapshot is useful for short-lived diagnostics, but old
        # product links and prices must not remain in the paid API forever.
        # Keep the retailer health/timestamps while scrubbing the commercial
        # payload after the documented diagnostic window.
        if stale_too_old:
            retained_products = []
        immediate_count = _immediate_count(retained_products)
        presale_count = _presale_count(retained_products)
        sites[site_id] = {
            "status": "error",
            "stale": True,
            "freshness": "stale",
            "counts_toward_totals": False,
            "stale_age_seconds": stale_age_seconds,
            "stale_too_old": stale_too_old,
            "country": site_identity["country"],
            "site": site_identity["site"],
            "site_id": site_id,
            "delivery_coverage": site_identity["delivery_coverage"],
            "last_attempt_at": timestamp,
            "last_success_at": previous.get("last_success_at"),
            "available_product_count": len(retained_products),
            "immediate_product_count": immediate_count,
            "presale_product_count": presale_count,
            "products": retained_products,
        }

    verified_sites = [item for item in sites.values() if bool(item["counts_toward_totals"])]
    available_product_count = sum(int(item["available_product_count"]) for item in verified_sites)
    immediate_product_count = sum(int(item["immediate_product_count"]) for item in verified_sites)
    presale_product_count = sum(int(item["presale_product_count"]) for item in verified_sites)
    stale_site_count = sum(bool(item["stale"]) for item in sites.values())
    if not verified_sites:
        confidence = "unavailable"
    elif stale_site_count:
        confidence = "partial"
    else:
        confidence = "verified"
    return {
        "version": INVENTORY_SCHEMA_VERSION,
        "updated_at": timestamp,
        "refresh_interval_seconds": 600,
        "site_count": len(sites),
        "verified_site_count": len(verified_sites),
        "stale_site_count": stale_site_count,
        "available_product_count": available_product_count,
        "immediate_product_count": immediate_product_count,
        "presale_product_count": presale_product_count,
        "inventory_confidence": confidence,
        "stale_diagnostic_max_age_seconds": STALE_DIAGNOSTIC_MAX_AGE_SECONDS,
        "sites": sites,
    }


def _normalise_site_map(all_sites: set[str] | Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    if isinstance(all_sites, Mapping):
        result: dict[str, dict[str, Any]] = {}
        for raw_key, raw_identity in all_sites.items():
            identity = raw_identity if isinstance(raw_identity, Mapping) else {}
            site = str(identity.get("site") or raw_key).strip()
            country = normalize_country(str(identity.get("country") or DEFAULT_COUNTRY))
            site_id = str(identity.get("site_id") or raw_key or site_id_for(country, site)).strip()
            if not site_id:
                site_id = site_id_for(country, site)
            result[site_id] = {
                "country": country,
                "site": site,
                "site_id": site_id,
                "delivery_coverage": _normalise_delivery_coverage_record(
                    identity.get("delivery_coverage"),
                    default_country=country,
                ),
            }
        return result
    return {
        site_id_for(DEFAULT_COUNTRY, str(site)): {
            "country": DEFAULT_COUNTRY,
            "site": str(site),
            "site_id": site_id_for(DEFAULT_COUNTRY, str(site)),
            "delivery_coverage": [DEFAULT_COUNTRY],
        }
        for site in all_sites
    }


def _normalise_checked_sites(checked_sites: set[str], site_map: Mapping[str, Mapping[str, str]]) -> set[str]:
    normalised: set[str] = set()
    display_name_index: dict[str, list[str]] = {}
    for site_id, identity in site_map.items():
        display_name_index.setdefault(str(identity.get("site", "")), []).append(site_id)
    for checked in checked_sites:
        if checked in site_map:
            normalised.add(checked)
            continue
        matches = display_name_index.get(checked)
        if matches:
            normalised.update(matches)
            continue
        normalised.add(site_id_for(DEFAULT_COUNTRY, checked))
    return normalised


def _normalise_product_record(product: dict[str, Any], site_identity: Mapping[str, Any]) -> dict[str, Any]:
    country = normalize_country(str(product.get("country") or site_identity["country"]))
    site = str(product.get("site") or site_identity["site"])
    normalised = dict(product)
    normalised["country"] = country
    normalised["site"] = site
    normalised["site_id"] = str(product.get("site_id") or site_id_for(country, site))
    normalised["presale"] = bool(product.get("presale", False))
    return normalised


def _normalise_delivery_coverage_record(value: Any, *, default_country: str) -> list[str]:
    if value is None:
        return [normalize_country(default_country)]
    if isinstance(value, str):
        raw_tokens = [value]
    elif isinstance(value, IterableABC):
        raw_tokens = [str(token) for token in value]
    else:
        raw_tokens = []
    tokens = sorted({token.strip().lower() for token in raw_tokens if token.strip()})
    return tokens or [normalize_country(default_country)]


def _immediate_count(products: Iterable[Mapping[str, Any]]) -> int:
    return sum(1 for product in products if not bool(product.get("presale", False)))


def _presale_count(products: Iterable[Mapping[str, Any]]) -> int:
    return sum(1 for product in products if bool(product.get("presale", False)))


def _age_seconds(value: Any, *, now: datetime) -> int | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0, int((now.astimezone(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()))
