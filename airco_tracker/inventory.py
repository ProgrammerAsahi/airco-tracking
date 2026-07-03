from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Iterable

from .models import Product


EMPTY_INVENTORY: dict[str, Any] = {
    "version": 1,
    "updated_at": None,
    "refresh_interval_seconds": 600,
    "site_count": 0,
    "stale_site_count": 0,
    "available_product_count": 0,
    "sites": {},
}


def empty_inventory() -> dict[str, Any]:
    return deepcopy(EMPTY_INVENTORY)


def updated_inventory(
    old_inventory: dict[str, Any],
    products: Iterable[Product],
    *,
    all_sites: set[str],
    checked_sites: set[str],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build a current available-stock snapshot without alert filters.

    A successfully checked site replaces its previous snapshot, including with
    an empty list. A failed site retains its last successful product list and is
    explicitly marked stale.
    """
    timestamp = (now or datetime.now(timezone.utc)).isoformat()
    old_sites = old_inventory.get("sites", {})
    if not isinstance(old_sites, dict):
        old_sites = {}

    available_by_site: dict[str, list[dict[str, Any]]] = {
        site: [] for site in checked_sites
    }
    for product in products:
        if product.available and product.site in checked_sites:
            available_by_site.setdefault(product.site, []).append(product.to_dict())

    sites: dict[str, dict[str, Any]] = {}
    for site in sorted(all_sites):
        if site in checked_sites:
            site_products = sorted(
                available_by_site.get(site, []),
                key=lambda item: (str(item.get("name", "")).casefold(), str(item.get("url", ""))),
            )
            sites[site] = {
                "status": "ok",
                "stale": False,
                "last_attempt_at": timestamp,
                "last_success_at": timestamp,
                "available_product_count": len(site_products),
                "products": site_products,
            }
            continue

        previous = old_sites.get(site, {})
        if not isinstance(previous, dict):
            previous = {}
        retained_products = previous.get("products", [])
        if not isinstance(retained_products, list):
            retained_products = []
        retained_products = deepcopy(retained_products)
        sites[site] = {
            "status": "error",
            "stale": True,
            "last_attempt_at": timestamp,
            "last_success_at": previous.get("last_success_at"),
            "available_product_count": len(retained_products),
            "products": retained_products,
        }

    return {
        "version": 1,
        "updated_at": timestamp,
        "refresh_interval_seconds": 600,
        "site_count": len(sites),
        "stale_site_count": sum(bool(item["stale"]) for item in sites.values()),
        "available_product_count": sum(
            int(item["available_product_count"]) for item in sites.values()
        ),
        "sites": sites,
    }
