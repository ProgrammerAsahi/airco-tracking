from __future__ import annotations

import io
import unittest
from contextlib import contextmanager, redirect_stdout
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from airco_tracker.adapters.registry import AdapterSpec
from airco_tracker.cli import _mask_email, check
from airco_tracker.models import Product
from airco_tracker.subscribers import AlertRecipient


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


def _adapter_class(base, site: str):
    """Build a one-off adapter subclass with a unique site name.

    The original tests patched 28 adapter classes by name; the registry now
    resolves classes dynamically, so tests inject a list of fake classes
    instead. Each needs a distinct ``site`` so that a single failing adapter
    does not collapse all retailers into one site in the inventory snapshot.
    """
    return type(base.__name__ + "_" + site, (base,), {"site": site})


def _adapter_classes(default, *, fail_sites: tuple[str, ...] = (), stock_site: str | None = None):
    """Build the fake adapter-class list the CLI would normally load.

    Mirrors the previous semantics: a full set of retailers where most use
    ``default``, the named ``fail_sites`` use _FailingAdapter, and when
    ``stock_site`` is set one retailer uses _AvailableAdapter under that site.
    """
    sites = [f"Shop {i}" for i in range(26)]
    if stock_site:
        sites.append(stock_site)
    while len(sites) < 28:
        sites.append(f"Extra shop {len(sites)}")
    classes = []
    for site in sites:
        if site in fail_sites:
            classes.append(_adapter_class(_FailingAdapter, site))
        elif site == stock_site:
            classes.append(_adapter_class(_AvailableAdapter, site))
        else:
            classes.append(_adapter_class(default, site))
    return classes


class _StateStore:
    def load(self):
        return {"version": 1, "products": {}}


class _InventoryStore:
    def load(self):
        return {"version": 1, "sites": {}}

    def save(self, _inventory):
        pass


@contextmanager
def _patched_adapters(default, *, fail_sites: tuple[str, ...] = (), stock_site: str | None = None):
    """Patch load_adapter_specs to return a fake adapter-spec list."""
    classes = _adapter_classes(default, fail_sites=fail_sites, stock_site=stock_site)
    specs = [AdapterSpec(country="nl", adapter_class=cls) for cls in classes]
    with patch("airco_tracker.cli.load_adapter_specs", return_value=specs):
        yield


class CliTests(unittest.TestCase):
    def test_mask_email_keeps_test_output_private(self) -> None:
        self.assertEqual(_mask_email("alice@example.com"), "a***@example.com")
        self.assertEqual(_mask_email(None), "configured recipient")

    def test_all_retailers_succeed(self) -> None:
        config = SimpleNamespace(
            request_timeout_seconds=1,
            alert_on_first_seen=True,
            max_price_eur=None,
            min_btu=None,
            countries=["nl"],
        )
        with (
            _patched_adapters(_SuccessAdapter),
            patch("airco_tracker.cli.build_state_store", return_value=_StateStore()),
            patch("airco_tracker.cli.build_inventory_store", return_value=_InventoryStore()),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(check(config, dry_run=True, show_all=False), 0)

    def test_partial_retailer_failure_is_successful(self) -> None:
        config = SimpleNamespace(
            request_timeout_seconds=1,
            alert_on_first_seen=True,
            max_price_eur=None,
            min_btu=None,
            countries=["nl"],
        )
        with (
            _patched_adapters(_SuccessAdapter, fail_sites=("Shop 1", "Shop 2")),
            patch("airco_tracker.cli.build_state_store", return_value=_StateStore()),
            patch("airco_tracker.cli.build_inventory_store", return_value=_InventoryStore()),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(check(config, dry_run=True, show_all=False), 0)

    def test_all_retailer_failures_are_fatal(self) -> None:
        config = SimpleNamespace(
            request_timeout_seconds=1,
            countries=["nl"],
        )
        with (
            _patched_adapters(_FailingAdapter),
            patch("airco_tracker.cli.build_inventory_store", return_value=_InventoryStore()),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(check(config, dry_run=True, show_all=False), 2)

    def _config_with_alerts(self) -> SimpleNamespace:
        return SimpleNamespace(
            request_timeout_seconds=1,
            alert_on_first_seen=True,
            max_price_eur=None,
            min_btu=None,
            countries=["nl"],
            email_lang="zh",
            email_to="recipient@example.com",
            azure_storage_account_url="",
            validate_email=lambda: None,
        )

    def test_dry_run_neither_emails_nor_saves_state(self) -> None:
        config = self._config_with_alerts()
        store = MagicMock()
        store.load.return_value = {"version": 1, "products": {}}
        inventory_store = MagicMock()
        inventory_store.load.return_value = {"version": 1, "sites": {}}
        with (
            _patched_adapters(_SuccessAdapter, stock_site="Stocked shop"),
            patch("airco_tracker.cli.send_message") as mock_send,
            patch("airco_tracker.cli.build_state_store", return_value=store),
            patch("airco_tracker.cli.build_inventory_store", return_value=inventory_store),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(check(config, dry_run=True, show_all=False), 0)
        mock_send.assert_not_called()
        store.save.assert_not_called()
        inventory_store.save.assert_not_called()

    def test_non_dry_run_emails_and_saves_state(self) -> None:
        config = self._config_with_alerts()
        store = MagicMock()
        store.load.return_value = {"version": 1, "products": {}}
        inventory_store = MagicMock()
        inventory_store.load.return_value = {"version": 1, "sites": {}}
        with (
            _patched_adapters(_SuccessAdapter, stock_site="Stocked shop"),
            patch("airco_tracker.cli.load_alert_recipients", return_value=[AlertRecipient("subscriber@example.com", "zh", "nl")]),
            patch("airco_tracker.cli.send_message") as mock_send,
            patch("airco_tracker.cli.build_message", return_value="msg"),
            patch("airco_tracker.cli.build_state_store", return_value=store),
            patch("airco_tracker.cli.build_inventory_store", return_value=inventory_store),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(check(config, dry_run=False, show_all=False), 0)
        mock_send.assert_called_once()
        store.save.assert_called_once()
        inventory_store.save.assert_called_once()

    def test_inventory_is_saved_before_email_and_survives_email_failure(self) -> None:
        config = self._config_with_alerts()
        events: list[str] = []
        state_store = MagicMock()
        state_store.load.return_value = {"version": 1, "products": {}}
        inventory_store = MagicMock()
        inventory_store.load.return_value = {"version": 1, "sites": {}}
        inventory_store.save.side_effect = lambda _snapshot: events.append("inventory")

        def fail_email(*_args, **_kwargs):
            events.append("email")
            raise RuntimeError("delivery failed")

        with (
            _patched_adapters(_SuccessAdapter, stock_site="Stocked shop"),
            patch("airco_tracker.cli.load_alert_recipients", return_value=[AlertRecipient("subscriber@example.com", "zh", "nl")]),
            patch("airco_tracker.cli.send_message", side_effect=fail_email),
            patch("airco_tracker.cli.build_message", return_value="msg"),
            patch("airco_tracker.cli.build_state_store", return_value=state_store),
            patch("airco_tracker.cli.build_inventory_store", return_value=inventory_store),
            redirect_stdout(io.StringIO()),
        ):
            with self.assertRaisesRegex(RuntimeError, "delivery failed"):
                check(config, dry_run=False, show_all=False)

        self.assertEqual(events, ["inventory", "email"])
        inventory_store.save.assert_called_once()
        state_store.save.assert_not_called()

    def test_all_failures_save_stale_inventory_in_production(self) -> None:
        config = self._config_with_alerts()
        inventory_store = MagicMock()
        inventory_store.load.return_value = {"version": 1, "sites": {}}
        with (
            _patched_adapters(_FailingAdapter),
            patch("airco_tracker.cli.build_inventory_store", return_value=inventory_store),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(check(config, dry_run=False, show_all=False), 2)

        inventory_store.save.assert_called_once()
        snapshot = inventory_store.save.call_args.args[0]
        self.assertEqual(snapshot["site_count"], 28)
        self.assertEqual(snapshot["stale_site_count"], 28)


if __name__ == "__main__":
    unittest.main()
