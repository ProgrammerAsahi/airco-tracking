"""Azure Table Storage-backed i18n loader with local fallback.

Translations are stored in Azure Table Storage as entities:
  PartitionKey = scope ("email" or "web")
  RowKey = message key
  Columns: zh, nl, en

If Azure is not configured or the table is unreachable, the loader
falls back to the bundled ``i18n_local.json`` file so local development
and CI never break.
"""

from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)

_LOCAL_FILE = Path(__file__).parent / "i18n_local.json"


@lru_cache(maxsize=4)
def load_translations(scope: str) -> dict[str, dict[str, str]]:
    """Load all translations for *scope* from Azure Table Storage.

    Falls back to the local JSON file when Azure is not configured.
    The result is cached for the process lifetime.
    """
    account_url = os.getenv("AZURE_STORAGE_ACCOUNT_URL", "").strip()
    if account_url:
        try:
            return _load_from_table(account_url, scope)
        except Exception as exc:
            LOG.warning("Cannot load i18n from Azure Table for scope %s: %s", scope, exc)
    return _load_from_local(scope)


def _load_from_table(account_url: str, scope: str) -> dict[str, dict[str, str]]:
    from azure.data.tables import TableClient
    from azure.identity import DefaultAzureCredential

    credential = DefaultAzureCredential(
        managed_identityClientId=os.getenv("AZURE_CLIENT_ID", "").strip() or None
    )
    table = TableClient(endpoint=account_url, table_name="i18n", credential=credential)
    translations: dict[str, dict[str, str]] = {}
    for entity in table.query_entities(f"PartitionKey eq '{scope}'"):
        key = entity.get("RowKey", "")
        if not key:
            continue
        translations[key] = {
            "zh": str(entity.get("zh", "")),
            "nl": str(entity.get("nl", "")),
            "en": str(entity.get("en", "")),
        }
    if not translations:
        LOG.warning("Azure Table 'i18n' returned no entities for scope %s; using local", scope)
        return _load_from_local(scope)
    return translations


def _load_from_local(scope: str) -> dict[str, dict[str, str]]:
    data: dict[str, Any] = json.loads(_LOCAL_FILE.read_text(encoding="utf-8"))
    scope_data = data.get(scope, {})
    if not isinstance(scope_data, dict):
        return {}
    return {k: dict(v) for k, v in scope_data.items() if isinstance(v, dict)}
