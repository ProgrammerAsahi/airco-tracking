from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import DEFAULT_COUNTRY, Product, normalize_country, product_state_key, site_id_for


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "products": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise RuntimeError(f"Cannot read state file {path}: {exc}") from exc
    if not isinstance(data.get("products"), dict):
        raise RuntimeError(f"Invalid state file {path}")
    return data


def select_alerts(
    products: list[Product],
    old_state: dict[str, Any],
    *,
    alert_on_first_seen: bool,
    max_price_eur: float | None,
    min_btu: int | None,
) -> list[Product]:
    previous = old_state.get("products", {})
    alerts: list[Product] = []
    for product in products:
        old = previous.get(product_state_key(product.country, product.url)) or previous.get(product.url)
        old_alertable = (
            isinstance(old, dict)
            and bool(old.get("available", False))
            and not bool(old.get("presale", False))
        )
        became_available = product.available and not product.presale and (
            (old is None and alert_on_first_seen)
            or (old is not None and not old_alertable)
        )
        # Unknown prices remain eligible so a temporary parsing gap cannot hide
        # newly available stock. The recipient can verify the final price before buying.
        within_price = (
            max_price_eur is None
            or product.price_eur is None
            or product.price_eur <= max_price_eur
        )
        enough_power = min_btu is None or product.btu is None or product.btu >= min_btu
        if became_available and within_price and enough_power:
            alerts.append(product)
    return alerts


def updated_state(
    old_state: dict[str, Any],
    products: list[Product],
    *,
    checked_sites: set[str] | None = None,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    records = dict(old_state.get("products", {}))
    seen_keys = {product_state_key(product.country, product.url) for product in products}
    seen_urls = {product.url for product in products}
    if checked_sites is not None:
        # Seasonal shops may remove sold-out products from their category or
        # sitemap. Only a successful retailer check may mark a missing product
        # unavailable; a failed check keeps its previous state.
        for url, old_record in list(records.items()):
            if (
                url not in seen_keys
                and url not in seen_urls
                and isinstance(old_record, dict)
                and (
                    _record_site_id(old_record) in checked_sites
                    or old_record.get("site") in checked_sites
                )
            ):
                record = dict(old_record)
                record["available"] = False
                record["delivery"] = "Niet meer in het actuele assortiment"
                record["last_seen"] = now
                records[url] = record
    for product in products:
        record = product.to_dict()
        record["last_seen"] = now
        key = product_state_key(product.country, product.url)
        old_record = records.get(key) or records.get(product.url)
        old_generation = _availability_generation(old_record)
        old_alertable = _record_is_alertable(old_record)
        new_alertable = product.available and not product.presale
        record["availability_generation"] = (
            old_generation + 1 if new_alertable and not old_alertable else old_generation
        )
        if key != product.url:
            records.pop(product.url, None)
        records[key] = record
    return {"version": 1, "updated_at": now, "products": records}


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def _record_site_id(record: dict[str, Any]) -> str | None:
    explicit = record.get("site_id")
    if isinstance(explicit, str) and explicit:
        return explicit
    site = record.get("site")
    if not isinstance(site, str) or not site:
        return None
    country = normalize_country(str(record.get("country") or DEFAULT_COUNTRY))
    return site_id_for(country, site)


def _availability_generation(record: Any) -> int:
    if not isinstance(record, dict):
        return 0
    try:
        return max(0, int(record.get("availability_generation") or 0))
    except (TypeError, ValueError):
        return 0


def _record_is_alertable(record: Any) -> bool:
    return (
        isinstance(record, dict)
        and bool(record.get("available", False))
        and not bool(record.get("presale", False))
    )
