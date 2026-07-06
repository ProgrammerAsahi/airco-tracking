from __future__ import annotations

import unittest

from airco_tracker.azure_auth import table_endpoint_from_storage_url


class AzureAuthTests(unittest.TestCase):
    def test_table_endpoint_is_derived_from_blob_endpoint(self) -> None:
        self.assertEqual(
            table_endpoint_from_storage_url("https://acct.blob.core.windows.net"),
            "https://acct.table.core.windows.net",
        )

    def test_existing_table_endpoint_is_kept(self) -> None:
        self.assertEqual(
            table_endpoint_from_storage_url("https://acct.table.core.windows.net"),
            "https://acct.table.core.windows.net",
        )


if __name__ == "__main__":
    unittest.main()
