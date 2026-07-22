from __future__ import annotations

import math
import threading
import time
from typing import Any, Protocol

from .azure_auth import default_azure_credential, table_endpoint_from_storage_url
from .config import Config


_PARTITION_KEY = "email"
_ROW_KEY = "acs-global"
_LOCAL_LOCK = threading.Lock()
_LOCAL_LAST_SEND_MONOTONIC = 0.0


class EmailRateLimiter(Protocol):
    def wait(self, interval_seconds: float) -> None: ...


class LocalEmailRateLimiter:
    """Process-wide compatibility limiter for local/single-replica workers."""

    def wait(self, interval_seconds: float) -> None:
        if interval_seconds <= 0:
            return
        global _LOCAL_LAST_SEND_MONOTONIC
        with _LOCAL_LOCK:
            now = time.monotonic()
            remaining = interval_seconds - (now - _LOCAL_LAST_SEND_MONOTONIC)
            if remaining > 0:
                time.sleep(remaining)
            _LOCAL_LAST_SEND_MONOTONIC = time.monotonic()


class AzureTableEmailRateLimiter:
    """Reserve globally ordered ACS send slots with Azure Table ETag CAS.

    Every replica contends on one tiny entity. A successful conditional write
    reserves a unique timestamp at least ``interval_seconds`` after the prior
    reservation. The worker then waits until its slot before re-reading the
    recipient and sending. A crashed worker can waste a slot, but can never
    make another worker exceed the configured aggregate rate.
    """

    def __init__(
        self,
        config: Config | None = None,
        *,
        table: Any | None = None,
        wall_time=time.time,
        sleep=time.sleep,
    ) -> None:
        if table is None:
            if config is None:
                raise ValueError("Config is required when an Azure Table client is not supplied")
            try:
                from azure.data.tables import TableClient
            except ImportError as exc:
                raise RuntimeError(
                    "Install the 'azure' extra to use distributed email rate limiting"
                ) from exc
            table = TableClient(
                endpoint=table_endpoint_from_storage_url(
                    config.azure_storage_account_url
                ),
                table_name=config.email_rate_limit_table,
                credential=default_azure_credential(),
            )
        self._table = table
        self._wall_time = wall_time
        self._sleep = sleep

    def wait(self, interval_seconds: float) -> None:
        if interval_seconds <= 0:
            return
        try:
            from azure.core import MatchConditions
            from azure.core.exceptions import (
                ResourceExistsError,
                ResourceModifiedError,
                ResourceNotFoundError,
            )
            from azure.data.tables import UpdateMode
        except ImportError as exc:
            raise RuntimeError(
                "Install the 'azure' extra to use distributed email rate limiting"
            ) from exc

        interval_ms = max(1, math.ceil(interval_seconds * 1000))
        reserved_at_ms: int | None = None
        for _attempt in range(100):
            now_ms = math.floor(self._wall_time() * 1000)
            try:
                current = self._table.get_entity(_PARTITION_KEY, _ROW_KEY)
            except ResourceNotFoundError:
                try:
                    self._table.create_entity(
                        {
                            "PartitionKey": _PARTITION_KEY,
                            "RowKey": _ROW_KEY,
                            "nextAllowedAtMs": now_ms + interval_ms,
                        }
                    )
                except ResourceExistsError:
                    continue
                reserved_at_ms = now_ms
                break

            next_allowed_ms = _nonnegative_milliseconds(
                current.get("nextAllowedAtMs")
            )
            etag = _etag(current)
            if not etag:
                raise RuntimeError("Email rate-limit entity is missing its ETag")
            reserved_at_ms = max(now_ms, next_allowed_ms)
            try:
                self._table.update_entity(
                    {
                        "PartitionKey": _PARTITION_KEY,
                        "RowKey": _ROW_KEY,
                        "nextAllowedAtMs": reserved_at_ms + interval_ms,
                    },
                    mode=UpdateMode.MERGE,
                    etag=etag,
                    match_condition=MatchConditions.IfNotModified,
                )
            except (ResourceModifiedError, ResourceNotFoundError):
                reserved_at_ms = None
                continue
            break
        if reserved_at_ms is None:
            raise RuntimeError("Could not reserve a distributed email rate-limit slot")

        delay_seconds = max(
            0.0,
            (reserved_at_ms - math.floor(self._wall_time() * 1000)) / 1000,
        )
        if delay_seconds > 0:
            self._sleep(delay_seconds)


def build_email_rate_limiter(config: Config) -> EmailRateLimiter:
    backend = str(getattr(config, "email_rate_limit_backend", "local")).strip().lower()
    if backend == "local":
        return LocalEmailRateLimiter()
    if backend == "azure_table":
        return AzureTableEmailRateLimiter(config)
    raise ValueError("EMAIL_RATE_LIMIT_BACKEND must be local or azure_table")


def _nonnegative_milliseconds(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Email rate-limit entity contains an invalid timestamp") from exc
    if parsed < 0:
        raise RuntimeError("Email rate-limit entity contains an invalid timestamp")
    return parsed


def _etag(entity: Any) -> str | None:
    metadata = getattr(entity, "metadata", None)
    if isinstance(metadata, dict) and metadata.get("etag"):
        return str(metadata["etag"])
    if isinstance(entity, dict):
        for key in ("etag", "odata.etag", "@odata.etag"):
            if entity.get(key):
                return str(entity[key])
    return None
