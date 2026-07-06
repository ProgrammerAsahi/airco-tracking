#!/usr/bin/env python3
"""Seed Azure Table Storage with i18n translations from i18n_local.json.

Usage:
    AZURE_STORAGE_ACCOUNT_URL=https://<account>.table.core.windows.net python scripts/seed-i18n.py

Requires the Azure CLI or Managed Identity with Storage Table Data Contributor
on the target Storage Account.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCAL_FILE = ROOT / "airco_tracker" / "i18n_local.json"
sys.path.insert(0, str(ROOT))

from airco_tracker.azure_auth import default_azure_credential, table_endpoint_from_storage_url  # noqa: E402


def main() -> int:
    account_url = table_endpoint_from_storage_url(os.getenv("AZURE_STORAGE_ACCOUNT_URL", "").strip())
    if not account_url:
        print("AZURE_STORAGE_ACCOUNT_URL is required.", file=sys.stderr)
        return 1

    from azure.data.tables import TableServiceClient

    data = json.loads(LOCAL_FILE.read_text(encoding="utf-8"))

    credential = default_azure_credential()
    client = TableServiceClient(endpoint=account_url, credential=credential)
    table = client.create_table_if_not_exists("i18n")
    print(f"Table 'i18n' ready.")

    count = 0
    for scope, messages in data.items():
        for key, translations in messages.items():
            entity = {
                "PartitionKey": scope,
                "RowKey": key,
                "zh": str(translations.get("zh", "")),
                "nl": str(translations.get("nl", "")),
                "en": str(translations.get("en", "")),
            }
            table.upsert_entity(entity)
            count += 1

    print(f"Seeded {count} translation entries across {len(data)} scopes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
