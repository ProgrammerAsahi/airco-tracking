from __future__ import annotations

import io
import unittest
from contextlib import contextmanager, redirect_stdout
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from airco_tracker.adapters.base import verified_empty
from airco_tracker.adapters.registry import AdapterSpec
from airco_tracker.cli import _mask_email, _parser, check
from airco_tracker.inventory import empty_inventory, updated_inventory
from airco_tracker.models import Product, product_state_key, site_id_for
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


class _ConstructorFailingAdapter:
    site = "Misconfigured shop"

    def __init__(self, _fetcher, *args, **kwargs) -> None:
        raise RuntimeError("credentials are not configured")


class _AvailableAdapter:
    """Returns a product that is in stock — eligible for an alert on first run."""

    site = "Stocked shop"

    def __init__(self, _fetcher, *args, **kwargs) -> None:
        pass

    def fetch_products(self):
        return [Product(self.site, "Airco", "https://shop.test/1", True, 399.0, "Morgen", 7000)]


class _SilentEmptyAdapter:
    site = "Seasonal shop"

    def __init__(self, _fetcher, *args, **kwargs) -> None:
        pass

    def fetch_products(self):
        return []


class _VerifiedEmptyAdapter(_SilentEmptyAdapter):
    def fetch_products(self):
        return verified_empty(
            self,
            source="authoritative_test_catalogue",
            signal="validated total=0",
        )


class _RecoveredAvailableAdapter(_SilentEmptyAdapter):
    def fetch_products(self):
        return [
            Product(
                self.site,
                "Airco",
                "https://shop.test/seasonal",
                True,
                399.0,
                "Morgen",
                7000,
            )
        ]


class _FutureDeliveryAdapter:
    """Returns an in-stock product whose delivery text is a future date."""

    site = "Future shop"

    def __init__(self, _fetcher, *args, **kwargs) -> None:
        pass

    def fetch_products(self):
        return [Product(self.site, "Airco", "https://shop.test/1", True, 399.0, "Binnen 2-3 weken leverbaar", 7000)]


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
    def test_pipeline_test_accepts_opaque_recipient_ids_without_an_email_lookup(self) -> None:
        recipient_id = "95bc3d32-8f2e-4cf0-a924-731efb4ebcf2"
        args = _parser().parse_args(["pipeline-test", "--recipient-id", recipient_id])

        self.assertEqual(args.recipient_id, [recipient_id])

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

    def test_constructor_failure_is_stale_and_other_retailers_continue(self) -> None:
        config = self._config_with_alerts()
        working = _adapter_class(_SuccessAdapter, "Working shop")
        broken = _adapter_class(_ConstructorFailingAdapter, "Misconfigured shop")
        specs = [
            AdapterSpec(country="nl", adapter_class=broken),
            AdapterSpec(country="nl", adapter_class=working),
        ]
        state_store = MagicMock()
        state_store.load.return_value = {"version": 1, "products": {}}
        inventory_store = MagicMock()
        inventory_store.load.return_value = {"version": 1, "sites": {}}

        with (
            patch("airco_tracker.cli.load_adapter_specs", return_value=specs),
            patch("airco_tracker.cli.build_state_store", return_value=state_store),
            patch("airco_tracker.cli.build_inventory_store", return_value=inventory_store),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(check(config, dry_run=False, show_all=False), 0)

        inventory_store.save.assert_called_once()
        snapshot = inventory_store.save.call_args.args[0]
        self.assertEqual(snapshot["site_count"], 2)
        self.assertEqual(snapshot["stale_site_count"], 1)
        self.assertTrue(snapshot["sites"]["nl:Misconfigured shop"]["stale"])
        self.assertFalse(snapshot["sites"]["nl:Working shop"]["stale"])
        state_store.save.assert_called_once()

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

    def test_all_constructor_failures_are_fatal_and_saved_as_stale(self) -> None:
        config = self._config_with_alerts()
        inventory_store = MagicMock()
        inventory_store.load.return_value = {"version": 1, "sites": {}}

        with (
            _patched_adapters(_ConstructorFailingAdapter),
            patch("airco_tracker.cli.build_inventory_store", return_value=inventory_store),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(check(config, dry_run=False, show_all=False), 2)

        inventory_store.save.assert_called_once()
        snapshot = inventory_store.save.call_args.args[0]
        self.assertEqual(snapshot["site_count"], 28)
        self.assertEqual(snapshot["stale_site_count"], 28)

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

    def test_presale_delivery_text_does_not_trigger_immediate_stock_alert(self) -> None:
        # The alert path must apply the same delivery-text presale detection as
        # the inventory snapshot: a multi-week lead time is not immediate stock.
        config = self._config_with_alerts()
        future = _adapter_class(_FutureDeliveryAdapter, "Future shop")
        specs = [AdapterSpec(country="nl", adapter_class=future)]
        store = MagicMock()
        store.load.return_value = {"version": 1, "products": {}}
        inventory_store = MagicMock()
        inventory_store.load.return_value = {"version": 1, "sites": {}}
        with (
            patch("airco_tracker.cli.load_adapter_specs", return_value=specs),
            patch("airco_tracker.cli.load_alert_recipients", return_value=[AlertRecipient("subscriber@example.com", "zh", "nl")]),
            patch("airco_tracker.cli.send_message") as mock_send,
            patch("airco_tracker.cli.build_state_store", return_value=store),
            patch("airco_tracker.cli.build_inventory_store", return_value=inventory_store),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(check(config, dry_run=False, show_all=False), 0)
        mock_send.assert_not_called()
        snapshot = inventory_store.save.call_args.args[0]
        site = snapshot["sites"]["nl:Future shop"]
        self.assertEqual(site["presale_product_count"], 1)
        self.assertEqual(site["immediate_product_count"], 0)
        saved_state = store.save.call_args.args[0]
        record = saved_state["products"]["nl:https://shop.test/1"]
        self.assertTrue(record["available"])
        self.assertTrue(record["presale"])

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

    def test_unverified_empty_result_is_stale_and_recovery_does_not_realert(self) -> None:
        config = self._config_with_alerts()
        site_id = site_id_for("nl", "Seasonal shop")
        previous = Product(
            "Seasonal shop",
            "Airco",
            "https://shop.test/seasonal",
            True,
            399.0,
            "Morgen",
            7000,
            country="nl",
        )
        old_state = {
            "version": 1,
            "products": {product_state_key("nl", previous.url): previous.to_dict()},
        }
        old_inventory = updated_inventory(
            empty_inventory(),
            [previous],
            all_sites={
                site_id: {
                    "country": "nl",
                    "site": "Seasonal shop",
                    "site_id": site_id,
                    "delivery_coverage": ["nl"],
                }
            },
            checked_sites={site_id},
        )
        state_store = MagicMock()
        state_store.load.return_value = old_state
        inventory_store = MagicMock()
        inventory_store.load.return_value = old_inventory

        with (
            patch(
                "airco_tracker.cli.load_adapter_specs",
                return_value=[AdapterSpec(country="nl", adapter_class=_SilentEmptyAdapter)],
            ),
            patch("airco_tracker.cli.build_state_store", return_value=state_store),
            patch("airco_tracker.cli.build_inventory_store", return_value=inventory_store),
            patch("airco_tracker.cli.send_message") as mock_send,
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(check(config, dry_run=False, show_all=False), 2)

        state_store.save.assert_not_called()
        mock_send.assert_not_called()
        stale_snapshot = inventory_store.save.call_args.args[0]
        self.assertTrue(stale_snapshot["sites"][site_id]["stale"])
        self.assertEqual(stale_snapshot["sites"][site_id]["available_product_count"], 1)

        inventory_store.reset_mock()
        with (
            patch(
                "airco_tracker.cli.load_adapter_specs",
                return_value=[
                    AdapterSpec(country="nl", adapter_class=_RecoveredAvailableAdapter)
                ],
            ),
            patch("airco_tracker.cli.build_state_store", return_value=state_store),
            patch("airco_tracker.cli.build_inventory_store", return_value=inventory_store),
            patch("airco_tracker.cli.send_message") as recovery_send,
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(check(config, dry_run=False, show_all=False), 0)

        recovery_send.assert_not_called()
        state_store.save.assert_called_once()

    def test_verified_empty_result_clears_inventory_and_advances_state(self) -> None:
        config = self._config_with_alerts()
        site_id = site_id_for("nl", "Seasonal shop")
        previous = Product(
            "Seasonal shop",
            "Airco",
            "https://shop.test/seasonal",
            True,
            399.0,
            "Morgen",
            7000,
            country="nl",
        )
        state_store = MagicMock()
        state_store.load.return_value = {
            "version": 1,
            "products": {product_state_key("nl", previous.url): previous.to_dict()},
        }
        inventory_store = MagicMock()
        inventory_store.load.return_value = updated_inventory(
            empty_inventory(),
            [previous],
            all_sites={
                site_id: {
                    "country": "nl",
                    "site": "Seasonal shop",
                    "site_id": site_id,
                    "delivery_coverage": ["nl"],
                }
            },
            checked_sites={site_id},
        )

        with (
            patch(
                "airco_tracker.cli.load_adapter_specs",
                return_value=[AdapterSpec(country="nl", adapter_class=_VerifiedEmptyAdapter)],
            ),
            patch("airco_tracker.cli.build_state_store", return_value=state_store),
            patch("airco_tracker.cli.build_inventory_store", return_value=inventory_store),
            patch("airco_tracker.cli.send_message") as mock_send,
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(check(config, dry_run=False, show_all=False), 0)

        mock_send.assert_not_called()
        snapshot = inventory_store.save.call_args.args[0]
        self.assertFalse(snapshot["sites"][site_id]["stale"])
        self.assertEqual(snapshot["sites"][site_id]["available_product_count"], 0)
        saved_state = state_store.save.call_args.args[0]
        self.assertFalse(saved_state["products"][product_state_key("nl", previous.url)]["available"])


if __name__ == "__main__":
    unittest.main()
