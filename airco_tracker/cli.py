from __future__ import annotations

import argparse
import json
import logging
import sys
from copy import copy
from dataclasses import is_dataclass, replace

from .adapters.registry import load_adapter_specs
from .alert_events import EmailJob, StockAvailableEvent
from .alert_pipeline import (
    OutboxPublisher,
    purge_delivery_report_dead_letters,
    run_delivery_report_worker,
    run_email_worker,
    run_fanout_coordinator,
    run_fanout_worker,
)
from .config import Config
from .deliveries import DeliveryLedger
from .delivery_coverage import coverage_reaches_country
from .fetch import Fetcher
from .inventory import updated_inventory
from .inventory_store import build_inventory_store
from .mailer import build_message, send_message
from .models import Product, product_state_key
from .outbox import build_outbox
from .recipient_projection import RecipientProjection
from .retention import cleanup_alert_data
from .scan_lock import scanner_lease
from .state import select_alerts, updated_state
from .state_store import build_state_store
from .subscribers import AlertRecipient, load_alert_recipients


LOG = logging.getLogger("airco_tracker")

def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Track portable airco stock across configured countries")
    subparsers = parser.add_subparsers(dest="command", required=True)
    check = subparsers.add_parser("check", help="Check all retailers once")
    check.add_argument("--dry-run", action="store_true", help="Do not email or update state")
    check.add_argument("--show-all", action="store_true", help="Print out-of-stock products too")
    subparsers.add_parser("send-test", help="Send a test email")
    publisher = subparsers.add_parser("publish-outbox", help="Publish pending stock events")
    publisher.add_argument("--limit", type=int, default=100)
    for command, help_text in (
        ("fanout-coordinator", "Split one stock event into recipient shards"),
        ("fanout-worker", "Expand one recipient shard into email jobs"),
        ("email-worker", "Send queued email jobs"),
        ("delivery-report-worker", "Consume ACS final delivery reports"),
    ):
        worker = subparsers.add_parser(command, help=help_text)
        worker.add_argument("--once", action="store_true", help="Drain one receive batch and exit")
    subparsers.add_parser(
        "reconcile-alert-recipients",
        help="Repair the sharded alert recipient projection from users",
    )
    cleanup = subparsers.add_parser(
        "cleanup-alert-data", help="Apply outbox and delivery retention policy"
    )
    cleanup.add_argument("--limit", type=int, default=5000)
    delivery_dlq_cleanup = subparsers.add_parser(
        "purge-delivery-report-dlq",
        help="Delete raw ACS delivery reports retained by the Service Bus DLQ",
    )
    delivery_dlq_cleanup.add_argument("--limit", type=int, default=5000)
    pipeline_test = subparsers.add_parser(
        "pipeline-test", help="Send an explicitly targeted synthetic pipeline test"
    )
    pipeline_test.add_argument(
        "--recipient-id",
        action="append",
        required=True,
        help="Opaque registered UUID for Managed Identity operator runs; may be repeated",
    )
    pipeline_status = subparsers.add_parser(
        "pipeline-status", help="Show safe delivery status for a targeted pipeline test"
    )
    pipeline_status.add_argument("event_id")
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
    products: list[Product] = []
    failures: list[str] = []
    site_identity = {
        spec.site_id: {
            "country": spec.country,
            "site": spec.site,
            "site_id": spec.site_id,
            "delivery_coverage": sorted(spec.delivery_coverage),
        }
        for spec in adapter_specs
    }
    successful_sites: set[str] = set()
    for spec in adapter_specs:
        country = spec.country
        adapter_site_id = spec.site_id
        try:
            adapter = spec.adapter_class(fetcher)
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

    new_state = updated_state(old_state, products, checked_sites=successful_sites)
    dispatch_backend = getattr(config, "alert_dispatch_backend", "direct")
    if dispatch_backend == "service_bus":
        config.validate_alert_pipeline()
        if alerts:
            outbox = build_outbox(config)
            coverage_by_site = {
                spec.site_id: spec.delivery_coverage for spec in adapter_specs
            }
            created = 0
            for product in alerts:
                state_record = new_state["products"].get(
                    product_state_key(product.country, product.url), {}
                )
                generation = int(state_record.get("availability_generation") or 0)
                coverage = coverage_by_site.get(product.site_id) or frozenset({product.country})
                event = StockAvailableEvent.for_product(
                    product,
                    availability_generation=generation,
                    delivery_coverage=coverage,
                )
                created += int(outbox.create_if_absent(event))
            LOG.info(
                "Persisted %d new outbox event(s) for %d stock transition(s)",
                created,
                len(alerts),
            )
        else:
            LOG.info("No new stock; no outbox event created")
        # The state advances only after every event is durably in the outbox.
        # Publisher/fan-out/email failures can no longer delay the scanner.
        state_store.save(new_state)
        return 0

    # Local/direct compatibility path. Azure production always uses the
    # Service Bus outbox path above and fails closed if users cannot be read.
    if alerts:
        recipients = load_alert_recipients(config)
        LOG.info("Loaded %d stock-alert recipient(s)", len(recipients))
        sent_count = 0
        coverage_by_site = {
            spec.site_id: spec.delivery_coverage
            for spec in adapter_specs
        }
        for recipient in recipients:
            recipient_alerts = [
                product
                for product in alerts
                if _product_matches_recipient(product, recipient, coverage_by_site)
            ]
            if not recipient_alerts:
                LOG.info(
                    "No matching alerts for %s (delivery_country=%s)",
                    _mask_email(recipient.email),
                    recipient.delivery_country or "all",
                )
                continue
            recipient_config = _config_for_recipient(config, recipient)
            send_message(
                recipient_config,
                build_message(
                    recipient_config,
                    recipient_alerts,
                    delivery_country=recipient.delivery_country,
                ),
            )
            sent_count += 1
            LOG.info(
                "Sent stock alert for %d product(s) to %s (delivery_country=%s)",
                len(recipient_alerts),
                _mask_email(recipient.email),
                recipient.delivery_country or "all",
            )
        LOG.info(
            "Sent stock alert for %d products to %d recipient(s)",
            len(alerts),
            sent_count,
        )
    else:
        LOG.info("No new stock; no email sent")
    state_store.save(new_state)
    return 0


def _product_matches_recipient(
    product: Product,
    recipient: AlertRecipient,
    coverage_by_site: dict[str, frozenset[str]],
) -> bool:
    country = (recipient.delivery_country or "").strip().lower()
    if not country:
        return True
    coverage = coverage_by_site.get(product.site_id) or frozenset({product.country})
    return coverage_reaches_country(coverage, country)


def _config_for_recipient(config: Config, recipient: AlertRecipient) -> Config:
    if is_dataclass(config):
        return replace(config, email_to=recipient.email, email_lang=recipient.language)
    test_config = copy(config)
    setattr(test_config, "email_to", recipient.email)
    setattr(test_config, "email_lang", recipient.language)
    return test_config


def doctor(config: Config) -> int:
    store = build_state_store(config)
    state = store.load()
    inventory = build_inventory_store(config).load()
    config.validate_state()
    if config.alert_dispatch_backend == "service_bus":
        config.validate_alert_pipeline()
    else:
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
        "alert_dispatch_backend": config.alert_dispatch_backend,
        "service_bus_configured": bool(config.service_bus_namespace),
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
        if args.command == "publish-outbox":
            config.validate_alert_pipeline()
            OutboxPublisher(config).publish_pending(limit=args.limit)
            return 0
        if args.command == "fanout-coordinator":
            config.validate_alert_pipeline()
            run_fanout_coordinator(config, once=args.once)
            return 0
        if args.command == "fanout-worker":
            config.validate_alert_pipeline()
            run_fanout_worker(config, once=args.once)
            return 0
        if args.command == "email-worker":
            config.validate_alert_pipeline()
            run_email_worker(config, once=args.once)
            return 0
        if args.command == "delivery-report-worker":
            config.validate_alert_pipeline()
            run_delivery_report_worker(config, once=args.once)
            return 0
        if args.command == "reconcile-alert-recipients":
            config.validate_alert_pipeline()
            updated, removed = RecipientProjection(config).reconcile()
            print(json.dumps({"updated": updated, "removed": removed}))
            return 0
        if args.command == "cleanup-alert-data":
            config.validate_alert_pipeline()
            (
                removed_outbox,
                removed_deliveries,
                removed_delivery_index,
                removed_suppressions,
            ) = cleanup_alert_data(config, limit=args.limit)
            print(
                json.dumps(
                    {
                        "removed_outbox": removed_outbox,
                        "removed_deliveries": removed_deliveries,
                        "removed_delivery_index": removed_delivery_index,
                        "removed_suppressions": removed_suppressions,
                    }
                )
            )
            return 0
        if args.command == "purge-delivery-report-dlq":
            config.validate_alert_pipeline()
            removed = purge_delivery_report_dead_letters(config, limit=args.limit)
            print(json.dumps({"removed_delivery_report_dead_letters": removed}))
            return 0
        if args.command == "pipeline-test":
            config.validate_alert_pipeline()
            recipient_ids: list[str] = list(args.recipient_id or [])
            event = StockAvailableEvent.test_event(target_recipient_ids=recipient_ids)
            outbox = build_outbox(config)
            outbox.create_if_absent(event)
            OutboxPublisher(config, outbox=outbox).publish_pending(limit=100)
            print(json.dumps({"event_id": event.event_id, "target_count": len(recipient_ids)}))
            return 0
        if args.command == "pipeline-status":
            config.validate_alert_pipeline()
            event = build_outbox(config).get(args.event_id).event
            ledger = DeliveryLedger(config)
            statuses = []
            for recipient_id in event.target_recipient_ids:
                job = EmailJob.create(event.event_id, recipient_id)
                try:
                    entity = ledger.get(job)
                    status = str(entity.get("status") or "unknown")
                except Exception as exc:
                    if type(exc).__name__ == "ResourceNotFoundError":
                        status = "not_created"
                    else:
                        raise
                statuses.append({"recipient_id": recipient_id, "status": status})
            print(json.dumps({"event_id": event.event_id, "deliveries": statuses}, indent=2))
            return 0
        if (
            not args.dry_run
            and config.alert_dispatch_backend == "service_bus"
            and config.azure_storage_account_url
        ):
            with scanner_lease(config) as acquired:
                if not acquired:
                    LOG.info("Another scanner execution holds the distributed lease; skipping")
                    return 0
                return check(config, dry_run=False, show_all=args.show_all)
        return check(config, dry_run=args.dry_run, show_all=args.show_all)
    except (ValueError, RuntimeError) as exc:
        LOG.error("%s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
