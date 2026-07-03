from __future__ import annotations

import io
import unittest
from contextlib import ExitStack, contextmanager, redirect_stdout
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from airco_tracker.cli import check
from airco_tracker.models import Product


ADAPTER_NAMES = (
    "CoolblueAdapter",
    "MediaMarktAdapter",
    "EpAdapter",
    "ElectroWorldAdapter",
    "WehkampAdapter",
    "LidlAdapter",
    "GammaAdapter",
    "KarweiAdapter",
    "PraxisAdapter",
    "AlternateAdapter",
    "TrotecAdapter",
    "KlarsteinAdapter",
    "FlinqAdapter",
    "ActionAdapter",
    "ExpertAdapter",
    "DelonghiAdapter",
    "ObelinkAdapter",
    "KampeerwereldAdapter",
    "CreateStoreAdapter",
    "CostwayAdapter",
    "EvolarshopAdapter",
    "AircoVoorInHuisAdapter",
    "SolagoAdapter",
)


class _SuccessAdapter:
    site = "Working shop"

    def __init__(self, _fetcher, *args, **kwargs) -> None:
        pass

    def fetch_products(self):
        return [Product(self.site, "Airco", "https://shop.test/1", False)]


class _FailingAdapter:
    site = "Blocked shop"

    def __init__(self, _fetcher, *args, **kwargs) -> None:
        pass

    def fetch_products(self):
        raise RuntimeError("403 Forbidden")


class _AvailableAdapter:
    """Returns a product that is in stock — eligible for an alert on first run."""

    site = "Stocked shop"

    def __init__(self, _fetcher, *args, **kwargs) -> None:
        pass

    def fetch_products(self):
        return [Product(self.site, "Airco", "https://shop.test/1", True, 399.0, "Morgen", 7000)]


class _StateStore:
    def load(self):
        return {"version": 1, "products": {}}


@contextmanager
def _patched_adapters(default, **overrides):
    """Patch all adapters without exceeding Python 3.9's nesting limit."""
    with ExitStack() as stack:
        for name in ADAPTER_NAMES:
            stack.enter_context(
                patch(f"airco_tracker.cli.{name}", overrides.get(name, default))
            )
        yield


class CliTests(unittest.TestCase):
    def test_all_retailers_succeed(self) -> None:
        config = SimpleNamespace(
            request_timeout_seconds=1,
            alert_on_first_seen=True,
            max_price_eur=None,
            min_btu=None,
        )
        with (
            _patched_adapters(_SuccessAdapter),
            patch("airco_tracker.cli.build_state_store", return_value=_StateStore()),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(check(config, dry_run=True, show_all=False), 0)

    def test_partial_retailer_failure_is_successful(self) -> None:
        config = SimpleNamespace(
            request_timeout_seconds=1,
            alert_on_first_seen=True,
            max_price_eur=None,
            min_btu=None,
        )
        with (
            _patched_adapters(
                _SuccessAdapter,
                MediaMarktAdapter=_FailingAdapter,
                KampeerwereldAdapter=_FailingAdapter,
            ),
            patch("airco_tracker.cli.build_state_store", return_value=_StateStore()),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(check(config, dry_run=True, show_all=False), 0)

    def test_all_retailer_failures_are_fatal(self) -> None:
        config = SimpleNamespace(
            request_timeout_seconds=1,
        )
        with (
            _patched_adapters(_FailingAdapter),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(check(config, dry_run=True, show_all=False), 2)

    def _config_with_alerts(self) -> SimpleNamespace:
        return SimpleNamespace(
            request_timeout_seconds=1,
            alert_on_first_seen=True,
            max_price_eur=None,
            min_btu=None,
            email_lang="zh",
            validate_email=lambda: None,
        )

    def test_dry_run_neither_emails_nor_saves_state(self) -> None:
        config = self._config_with_alerts()
        store = MagicMock()
        store.load.return_value = {"version": 1, "products": {}}
        with (
            _patched_adapters(_SuccessAdapter, CoolblueAdapter=_AvailableAdapter),
            patch("airco_tracker.cli.send_message") as mock_send,
            patch("airco_tracker.cli.build_state_store", return_value=store),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(check(config, dry_run=True, show_all=False), 0)
        mock_send.assert_not_called()
        store.save.assert_not_called()

    def test_non_dry_run_emails_and_saves_state(self) -> None:
        config = self._config_with_alerts()
        store = MagicMock()
        store.load.return_value = {"version": 1, "products": {}}
        with (
            _patched_adapters(_SuccessAdapter, CoolblueAdapter=_AvailableAdapter),
            patch("airco_tracker.cli.send_message") as mock_send,
            patch("airco_tracker.cli.build_message", return_value="msg"),
            patch("airco_tracker.cli.build_state_store", return_value=store),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(check(config, dry_run=False, show_all=False), 0)
        mock_send.assert_called_once()
        store.save.assert_called_once()


if __name__ == "__main__":
    unittest.main()
