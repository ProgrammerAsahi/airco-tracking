from __future__ import annotations

import unittest
from unittest.mock import patch

from airco_tracker.adapters import registry


class _AdapterA:
    site = "Shop"


class _AdapterWithDeliveryCoverage:
    site = "Crossborder"
    delivery_coverage = {"EU", "CH"}


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
        self.assertEqual(specs[0].delivery_coverage, frozenset({"be"}))

    def test_load_adapter_specs_reads_delivery_coverage(self) -> None:
        with patch.dict(registry._ADAPTERS_BY_COUNTRY, {"de": [_AdapterWithDeliveryCoverage]}, clear=True):
            specs = registry.load_adapter_specs(["de"])

        self.assertEqual(specs[0].delivery_coverage, frozenset({"eu", "ch"}))

    def test_registered_sites_have_explicit_delivery_coverage(self) -> None:
        specs = registry.load_adapter_specs(["nl", "fr"])

        self.assertEqual(
            {spec.site_id for spec in specs},
            set(registry._DELIVERY_COVERAGE_BY_SITE_ID),
        )
        self.assertTrue(all(spec.delivery_coverage for spec in specs))

    def test_invalid_delivery_coverage_tokens_fail_fast(self) -> None:
        class _AdapterWithInvalidCoverage:
            site = "Invalid"
            delivery_coverage = {"europe"}

        with patch.dict(registry._ADAPTERS_BY_COUNTRY, {"de": [_AdapterWithInvalidCoverage]}, clear=True):
            with self.assertRaisesRegex(ValueError, "Invalid delivery coverage token"):
                registry.load_adapter_specs(["de"])

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
