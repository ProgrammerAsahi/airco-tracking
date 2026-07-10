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

from .azure_auth import default_azure_credential, table_endpoint_from_storage_url

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
            # Bundled strings are the schema/defaults; Table Storage is an
            # optional runtime override. This makes newly deployed keys safe
            # before the separate seed job has populated every language.
            merged = _load_from_local(scope)
            for key, translations in _load_from_table(account_url, scope).items():
                merged.setdefault(key, {}).update(
                    {lang: value for lang, value in translations.items() if value}
                )
            return merged
        except Exception as exc:
            LOG.warning("Cannot load i18n from Azure Table for scope %s: %s", scope, exc)
    return _load_from_local(scope)


def _load_from_table(account_url: str, scope: str) -> dict[str, dict[str, str]]:
    from azure.data.tables import TableClient

    table = TableClient(
        endpoint=table_endpoint_from_storage_url(account_url),
        table_name="i18n",
        credential=default_azure_credential(),
    )
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
        return {}
    return translations


def _load_from_local(scope: str) -> dict[str, dict[str, str]]:
    data: dict[str, Any] = json.loads(_LOCAL_FILE.read_text(encoding="utf-8"))
    scope_data = data.get(scope, {})
    if not isinstance(scope_data, dict):
        return {}
    return {k: dict(v) for k, v in scope_data.items() if isinstance(v, dict)}
