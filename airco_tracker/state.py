from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import Product


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
        old = previous.get(product.url)
        became_available = product.available and (
            (old is None and alert_on_first_seen)
            or (old is not None and not old.get("available", False))
        )
        within_price = max_price_eur is None or (
            product.price_eur is not None and product.price_eur <= max_price_eur
        )
        enough_power = min_btu is None or product.btu is None or product.btu >= min_btu
        if became_available and within_price and enough_power:
            alerts.append(product)
    return alerts


def updated_state(old_state: dict[str, Any], products: list[Product]) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    records = dict(old_state.get("products", {}))
    for product in products:
        record = product.to_dict()
        record["last_seen"] = now
        records[product.url] = record
    return {"version": 1, "updated_at": now, "products": records}


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
