from __future__ import annotations

import unittest
from unittest.mock import patch

from airco_tracker.adapters import registry


class _AdapterA:
    site = "Shop"


class _AdapterB:
    site = "Shop"


class _AdapterWithoutSite:
    site = ""


class RegistryTests(unittest.TestCase):
    def test_load_adapter_specs_binds_country_explicitly(self) -> None:
        with patch.dict(registry._ADAPTERS_BY_COUNTRY, {"be": [_AdapterA]}, clear=True):
            specs = registry.load_adapter_specs(["BE"])

        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0].country, "be")
        self.assertEqual(specs[0].site, "Shop")
        self.assertEqual(specs[0].site_id, "be:Shop")

    def test_duplicate_site_ids_fail_fast(self) -> None:
        with patch.dict(registry._ADAPTERS_BY_COUNTRY, {"nl": [_AdapterA, _AdapterB]}, clear=True):
            with self.assertRaisesRegex(ValueError, "Duplicate adapter site_id"):
                registry.load_adapter_specs(["nl"])

    def test_empty_site_names_fail_fast(self) -> None:
        with patch.dict(registry._ADAPTERS_BY_COUNTRY, {"nl": [_AdapterWithoutSite]}, clear=True):
            with self.assertRaisesRegex(ValueError, "missing a non-empty site name"):
                registry.load_adapter_specs(["nl"])


if __name__ == "__main__":
    unittest.main()
