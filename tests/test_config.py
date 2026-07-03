from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from airco_tracker.config import Config


class ConfigTests(unittest.TestCase):
    def test_shared_filter_and_language_defaults(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("airco_tracker.config.load_dotenv"),
            patch("airco_tracker.config._load_key_vault_secrets"),
        ):
            config = Config.from_env()

        self.assertEqual(config.max_price_eur, 1500.0)
        self.assertEqual(config.min_btu, 7000)
        self.assertEqual(config.email_lang, "zh")
        self.assertEqual(config.inventory_path.name, "inventory.json")
        self.assertEqual(config.azure_inventory_blob, "inventory.json")


if __name__ == "__main__":
    unittest.main()
