from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import replace

from .adapters.registry import load_adapter_specs
from .config import Config
from .fetch import Fetcher
from .inventory import updated_inventory
from .inventory_store import build_inventory_store
from .mailer import build_message, send_message
from .models import Product
from .state import select_alerts, updated_state
from .state_store import build_state_store


LOG = logging.getLogger("airco_tracker")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Track portable airco stock across configured countries")
    subparsers = parser.add_subparsers(dest="command", required=True)
    check = subparsers.add_parser("check", help="Check all retailers once")
    check.add_argument("--dry-run", action="store_true", help="Do not email or update state")
    check.add_argument("--show-all", action="store_true", help="Print out-of-stock products too")
    subparsers.add_parser("send-test", help="Send a test email")
    subparsers.add_parser("doctor", help="Print safe runtime configuration and test state access")
    return parser


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    for logger_name in ("azure", "azure.core.pipeline.policies.http_logging_policy"):
        logging.getLogger(logger_name).setLevel(logging.WARNING)


def _mask_email(address: str | None) -> str:
    if not address or "@" not in address:
        return "configured recipient"
    local, domain = address.rsplit("@", 1)
    if not local or not domain:
        return "configured recipient"
    visible = local[0]
    return f"{visible}***@{domain}"


def check(config: Config, *, dry_run: bool, show_all: bool) -> int:
    fetcher = Fetcher(config.request_timeout_seconds)
    adapter_specs = load_adapter_specs(config.countries)
    adapters = [(spec, spec.adapter_class(fetcher)) for spec in adapter_specs]
    products: list[Product] = []
    failures: list[str] = []
    site_identity = {
        spec.site_id: {
            "country": spec.country,
            "site": spec.site,
            "site_id": spec.site_id,
        }
        for spec, _adapter in adapters
    }
    successful_sites: set[str] = set()
    for spec, adapter in adapters:
        country = spec.country
        adapter_site_id = spec.site_id
        try:
            found = adapter.fetch_products()
            found = [
                replace(product, site=spec.site, country=country)
                for product in found
            ]
            products.extend(found)
            successful_sites.add(adapter_site_id)
            available = sum(product.available for product in found)
            LOG.info("%s/%s: %d products, %d available", country, spec.site, len(found), available)
        except Exception as exc:  # Keep other retailers running.
            failures.append(f"{country}/{spec.site}: {exc}")
            LOG.exception("Retailer check failed: %s/%s", country, spec.site)

    if failures:
        LOG.warning(
            "%d retailer check(s) failed; continuing with successful retailers: %s",
            len(failures),
            "; ".join(failures),
        )

    inventory_store = build_inventory_store(config)
    inventory = updated_inventory(
        inventory_store.load(),
        products,
        all_sites=site_identity,
        checked_sites=successful_sites,
    )
    if not dry_run:
        inventory_store.save(inventory)
        LOG.info(
            "Saved inventory snapshot: %d available products across %d sites (%d stale)",
            inventory["available_product_count"],
            inventory["site_count"],
            inventory["stale_site_count"],
        )

    if not successful_sites:
        LOG.error("All retailer checks failed")
        return 2

    # Keep production configuration checks strict, but only after the inventory
    # snapshot is durable so a notification outage cannot make stock look stale.
    if not dry_run:
        config.validate_email()

    state_store = build_state_store(config)
    old_state = state_store.load()
    alerts = select_alerts(
        products,
        old_state,
        alert_on_first_seen=config.alert_on_first_seen,
        max_price_eur=config.max_price_eur,
        min_btu=config.min_btu,
    )
    visible = products if show_all else [product for product in products if product.available]
    if dry_run or show_all:
        print(json.dumps([product.to_dict() for product in visible], ensure_ascii=False, indent=2))
    else:
        LOG.info(
            "Product JSON omitted from production stdout; use --dry-run or --show-all to print %d visible products",
            len(visible),
        )

    if dry_run:
        LOG.info(
            "Dry run: %d available products in snapshot; %d products would trigger an alert",
            inventory["available_product_count"],
            len(alerts),
        )
        return 0

    # Inventory is already current. Keep alert state uncommitted until email
    # succeeds so a failed delivery is retried on the next run.
    if alerts:
        send_message(config, build_message(config, alerts))
        LOG.info("Sent stock alert for %d products", len(alerts))
    else:
        LOG.info("No new stock; no email sent")
    state_store.save(updated_state(old_state, products, checked_sites=successful_sites))
    return 0


def doctor(config: Config) -> int:
    store = build_state_store(config)
    state = store.load()
    inventory = build_inventory_store(config).load()
    config.validate_email()
    summary = {
        "app_env": config.app_env,
        "email_backend": config.email_backend,
        "email_to_configured": bool(config.email_to),
        "email_from": config.email_from,
        "email_lang": config.email_lang,
        "state_backend": config.state_backend,
        "known_products": len(state.get("products", {})),
        "inventory_available_products": inventory.get("available_product_count", 0),
        "inventory_sites": inventory.get("site_count", 0),
        "inventory_stale_sites": inventory.get("stale_site_count", 0),
        "azure_storage_account_url": config.azure_storage_account_url or None,
        "acs_endpoint": config.acs_endpoint or None,
        "key_vault_enabled": bool(config.azure_key_vault_url),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    _configure_logging()
    args = _parser().parse_args(argv)
    try:
        config = Config.from_env()
        if args.command == "send-test":
            send_message(config, build_message(config, [], test=True))
            print(f"Test email sent to {_mask_email(config.email_to)}")
            return 0
        if args.command == "doctor":
            return doctor(config)
        return check(config, dry_run=args.dry_run, show_all=args.show_all)
    except (ValueError, RuntimeError) as exc:
        LOG.error("%s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
