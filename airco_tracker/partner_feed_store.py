from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Protocol

from .azure_auth import default_azure_credential
from .state_store import _is_not_found


_SAFE_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_MAX_CACHE_BYTES = 64 * 1024 * 1024
_MAX_CACHE_ROWS = 10_000


class PartnerFeedCache(Protocol):
    def load(self, namespace: str, feed_id: str) -> dict[str, Any] | None: ...

    def save(self, namespace: str, feed_id: str, payload: dict[str, Any]) -> None: ...


class LocalPartnerFeedCache:
    def __init__(self, root: Path) -> None:
        self.root = root

    def load(self, namespace: str, feed_id: str) -> dict[str, Any] | None:
        path = self._path(namespace, feed_id)
        if not path.exists():
            return None
        try:
            with path.open("rb") as source:
                raw = source.read(_MAX_CACHE_BYTES + 1)
            if len(raw) > _MAX_CACHE_BYTES:
                raise RuntimeError("Local partner-feed cache exceeds the safety limit")
            data = json.loads(raw.decode("utf-8"))
        except RuntimeError:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("Cannot read local partner-feed cache") from exc
        return _validate_cache(data)

    def save(self, namespace: str, feed_id: str, payload: dict[str, Any]) -> None:
        path = self._path(namespace, feed_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        try:
            body = json.dumps(
                _validate_cache(payload), ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            if len(body) > _MAX_CACHE_BYTES:
                raise RuntimeError("Local partner-feed cache exceeds the safety limit")
            temporary.write_bytes(body)
            temporary.replace(path)
        except RuntimeError:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        except OSError as exc:
            raise RuntimeError("Cannot write local partner-feed cache") from exc

    def _path(self, namespace: str, feed_id: str) -> Path:
        return self.root / _safe_key(namespace) / f"{_safe_key(feed_id)}.json"


class AzureBlobPartnerFeedCache:
    def __init__(self, account_url: str, container: str, prefix: str = "partner-feeds") -> None:
        try:
            from azure.storage.blob import BlobServiceClient, ContentSettings
        except ImportError as exc:
            raise RuntimeError("Install the 'azure' extra to use Azure partner-feed cache") from exc
        self._container = BlobServiceClient(
            account_url=account_url,
            credential=default_azure_credential(),
        ).get_container_client(container)
        self._content_settings = ContentSettings(content_type="application/json")
        self._prefix = prefix.strip("/")

    def load(self, namespace: str, feed_id: str) -> dict[str, Any] | None:
        blob = self._container.get_blob_client(self._blob_name(namespace, feed_id))
        try:
            raw = blob.download_blob(offset=0, length=_MAX_CACHE_BYTES + 1).readall()
        except Exception as exc:
            if _is_not_found(exc):
                return None
            raise RuntimeError("Cannot read Azure partner-feed cache") from exc
        if len(raw) > _MAX_CACHE_BYTES:
            raise RuntimeError("Azure partner-feed cache exceeds the safety limit")
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("Invalid Azure partner-feed cache") from exc
        return _validate_cache(data)

    def save(self, namespace: str, feed_id: str, payload: dict[str, Any]) -> None:
        blob = self._container.get_blob_client(self._blob_name(namespace, feed_id))
        body = json.dumps(
            _validate_cache(payload), ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        if len(body) > _MAX_CACHE_BYTES:
            raise RuntimeError("Azure partner-feed cache exceeds the safety limit")
        try:
            blob.upload_blob(body, overwrite=True, content_settings=self._content_settings)
        except Exception as exc:
            raise RuntimeError("Cannot write Azure partner-feed cache") from exc

    def _blob_name(self, namespace: str, feed_id: str) -> str:
        cache_path = f"{_safe_key(namespace)}/{_safe_key(feed_id)}.json"
        return f"{self._prefix}/{cache_path}" if self._prefix else cache_path


def build_partner_feed_cache() -> PartnerFeedCache:
    backend = os.getenv("STATE_BACKEND", "local").strip().lower()
    account_url = os.getenv("AZURE_STORAGE_ACCOUNT_URL", "").strip()
    container = os.getenv("AZURE_STORAGE_CONTAINER", "airco-tracker").strip()
    if backend == "azure_blob":
        if not account_url:
            raise RuntimeError("AZURE_STORAGE_ACCOUNT_URL is required for partner-feed cache")
        return AzureBlobPartnerFeedCache(account_url, container)
    root = Path(os.getenv("AIRCO_TRACKER_HOME", os.getcwd())).expanduser().resolve()
    return LocalPartnerFeedCache(root / "partner-feeds")


def _safe_key(value: str) -> str:
    key = value.strip().lower()
    if _SAFE_KEY_RE.fullmatch(key) is None:
        raise ValueError("Invalid internal partner-feed cache key")
    return key


def _validate_cache(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("version") != 2:
        raise RuntimeError("Invalid partner-feed cache document")
    if (
        not isinstance(value.get("last_imported"), str)
        or not isinstance(value.get("rows"), list)
        or not isinstance(value.get("source_row_count"), int)
        or isinstance(value.get("source_row_count"), bool)
        or value["source_row_count"] < 0
    ):
        raise RuntimeError("Invalid partner-feed cache document")
    if len(value["rows"]) > _MAX_CACHE_ROWS:
        raise RuntimeError("Partner-feed cache contains too many rows")
    if any(not isinstance(row, dict) for row in value["rows"]):
        raise RuntimeError("Invalid partner-feed cache rows")
    return value
