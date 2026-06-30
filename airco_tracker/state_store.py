from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from .config import Config
from .state import load_state, save_state


EMPTY_STATE = {"version": 1, "products": {}}


class StateStore(Protocol):
    def load(self) -> dict[str, Any]: ...

    def save(self, state: dict[str, Any]) -> None: ...


class LocalStateStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict[str, Any]:
        return load_state(self.path)

    def save(self, state: dict[str, Any]) -> None:
        save_state(self.path, state)


class AzureBlobStateStore:
    def __init__(self, account_url: str, container: str, blob: str) -> None:
        try:
            from azure.identity import DefaultAzureCredential
            from azure.storage.blob import BlobServiceClient, ContentSettings
        except ImportError as exc:
            raise RuntimeError("Install the 'azure' extra to use Azure Blob state") from exc
        self._blob = BlobServiceClient(
            account_url=account_url,
            credential=DefaultAzureCredential(),
        ).get_blob_client(container=container, blob=blob)
        self._content_settings = ContentSettings(content_type="application/json")

    def load(self) -> dict[str, Any]:
        try:
            raw = self._blob.download_blob().readall()
        except Exception as exc:
            if _is_not_found(exc):
                return dict(EMPTY_STATE)
            raise RuntimeError(f"Cannot read Azure Blob state: {exc}") from exc
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Invalid JSON in Azure Blob state: {exc}") from exc
        if not isinstance(data.get("products"), dict):
            raise RuntimeError("Invalid Azure Blob state: products must be an object")
        return data

    def save(self, state: dict[str, Any]) -> None:
        payload = json.dumps(state, ensure_ascii=False, indent=2).encode("utf-8")
        try:
            self._blob.upload_blob(
                payload,
                overwrite=True,
                content_settings=self._content_settings,
            )
        except Exception as exc:
            raise RuntimeError(f"Cannot write Azure Blob state: {exc}") from exc


def build_state_store(config: Config) -> StateStore:
    config.validate_state()
    if config.state_backend == "azure_blob":
        return AzureBlobStateStore(
            config.azure_storage_account_url,
            config.azure_storage_container,
            config.azure_storage_blob,
        )
    return LocalStateStore(config.state_path)


def _is_not_found(exc: Exception) -> bool:
    return exc.__class__.__name__ == "ResourceNotFoundError" or getattr(exc, "status_code", None) == 404
