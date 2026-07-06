from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from .azure_auth import default_azure_credential
from .config import Config
from .inventory import empty_inventory
from .state_store import _is_not_found


class InventoryStore(Protocol):
    def load(self) -> dict[str, Any]: ...

    def save(self, inventory: dict[str, Any]) -> None: ...


class LocalInventoryStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return empty_inventory()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Cannot read local inventory snapshot: {exc}") from exc
        _validate_inventory(data, "local inventory snapshot")
        return data

    def save(self, inventory: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            temporary.write_text(
                json.dumps(inventory, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(self.path)
        except OSError as exc:
            raise RuntimeError(f"Cannot write local inventory snapshot: {exc}") from exc


class AzureBlobInventoryStore:
    def __init__(self, account_url: str, container: str, blob: str) -> None:
        try:
            from azure.storage.blob import BlobServiceClient, ContentSettings
        except ImportError as exc:
            raise RuntimeError("Install the 'azure' extra to use Azure Blob inventory") from exc
        self._blob = BlobServiceClient(
            account_url=account_url,
            credential=default_azure_credential(),
        ).get_blob_client(container=container, blob=blob)
        self._content_settings = ContentSettings(content_type="application/json")

    def load(self) -> dict[str, Any]:
        try:
            raw = self._blob.download_blob().readall()
        except Exception as exc:
            if _is_not_found(exc):
                return empty_inventory()
            raise RuntimeError(f"Cannot read Azure Blob inventory: {exc}") from exc
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Invalid JSON in Azure Blob inventory: {exc}") from exc
        _validate_inventory(data, "Azure Blob inventory")
        return data

    def save(self, inventory: dict[str, Any]) -> None:
        payload = json.dumps(inventory, ensure_ascii=False, indent=2).encode("utf-8")
        try:
            self._blob.upload_blob(
                payload,
                overwrite=True,
                content_settings=self._content_settings,
            )
        except Exception as exc:
            raise RuntimeError(f"Cannot write Azure Blob inventory: {exc}") from exc


def build_inventory_store(config: Config) -> InventoryStore:
    config.validate_state()
    if config.state_backend == "azure_blob":
        return AzureBlobInventoryStore(
            config.azure_storage_account_url,
            config.azure_storage_container,
            config.azure_inventory_blob,
        )
    return LocalInventoryStore(config.inventory_path)


def _validate_inventory(data: Any, label: str) -> None:
    if not isinstance(data, dict) or not isinstance(data.get("sites"), dict):
        raise RuntimeError(f"Invalid {label}: sites must be an object")
