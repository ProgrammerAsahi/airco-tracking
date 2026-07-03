from __future__ import annotations

import argparse
import json
import logging
import sys

from .adapters import (
    ActionAdapter,
    AircoVoorInHuisAdapter,
    AircoWebwinkelAdapter,
    AlternateAdapter,
    CoolblueAdapter,
    CostwayAdapter,
    CreateStoreAdapter,
    DelonghiAdapter,
    ElectroWorldAdapter,
    EpAdapter,
    EvolarshopAdapter,
    ExpertAdapter,
    FlinqAdapter,
    GammaAdapter,
    HuboAdapter,
    KarweiAdapter,
    KampeerwereldAdapter,
    KlarsteinAdapter,
    KlimaatshopAdapter,
    LidlAdapter,
    MediaMarktAdapter,
    ObelinkAdapter,
    PraxisAdapter,
    SolagoAdapter,
    TrotecAdapter,
    VrijbuiterAdapter,
    WehkampAdapter,
)
from .config import Config
from .fetch import Fetcher
from .mailer import build_message, send_message
from .models import Product
from .state import select_alerts, updated_state
from .state_store import build_state_store


LOG = logging.getLogger("airco_tracker")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Track portable airco stock in the Netherlands")
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


def check(config: Config, *, dry_run: bool, show_all: bool) -> int:
    if not dry_run:
        config.validate_email()
    fetcher = Fetcher(config.request_timeout_seconds)
    adapters = [
        CoolblueAdapter(fetcher),
        MediaMarktAdapter(fetcher),
        EpAdapter(fetcher),
        ElectroWorldAdapter(fetcher),
        WehkampAdapter(fetcher),
        LidlAdapter(fetcher),
        GammaAdapter(fetcher),
        KarweiAdapter(fetcher),
        PraxisAdapter(fetcher),
        AlternateAdapter(fetcher),
        TrotecAdapter(fetcher),
        KlarsteinAdapter(fetcher),
        FlinqAdapter(fetcher),
        ActionAdapter(fetcher),
        ExpertAdapter(fetcher),
        DelonghiAdapter(fetcher),
        ObelinkAdapter(fetcher),
        KampeerwereldAdapter(fetcher),
        CreateStoreAdapter(fetcher),
        CostwayAdapter(fetcher),
        EvolarshopAdapter(fetcher),
        AircoVoorInHuisAdapter(fetcher),
        SolagoAdapter(fetcher),
        HuboAdapter(fetcher),
        VrijbuiterAdapter(fetcher),
        KlimaatshopAdapter(fetcher),
        AircoWebwinkelAdapter(fetcher),
    ]
    products: list[Product] = []
    failures: list[str] = []
    successful_sites: set[str] = set()
    for adapter in adapters:
        try:
            found = adapter.fetch_products()
            products.extend(found)
            successful_sites.add(adapter.site)
            available = sum(product.available for product in found)
            LOG.info("%s: %d products, %d available", adapter.site, len(found), available)
        except Exception as exc:  # Keep other retailers running.
            failures.append(f"{adapter.site}: {exc}")
            LOG.exception("Retailer check failed: %s", adapter.site)

    if not products:
        LOG.error("All retailer checks failed")
        return 2

    if failures:
        LOG.warning(
            "%d retailer check(s) failed; continuing with successful retailers: %s",
            len(failures),
            "; ".join(failures),
        )

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
    print(json.dumps([product.to_dict() for product in visible], ensure_ascii=False, indent=2))

    if dry_run:
        LOG.info("Dry run: %d products would trigger an alert", len(alerts))
        return 0

    # Send before committing state: a failed email will be retried next run.
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
    config.validate_email()
    summary = {
        "app_env": config.app_env,
        "email_backend": config.email_backend,
        "email_to_configured": bool(config.email_to),
        "email_from": config.email_from,
        "email_lang": config.email_lang,
        "state_backend": config.state_backend,
        "known_products": len(state.get("products", {})),
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
            print(f"Test email sent to {config.email_to}")
            return 0
        if args.command == "doctor":
            return doctor(config)
        return check(config, dry_run=args.dry_run, show_all=args.show_all)
    except (ValueError, RuntimeError) as exc:
        LOG.error("%s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
