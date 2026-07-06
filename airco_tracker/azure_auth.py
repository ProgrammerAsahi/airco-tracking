from __future__ import annotations

import os


def default_azure_credential():
    """Build DefaultAzureCredential with the configured user-assigned MI.

    Azure Container Apps can expose more than one identity over time. Passing
    AZURE_CLIENT_ID explicitly keeps Blob, Table, Key Vault, and ACS clients on
    the intended runtime identity instead of relying on SDK inference.
    """
    from azure.identity import DefaultAzureCredential

    return DefaultAzureCredential(
        managed_identity_client_id=os.getenv("AZURE_CLIENT_ID", "").strip() or None
    )


def table_endpoint_from_storage_url(account_url: str) -> str:
    """Return the Table endpoint for a Storage account URL.

    The app already uses AZURE_STORAGE_ACCOUNT_URL for Blob access. In Azure the
    Table endpoint is the same account host with the ``.blob.`` service segment
    replaced by ``.table.``; if callers pass a table endpoint directly, keep it.
    """
    url = account_url.strip()
    if ".table." in url:
        return url
    return url.replace(".blob.", ".table.", 1)
