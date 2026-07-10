from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .models import Product, product_state_key


EVENT_SCHEMA_VERSION = 1
EVENT_TYPE_STOCK_AVAILABLE = "stock.available.v1"
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_DELIVERY_TOKEN = re.compile(r"^[a-z]{2,12}$")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def stock_event_id(product: Product, availability_generation: int) -> str:
    if availability_generation <= 0:
        raise ValueError("availability_generation must be positive")
    identity = (
        f"{EVENT_TYPE_STOCK_AVAILABLE}\0"
        f"{product_state_key(product.country, product.url)}\0"
        f"{availability_generation}"
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class StockAvailableEvent:
    event_id: str
    product: Product
    delivery_coverage: tuple[str, ...]
    availability_generation: int
    created_at: str = field(default_factory=utc_now_iso)
    test_only: bool = False
    target_recipient_ids: tuple[str, ...] = ()
    schema_version: int = EVENT_SCHEMA_VERSION
    event_type: str = EVENT_TYPE_STOCK_AVAILABLE

    @classmethod
    def for_product(
        cls,
        product: Product,
        *,
        availability_generation: int,
        delivery_coverage: set[str] | frozenset[str] | list[str] | tuple[str, ...],
    ) -> "StockAvailableEvent":
        return cls(
            event_id=stock_event_id(product, availability_generation),
            product=product,
            delivery_coverage=tuple(sorted({str(code).strip().lower() for code in delivery_coverage if code})),
            availability_generation=availability_generation,
        )

    @classmethod
    def test_event(
        cls,
        *,
        target_recipient_ids: list[str] | tuple[str, ...],
    ) -> "StockAvailableEvent":
        normalized_targets: list[str] = []
        for value in target_recipient_ids:
            candidate = value.strip()
            if not candidate:
                continue
            try:
                normalized = str(uuid.UUID(candidate))
            except (ValueError, AttributeError) as exc:
                raise ValueError("Pipeline-test targets must be opaque UUIDs") from exc
            normalized_targets.append(normalized)
        targets = tuple(dict.fromkeys(normalized_targets))
        if not targets:
            raise ValueError("A pipeline test must target at least one recipient")
        product = Product(
            site="Airco Tracker",
            name="Service Bus email pipeline test",
            url="https://airco-tracker.eu/",
            available=True,
            delivery="Synthetic test — no purchase required",
            btu=7000,
            country="nl",
        )
        return cls(
            event_id=hashlib.sha256(f"pipeline-test\0{uuid.uuid4()}".encode()).hexdigest(),
            product=product,
            delivery_coverage=("eu",),
            availability_generation=1,
            test_only=True,
            target_recipient_ids=targets,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "eventType": self.event_type,
            "eventId": self.event_id,
            "createdAt": self.created_at,
            "availabilityGeneration": self.availability_generation,
            "product": self.product.to_dict(),
            "deliveryCoverage": list(self.delivery_coverage),
            "testOnly": self.test_only,
            "targetRecipientIds": list(self.target_recipient_ids),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def from_json(cls, payload: str | bytes) -> "StockAvailableEvent":
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        try:
            data = json.loads(payload)
        except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Invalid stock-event JSON") from exc
        if not isinstance(data, dict):
            raise ValueError("Invalid stock-event payload")
        if type(data.get("schemaVersion")) is not int or data.get("schemaVersion") != EVENT_SCHEMA_VERSION:
            raise ValueError("Unsupported stock-event schemaVersion")
        if data.get("eventType") != EVENT_TYPE_STOCK_AVAILABLE:
            raise ValueError("Unsupported stock-event eventType")
        product_data = data.get("product")
        if not isinstance(product_data, dict):
            raise ValueError("Stock event is missing product")
        try:
            available = _required_bool(product_data, "available")
            presale = _optional_bool(product_data, "presale", False)
            generation_value = data["availabilityGeneration"]
            if isinstance(generation_value, bool) or not isinstance(generation_value, int):
                raise ValueError("availabilityGeneration must be an integer")
            product = Product(
                site=_required_string(product_data, "site"),
                name=_required_string(product_data, "name"),
                url=_required_string(product_data, "url"),
                available=available,
                price_eur=_optional_float(product_data.get("price_eur")),
                delivery=_optional_string(product_data.get("delivery")),
                btu=_optional_int(product_data.get("btu")),
                presale=presale,
                country=_optional_string(product_data.get("country")) or "nl",
            )
            coverage = _string_tuple(data.get("deliveryCoverage"), "deliveryCoverage")
            targets = _string_tuple(data.get("targetRecipientIds", []), "targetRecipientIds")
            event = cls(
                event_id=_required_string(data, "eventId"),
                product=product,
                delivery_coverage=tuple(value.lower() for value in coverage),
                availability_generation=generation_value,
                created_at=_required_string(data, "createdAt"),
                test_only=_optional_bool(data, "testOnly", False),
                target_recipient_ids=targets,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Invalid stock-event payload") from exc
        if not _SHA256_HEX.fullmatch(event.event_id) or event.availability_generation <= 0:
            raise ValueError("Invalid stock-event identity")
        if not event.product.available or event.product.presale:
            raise ValueError("Stock events must describe immediately available products")
        if not event.product.country or len(event.product.country.strip()) != 2:
            raise ValueError("Invalid stock-event product country")
        if not event.delivery_coverage or any(
            not _DELIVERY_TOKEN.fullmatch(value) for value in event.delivery_coverage
        ):
            raise ValueError("Invalid stock-event delivery coverage")
        _parse_created_at(event.created_at)
        if event.test_only and not event.target_recipient_ids:
            raise ValueError("Test events must contain explicit target recipients")
        if not event.test_only and event.target_recipient_ids:
            raise ValueError("Production events cannot contain target recipients")
        if event.test_only:
            if any(not _is_uuid(value) for value in event.target_recipient_ids):
                raise ValueError("Test-event targets must be opaque UUIDs")
        elif event.event_id != stock_event_id(event.product, event.availability_generation):
            raise ValueError("Stock-event identity does not match its product generation")
        return event


@dataclass(frozen=True)
class FanoutShardJob:
    event_id: str
    shard: int
    target_recipient_ids: tuple[str, ...] = ()
    schema_version: int = EVENT_SCHEMA_VERSION

    def to_json(self) -> str:
        return json.dumps(
            {
                "schemaVersion": self.schema_version,
                "eventId": self.event_id,
                "shard": self.shard,
                "targetRecipientIds": list(self.target_recipient_ids),
            },
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, payload: str | bytes) -> "FanoutShardJob":
        data = _load_job_json(payload)
        try:
            targets = _string_tuple(data.get("targetRecipientIds", []), "targetRecipientIds")
            shard_value = data["shard"]
            if isinstance(shard_value, bool) or not isinstance(shard_value, int):
                raise ValueError("shard must be an integer")
            job = cls(
                event_id=_required_string(data, "eventId"),
                shard=shard_value,
                target_recipient_ids=targets,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Invalid fan-out job") from exc
        if not _SHA256_HEX.fullmatch(job.event_id):
            raise ValueError("Invalid fan-out job identity")
        if any(not _is_uuid(value) for value in job.target_recipient_ids):
            raise ValueError("Fan-out targets must be opaque UUIDs")
        return job


@dataclass(frozen=True)
class EmailJob:
    event_id: str
    recipient_id: str
    delivery_id: str
    schema_version: int = EVENT_SCHEMA_VERSION

    @classmethod
    def create(cls, event_id: str, recipient_id: str) -> "EmailJob":
        delivery_id = hashlib.sha256(f"{event_id}\0{recipient_id}".encode("utf-8")).hexdigest()
        return cls(event_id=event_id, recipient_id=recipient_id, delivery_id=delivery_id)

    def to_json(self) -> str:
        return json.dumps(
            {
                "schemaVersion": self.schema_version,
                "eventId": self.event_id,
                "recipientId": self.recipient_id,
                "deliveryId": self.delivery_id,
            },
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, payload: str | bytes) -> "EmailJob":
        data = _load_job_json(payload)
        try:
            job = cls(
                event_id=_required_string(data, "eventId"),
                recipient_id=_required_string(data, "recipientId"),
                delivery_id=_required_string(data, "deliveryId"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Invalid email job") from exc
        if not _SHA256_HEX.fullmatch(job.event_id) or not _is_uuid(job.recipient_id):
            raise ValueError("Invalid email-job identity")
        if not _SHA256_HEX.fullmatch(job.delivery_id):
            raise ValueError("Invalid email-job deliveryId")
        expected = cls.create(job.event_id, job.recipient_id).delivery_id
        if job.delivery_id != expected:
            raise ValueError("Invalid email-job deliveryId")
        return job


def recipient_shard(recipient_id: str, shard_count: int) -> int:
    if shard_count <= 0:
        raise ValueError("shard_count must be positive")
    digest = hashlib.sha256(recipient_id.encode("utf-8")).digest()
    # Keep this byte-for-byte aligned with the web auth projection writer.
    return digest[-1] % shard_count


def _load_job_json(payload: str | bytes) -> dict[str, Any]:
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    try:
        data = json.loads(payload)
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid job JSON") from exc
    if (
        not isinstance(data, dict)
        or type(data.get("schemaVersion")) is not int
        or data.get("schemaVersion") != EVENT_SCHEMA_VERSION
    ):
        raise ValueError("Unsupported job schemaVersion")
    return data


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Expected a string")
    return value


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Expected a number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Expected a finite number")
    return result


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("Expected an integer")
    return value


def _required_string(data: dict[str, Any], key: str) -> str:
    value = data[key]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value.strip()


def _required_bool(data: dict[str, Any], key: str) -> bool:
    value = data[key]
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")
    return value


def _optional_bool(data: dict[str, Any], key: str, default: bool) -> bool:
    value = data.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")
    return value


def _string_tuple(value: Any, key: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{key} must be an array")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{key} must contain non-empty strings")
        normalized = item.strip()
        if normalized not in result:
            result.append(normalized)
    return tuple(result)


def _is_uuid(value: str) -> bool:
    try:
        return str(uuid.UUID(value)) == value.lower()
    except ValueError:
        return False


def _parse_created_at(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Invalid stock-event createdAt") from exc
    if parsed.tzinfo is None:
        raise ValueError("Stock-event createdAt must include a timezone")
    return parsed
