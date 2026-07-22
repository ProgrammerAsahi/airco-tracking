"""Shared, fail-closed AliExpress affiliate adapter support.

AliExpress' approved SKU endpoint documents product, price, tax and delivery
metadata, but it does not document stock.  Consequently this module never
equates a returned SKU, a price, a delivery estimate, or a promotional link
with availability.  ``fetch_products`` requires a separate, unambiguous
SKU-level orderability signal; the currently documented response therefore
remains diagnostic-only until AliExpress supplies such evidence.
"""

from __future__ import annotations

import logging
import math
import os
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Protocol, Sequence
from urllib.parse import urlsplit, urlunsplit

from ...aliexpress import AliExpressApiError, AliExpressClient
from ...models import Product
from ...partner_feed_store import PartnerFeedCache, build_partner_feed_cache
from ..base import Adapter, is_presale_delivery, parse_btu, parse_cooling_watts_btu


LOG = logging.getLogger(__name__)

_DISCOVERY_TTL = timedelta(hours=12)
_DISCOVERY_PAGE_SIZE = 50
_DISCOVERY_MAX_PAGES = 4
_DISCOVERY_MAX_PRODUCTS = 40
_API_BUDGET_SECONDS = 90.0
_MAX_SINGLE_CALL_SECONDS = 25.0
_MINIMUM_PRICE_EUR = 100.0
_MAXIMUM_PRICE_EUR = 5_000.0
_CACHE_KEY = "portable-air-conditioners"
_PRODUCT_ID_RE = re.compile(r"^[0-9]{5,32}$")
_SKU_ID_RE = re.compile(r"^[0-9A-Za-z._:-]{1,96}$")
_ITEM_PATH_RE = re.compile(r"^/item/([0-9]{5,32})\.html/?$")

_QUERY_FIELDS = ",".join(
    (
        "product_id",
        "product_title",
        "product_detail_url",
        "promotion_link",
        "target_sale_price",
        "target_sale_price_currency",
        "first_level_category_name",
        "second_level_category_name",
        "ship_to_days",
    )
)

_PORTABLE_TERMS = (
    "portable air conditioner",
    "portable air conditioning",
    "portable ac",
    "mobile air conditioner",
    "mobile air conditioning",
    "mobiele airco",
    "mobiele airconditioner",
    "verplaatsbare airco",
    "draagbare airco",
    "climatiseur mobile",
    "climatiseur portable",
    "clim mobile",
    "clim portable",
)
_PORTASPLIT_TERMS = ("portasplit", "porta split", "portable split")
_AIR_CONDITIONER_TERMS = (
    "air conditioner",
    "air conditioning",
    "airco",
    "airconditioner",
    "climatiseur",
)
_ACCESSORY_TERMS = (
    "accessory",
    "accessories",
    "accessoire",
    "accessoires",
    "accessoire airco",
    "window kit",
    "window seal",
    "window vent",
    "exhaust hose",
    "exhaust pipe",
    "drain hose",
    "replacement hose",
    "kit fenetre",
    "kit de fenetre",
    "kit calfeutrage",
    "tuyau d evacuation",
    "gaine de climatiseur",
    "raamafdichting",
    "raamkit",
    "afvoerslang",
    "luchtafvoerslang",
    "filter for",
    "filtre pour",
    "filter voor",
    "remote control",
    "telecommande",
    "afstandsbediening",
    "protective cover",
    "housse de protection",
    "beschermhoes",
    "mounting bracket",
    "support mural",
    "muurbeugel",
    "refrigerant refill",
    "refrigerant gas",
    "gaz refrigerant",
    "koelmiddel",
    "control board",
    "controller board",
    "compressor part",
)
_COOLER_TERMS = (
    "air cooler",
    "aircooler",
    "evaporative cooler",
    "evaporative air cooler",
    "water cooling fan",
    "swamp cooler",
    "rafraichisseur",
    "refroidisseur d air",
    "ventilateur refrigerant",
    "luchtkoeler",
    "verdampingskoeler",
    "watergekoelde ventilator",
    "personal air conditioner",
    "mini air conditioner",
    "desktop air conditioner",
    "usb air conditioner",
)
_FIXED_TERMS = (
    "wall mounted",
    "wall-mounted",
    "mini split",
    "multi split",
    "split system",
    "outdoor unit",
    "indoor unit only",
    "window air conditioner",
    "roof air conditioner",
    "climatiseur mural",
    "climatiseur fixe",
    "climatiseur cassette",
    "unite exterieure",
    "airco voor wandmontage",
    "wandairco",
    "vaste airco",
    "buitenunit",
    "dakairco",
)
_PREORDER_TERMS = (
    "pre-order",
    "pre order",
    "preorder",
    "pre sale",
    "presale",
    "precommande",
    "pre vente",
    "voorbestelling",
    "voorverkoop",
)

_TRUE_STOCK_STATUSES = frozenset(
    {"available", "in_stock", "in-stock", "instock", "orderable", "on_sale", "onsale"}
)
_FALSE_STOCK_STATUSES = frozenset(
    {
        "unavailable",
        "out_of_stock",
        "out-of-stock",
        "outofstock",
        "sold_out",
        "sold-out",
        "soldout",
        "not_orderable",
        "offline",
    }
)
_BOOLEAN_STOCK_FIELDS = frozenset(
    {
        "available",
        "is_available",
        "in_stock",
        "is_in_stock",
        "orderable",
        "is_orderable",
        "can_buy",
    }
)
_QUANTITY_STOCK_FIELDS = frozenset(
    {
        "stock_quantity",
        "available_quantity",
        "available_stock",
        "inventory_quantity",
    }
)
_STATUS_STOCK_FIELDS = frozenset(
    {"availability", "stock_status", "availability_status"}
)
_VERIFIABLE_STOCK_FIELDS = (
    _BOOLEAN_STOCK_FIELDS | _QUANTITY_STOCK_FIELDS | _STATUS_STOCK_FIELDS
)


class _AliExpressApi(Protocol):
    def product_query(self, params: Mapping[str, object]) -> dict[str, Any]: ...

    def product_sku_detail(self, params: Mapping[str, object]) -> dict[str, Any]: ...


class AliExpressAvailabilityUnknown(RuntimeError):
    """Relevant offers exist, but the API did not prove their stock state."""


class AliExpressSkuNoResult(AliExpressAvailabilityUnknown):
    """The SKU API returned code 405, which is not a sold-out signal."""


class AliExpressDiscoveryIncomplete(AliExpressAvailabilityUnknown):
    """Discovery could not prove that it enumerated the complete result set."""


@dataclass(frozen=True)
class AliExpressOffer:
    """A parsed SKU offer used for diagnostics before the adapter is enabled."""

    product_id: str
    sku_id: str
    name: str
    url: str
    affiliate_url: str | None
    price_eur: float
    delivery: str | None
    btu: int | None
    presale: bool
    orderable: bool | None
    sku_result_complete: bool


@dataclass(frozen=True)
class _Candidate:
    product_id: str
    title: str
    detail_url: str | None
    promotion_link: str | None


class AliExpressAffiliateAdapter(Adapter):
    """Country-bound AliExpress discovery and SKU inspection flow.

    Subclasses set a destination country, target language, and localized
    discovery keywords.  The country is part of every API request because an
    AliExpress listing visible for one destination is not proof that it can be
    delivered to another destination.
    """

    site = "AliExpress"
    urls: tuple[str, ...] = ()
    destination_country: str
    target_language: str
    discovery_keywords: tuple[str, ...]
    country: str
    # This must remain ``None`` until AliExpress documents a specific field,
    # or a production response has been independently verified against the
    # checkout page. Merely seeing a familiar-looking, undocumented key must
    # never turn a SKU into stock.
    verified_stock_field: str | None = None

    def __init__(
        self,
        fetcher: Any,
        *,
        client: _AliExpressApi | None = None,
        cache: PartnerFeedCache | None = None,
        now: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        super().__init__(fetcher)
        # Build the environment-backed client lazily. A missing third-party
        # credential must fail only this adapter inside the scanner's per-site
        # try/except, never abort construction of every retailer adapter.
        self._client = client
        self._cache = cache
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._monotonic = monotonic or time.monotonic
        self._last_candidate_count: int | None = None
        self._last_discovery_complete = False
        _validate_adapter_configuration(self)

    def fetch_products(self) -> list[Product]:
        """Return products only when SKU-level stock is explicitly proven.

        Under the currently documented SKU contract, ``inspect_offers`` will
        return offers whose ``orderable`` value is ``None`` and this method
        raises :class:`AliExpressAvailabilityUnknown`.  That deliberate stale
        result is safer than sending a false stock alert.
        """

        offers = self.inspect_offers()
        if not offers:
            if self._last_candidate_count == 0 and self._last_discovery_complete:
                return self.verified_empty(
                    source="affiliate_product_discovery_api",
                    signal="validated complete discovery returned zero relevant candidates",
                )
            raise AliExpressAvailabilityUnknown(
                f"{self.site} {self.destination_country}: discovery or SKU inspection "
                "returned no offers without proving an empty catalogue"
            )

        by_product: dict[str, list[AliExpressOffer]] = {}
        for offer in offers:
            by_product.setdefault(offer.product_id, []).append(offer)

        products: list[Product] = []
        unknown_products: list[str] = []
        for product_id, variants in sorted(by_product.items()):
            orderable = [offer for offer in variants if offer.orderable is True]
            unknown = [offer for offer in variants if offer.orderable is None]
            if orderable:
                # A single explicitly orderable SKU proves that the product
                # can be bought. Unknown sibling variants do not negate that.
                chosen = min(orderable, key=_offer_sort_key)
                products.append(_offer_product(chosen, self.site, self.country, True))
                continue
            if unknown:
                unknown_products.append(product_id)
                continue
            if any(not offer.sku_result_complete for offer in variants):
                # Omitting sku_ids returns at most 20 SKUs. Twenty explicit
                # unavailable variants do not prove that a 21st orderable
                # variant does not exist.
                unknown_products.append(product_id)
                continue
            # Every relevant, valid-price SKU was explicitly unavailable.
            chosen = min(variants, key=_offer_sort_key)
            products.append(_offer_product(chosen, self.site, self.country, False))

        if unknown_products:
            raise AliExpressAvailabilityUnknown(
                f"{self.site} {self.destination_country}: the approved SKU API did not "
                f"provide stock evidence for {len(unknown_products)} relevant product(s)"
            )
        return products

    def inspect_offers(self) -> list[AliExpressOffer]:
        """Return validated offer metadata without claiming unknown stock."""

        deadline = self._monotonic() + _API_BUDGET_SECONDS
        client = self._client or _client_from_environment(self.fetcher)
        self._last_candidate_count = None
        self._last_discovery_complete = False
        candidates = self._known_candidates(_utc(self._now()), client, deadline)
        offers: list[AliExpressOffer] = []
        for candidate in candidates:
            _require_call_budget(self._monotonic, deadline)
            try:
                payload = client.product_sku_detail(
                    {
                        "ship_to_country": self.destination_country,
                        "product_id": candidate.product_id,
                        "target_currency": "EUR",
                        "target_language": self.target_language,
                        "need_deliver_info": "Yes",
                    }
                )
            except AliExpressApiError as exc:
                if exc.code == "405" or (
                    exc.code == "15" and exc.sub_code == "405"
                ):
                    # Production has returned both a direct business code 405
                    # and an IOP envelope with code=15/sub_code=405. Both mean
                    # only "no query result"; neither proves sold-out stock.
                    raise AliExpressSkuNoResult(
                        "AliExpress SKU detail returned no result; stock is unknown"
                    ) from exc
                raise
            item, skus = _sku_parts(payload, candidate.product_id)
            parsed = _offers_from_item(
                item,
                skus,
                candidate,
                destination_country=self.destination_country,
                target_language=self.target_language,
                verified_stock_field=self.verified_stock_field,
                sku_result_complete=len(skus) < 20,
            )
            if len(skus) == 20 and not parsed:
                raise AliExpressAvailabilityUnknown(
                    "AliExpress SKU detail may have truncated all relevant variants"
                )
            offers.extend(parsed)
        return offers

    def parse(self, soup: Any, page_url: str) -> list[Product]:
        raise NotImplementedError("AliExpress uses approved affiliate JSON APIs")

    def _known_candidates(
        self,
        now: datetime,
        client: _AliExpressApi,
        deadline: float,
    ) -> list[_Candidate]:
        cache = self._cache
        if cache is None:
            try:
                cache = build_partner_feed_cache()
            except Exception:
                # Discovery cache is an optimisation. A temporary storage
                # outage must not prevent a complete live discovery result
                # from being inspected during this run.
                LOG.warning(
                    "AliExpress %s discovery cache is unavailable; using memory only",
                    self.destination_country,
                    exc_info=True,
                )
        namespace = f"aliexpress-{self.destination_country.lower()}-discovery-v1"
        stale: list[_Candidate] | None = None
        stale_complete = False
        if cache is not None:
            try:
                payload = cache.load(namespace, _CACHE_KEY)
                if payload is not None:
                    stale, imported, discovery_complete = _cached_candidates(
                        payload, now
                    )
                    stale_complete = discovery_complete
                    if (
                        self.verified_stock_field is not None
                        and not discovery_complete
                    ):
                        # A diagnostic page-one window is useful for contract
                        # inspection, but it must never become the catalogue
                        # baseline once stock mapping is enabled. Do not keep
                        # it as a stale fallback either.
                        stale = None
                    elif now - imported <= _DISCOVERY_TTL:
                        self._last_candidate_count = len(stale)
                        self._last_discovery_complete = discovery_complete
                        return stale
            except Exception:
                LOG.warning(
                    "AliExpress %s discovery cache is invalid; rebuilding it",
                    self.destination_country,
                    exc_info=True,
                )
                stale = None

        try:
            discovered, discovery_complete = self._discover_candidates(
                client, deadline
            )
        except Exception:
            if stale:
                LOG.warning(
                    "AliExpress %s discovery failed; using stale candidate identities",
                    self.destination_country,
                    exc_info=True,
                )
                self._last_candidate_count = len(stale)
                self._last_discovery_complete = stale_complete
                return stale
            raise

        if cache is not None:
            try:
                cache.save(
                    namespace,
                    _CACHE_KEY,
                    {
                        "version": 3,
                        "last_imported": now.isoformat(),
                        "discovery_complete": discovery_complete,
                        "rows": [
                            {
                                "product_id": candidate.product_id,
                                "title": candidate.title,
                                "detail_url": candidate.detail_url,
                                "promotion_link": candidate.promotion_link,
                            }
                            for candidate in discovered
                        ],
                        "source_row_count": len(discovered),
                    },
                )
            except Exception:
                LOG.warning(
                    "AliExpress %s discovery cache could not be saved; "
                    "continuing with the validated in-memory result",
                    self.destination_country,
                    exc_info=True,
                )
        self._last_candidate_count = len(discovered)
        self._last_discovery_complete = discovery_complete
        return discovered

    def _discover_candidates(
        self,
        client: _AliExpressApi,
        deadline: float,
    ) -> tuple[list[_Candidate], bool]:
        found: dict[str, _Candidate] = {}
        discovery_complete = True
        tracking_id = os.getenv("ALIEXPRESS_TRACKING_ID", "").strip()
        for keyword in self.discovery_keywords:
            pagination_unset = object()
            expected_total_pages: int | None | object = pagination_unset
            expected_total_records: int | None = None
            observed_records = 0
            observed_product_ids: set[str] = set()
            for page in range(1, _DISCOVERY_MAX_PAGES + 1):
                _require_call_budget(self._monotonic, deadline)
                params: dict[str, object] = {
                    "keywords": keyword,
                    "ship_to_country": self.destination_country,
                    "target_currency": "EUR",
                    "target_language": self.target_language,
                    "page_no": page,
                    "page_size": _DISCOVERY_PAGE_SIZE,
                    "fields": _QUERY_FIELDS,
                }
                if tracking_id:
                    params["tracking_id"] = tracking_id
                payload = client.product_query(params)
                rows, total_pages, total_records = _query_products(payload, page)
                if expected_total_pages is pagination_unset:
                    expected_total_pages = total_pages
                    expected_total_records = total_records
                elif (
                    total_pages != expected_total_pages
                    or total_records != expected_total_records
                ):
                    raise AliExpressDiscoveryIncomplete(
                        "AliExpress discovery pagination metadata changed during traversal"
                    )
                observed_records += len(rows)
                for row in rows:
                    raw_product_id = _product_id(row.get("product_id"), "query product")
                    if raw_product_id in observed_product_ids:
                        raise AliExpressDiscoveryIncomplete(
                            "AliExpress discovery returned a duplicate product across pages"
                        )
                    observed_product_ids.add(raw_product_id)
                    candidate = _candidate(row)
                    if candidate is not None:
                        found[candidate.product_id] = candidate
                    if len(found) > _DISCOVERY_MAX_PRODUCTS:
                        raise AliExpressDiscoveryIncomplete(
                            "AliExpress discovery exceeded its safe tracked-product limit"
                        )
                if total_pages is None:
                    # The live Standard API currently omits total_page_no and
                    # its advertised total_record_count cannot be reconciled
                    # with traversal: requesting the apparent next page may
                    # return code 405. Treat page one only as a diagnostic
                    # candidate window. A future verified stock field must not
                    # activate against this explicitly truncated catalogue.
                    if self.verified_stock_field is not None:
                        raise AliExpressDiscoveryIncomplete(
                            "AliExpress discovery omitted total_page_no; "
                            "production stock discovery is incomplete"
                        )
                    discovery_complete = False
                    LOG.info(
                        "AliExpress %s discovery is a bounded diagnostic window "
                        "for keyword %s (rows=%s advertised_total=%s)",
                        self.destination_country,
                        keyword,
                        len(rows),
                        total_records,
                    )
                    break
                if page >= total_pages:
                    if observed_records != total_records:
                        raise AliExpressDiscoveryIncomplete(
                            "AliExpress discovery returned an incomplete product count"
                        )
                    break
                if page == _DISCOVERY_MAX_PAGES:
                    raise AliExpressDiscoveryIncomplete(
                        "AliExpress discovery pagination exceeded its safe page limit"
                    )
        return (
            sorted(found.values(), key=lambda value: value.product_id),
            discovery_complete,
        )


def _client_from_environment(fetcher: Any) -> AliExpressClient:
    app_key = os.getenv("ALIEXPRESS_APP_KEY", "").strip()
    app_secret = os.getenv("ALIEXPRESS_APP_SECRET", "").strip()
    if not app_key or not app_secret:
        raise RuntimeError("AliExpress API credentials are not configured")
    return AliExpressClient(
        fetcher=fetcher,
        app_key=app_key,
        app_secret=app_secret,
        timeout=min(float(fetcher.timeout), 8.0),
    )


def _require_call_budget(monotonic: Callable[[], float], deadline: float) -> None:
    if deadline - monotonic() < _MAX_SINGLE_CALL_SECONDS:
        raise AliExpressAvailabilityUnknown(
            "AliExpress inspection exhausted its per-country API time budget"
        )


def _validate_adapter_configuration(adapter: AliExpressAffiliateAdapter) -> None:
    if adapter.destination_country not in {"FR", "NL"}:
        raise ValueError("AliExpress destination country must be FR or NL")
    if adapter.target_language not in {"FR", "NL"}:
        raise ValueError("AliExpress target language must be FR or NL")
    if adapter.country != adapter.destination_country.lower():
        raise ValueError("AliExpress adapter country does not match its destination")
    if not adapter.discovery_keywords or any(
        not isinstance(value, str) or not value.strip()
        for value in adapter.discovery_keywords
    ):
        raise ValueError("AliExpress discovery keywords are not configured")
    if (
        adapter.verified_stock_field is not None
        and adapter.verified_stock_field not in _VERIFIABLE_STOCK_FIELDS
    ):
        raise ValueError("AliExpress verified stock field is not allowlisted")


def _query_products(
    payload: Any, requested_page: int
) -> tuple[list[dict[str, Any]], int | None, int]:
    if not isinstance(payload, dict):
        raise RuntimeError("AliExpress product query returned an invalid result")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("AliExpress product query returned an invalid result")
    required = {
        "current_page_no",
        "current_record_count",
        "total_record_count",
    }
    if not required.issubset(result):
        raise RuntimeError("AliExpress product query omitted pagination metadata")
    current_page = _strict_positive_int(result["current_page_no"], "query current page")
    if current_page != requested_page:
        raise RuntimeError("AliExpress product query returned an unexpected page")
    current_records = _strict_nonnegative_int(
        result["current_record_count"], "query current records"
    )
    raw_total_pages = result.get("total_page_no")
    total_pages = (
        _strict_nonnegative_int(raw_total_pages, "query total pages")
        if raw_total_pages is not None
        else None
    )
    total_records = _strict_nonnegative_int(
        result["total_record_count"], "query total records"
    )
    products = result.get("products")
    if products is None and current_records == 0:
        rows: list[dict[str, Any]] = []
    elif not isinstance(products, dict) or not isinstance(products.get("product"), list):
        raise RuntimeError("AliExpress product query returned an invalid product list")
    else:
        rows = products["product"]
    if len(rows) > _DISCOVERY_PAGE_SIZE or any(not isinstance(row, dict) for row in rows):
        raise RuntimeError("AliExpress product query returned invalid products")
    if current_records != len(rows):
        raise RuntimeError("AliExpress product query returned an inconsistent page count")
    if total_records == 0:
        if total_pages not in {None, 0, 1} or rows:
            raise RuntimeError("AliExpress product query returned inconsistent empty pagination")
        return [], total_pages, 0
    if total_pages == 0 or total_records < current_records:
        raise RuntimeError("AliExpress product query returned inconsistent pagination")
    if not rows:
        raise RuntimeError("AliExpress product query pagination ended unexpectedly")
    if total_pages is not None and requested_page > total_pages:
        raise RuntimeError("AliExpress product query pagination ended unexpectedly")
    return rows, total_pages, total_records


def _candidate(row: dict[str, Any]) -> _Candidate | None:
    product_id = _product_id(row.get("product_id"), "query product")
    title = _required_text(row.get("product_title"), "query product title", maximum=1_000)
    categories = " ".join(
        str(row.get(key) or "")
        for key in ("first_level_category_name", "second_level_category_name")
    )
    if not _is_portable_air_conditioner(title, categories):
        return None
    currency = _required_text(
        row.get("target_sale_price_currency"),
        "query target currency",
        maximum=8,
    ).upper()
    if currency != "EUR":
        raise RuntimeError("AliExpress query price is not denominated in EUR")
    query_price = _strict_decimal(row.get("target_sale_price"), "query target price")
    if query_price < _MINIMUM_PRICE_EUR or query_price > _MAXIMUM_PRICE_EUR:
        return None
    detail_url = _validated_item_url(
        row.get("product_detail_url"), product_id, "query product"
    )
    promotion_link = _optional_product_affiliate_url(
        row.get("promotion_link"), product_id
    )
    if detail_url is None:
        raise RuntimeError("AliExpress query product has no canonical URL")
    return _Candidate(product_id, title, detail_url, promotion_link)


def _sku_parts(
    payload: Any, expected_product_id: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not isinstance(payload, dict):
        raise RuntimeError("AliExpress SKU detail returned an invalid result")
    nodes: list[dict[str, Any]] = [payload]
    node = payload
    for _ in range(3):
        nested = node.get("result")
        if not isinstance(nested, dict):
            break
        nodes.append(nested)
        node = nested

    for current in nodes:
        code = current.get("code")
        success = current.get("success")
        if success is not None and not _success_value(success):
            raise RuntimeError("AliExpress SKU detail reported an unsuccessful result")
        if code is not None and str(code) not in {"0", "200"}:
            raise RuntimeError("AliExpress SKU detail reported an API failure")

    container = next(
        (
            value
            for value in nodes
            if "ae_item_info" in value or "ae_item_sku_info" in value
        ),
        None,
    )
    if not isinstance(container, dict):
        raise RuntimeError("AliExpress SKU detail omitted its item container")
    item = container.get("ae_item_info")
    if not isinstance(item, dict):
        raise RuntimeError("AliExpress SKU detail omitted ae_item_info")
    product_id = _product_id(item.get("product_id"), "SKU product")
    if product_id != expected_product_id:
        raise RuntimeError("AliExpress SKU detail returned an unexpected product")
    raw_skus = container.get("ae_item_sku_info")
    if isinstance(raw_skus, dict):
        # Production wraps the documented SKU array in this exact key. Keep
        # the direct-list form for the published example, but do not recurse
        # through arbitrary undocumented containers.
        skus = raw_skus.get("traffic_sku_info_list")
    else:
        skus = raw_skus
    if (
        not isinstance(skus, list)
        or not skus
        or len(skus) > 20
        or any(not isinstance(sku, dict) for sku in skus)
    ):
        raise RuntimeError("AliExpress SKU detail returned an invalid SKU list")
    return item, skus


def _offers_from_item(
    item: dict[str, Any],
    skus: Sequence[dict[str, Any]],
    candidate: _Candidate,
    *,
    destination_country: str,
    target_language: str,
    verified_stock_field: str | None,
    sku_result_complete: bool,
) -> list[AliExpressOffer]:
    localized_title = _required_text(item.get("title"), "SKU product title", maximum=1_000)
    english_title = _optional_text(item.get("en_title"), maximum=1_000)
    categories = " ".join(
        _optional_text(item.get(key), maximum=500) or ""
        for key in (
            "display_category_name_l1",
            "display_category_name_l2",
            "display_category_name_l3",
            "display_category_name_l4",
            "first_level_category_name",
            "second_level_category_name",
            "leaf_category_name",
        )
    )
    if not _is_portable_air_conditioner(
        " ".join((candidate.title, localized_title, english_title or "")), categories
    ):
        return []

    product_id = _product_id(item.get("product_id"), "SKU product")
    original_link = item.get("original_link")
    if original_link is not None and str(original_link).strip():
        _validated_item_url(original_link, product_id, "SKU product")
    canonical_url = _canonical_product_url(product_id)
    offers: list[AliExpressOffer] = []
    for sku in skus:
        sku_id = _sku_id(sku.get("sku_id"))
        variant_text = _variant_text(sku)
        if _is_excluded_variant(variant_text):
            continue
        price = _sku_price_eur(sku)
        if price is None:
            continue
        orderable = _orderability(sku, verified_stock_field)
        name = _variant_name(localized_title, sku)
        searchable = " ".join(
            (candidate.title, localized_title, english_title or "", variant_text)
        )
        affiliate_url = _optional_product_affiliate_url(sku.get("link"), product_id)
        if affiliate_url is None:
            affiliate_url = candidate.promotion_link
        offers.append(
            AliExpressOffer(
                product_id=product_id,
                sku_id=sku_id,
                name=name,
                url=canonical_url,
                affiliate_url=affiliate_url,
                price_eur=price,
                delivery=_delivery_text(sku, destination_country, target_language),
                btu=parse_btu(searchable) or parse_cooling_watts_btu(searchable),
                presale=is_presale_delivery(searchable)
                or any(term in _normalise(searchable) for term in _PREORDER_TERMS),
                orderable=orderable,
                sku_result_complete=sku_result_complete,
            )
        )
    return offers


def _is_portable_air_conditioner(title: str, supporting_text: str = "") -> bool:
    name = _normalise(title)
    support = _normalise(supporting_text)
    combined = f"{name} {support}"
    if any(term in name for term in _ACCESSORY_TERMS):
        return False
    if any(term in name for term in _COOLER_TERMS):
        return False
    is_portasplit = any(term in name for term in _PORTASPLIT_TERMS)
    if not is_portasplit and any(term in name for term in _FIXED_TERMS):
        return False
    portable = is_portasplit or any(term in combined for term in _PORTABLE_TERMS)
    air_conditioner = is_portasplit or any(
        term in combined for term in _AIR_CONDITIONER_TERMS
    )
    return portable and air_conditioner


def _is_excluded_variant(value: str) -> bool:
    text = _normalise(value)
    if any(term in text for term in _ACCESSORY_TERMS) or any(
        term in text for term in _COOLER_TERMS
    ):
        return True
    is_portasplit = any(term in text for term in _PORTASPLIT_TERMS)
    return not is_portasplit and any(term in text for term in _FIXED_TERMS)


def _sku_price_eur(sku: dict[str, Any]) -> float | None:
    currency = _required_text(sku.get("currency"), "SKU currency", maximum=8).upper()
    if currency != "EUR":
        raise RuntimeError("AliExpress SKU price is not denominated in EUR")
    raw = sku.get("sale_price_with_tax")
    if raw is None or str(raw).strip() == "":
        raw = sku.get("price_with_tax")
    value = _strict_decimal(raw, "SKU tax-inclusive price")
    if value < _MINIMUM_PRICE_EUR or value > _MAXIMUM_PRICE_EUR:
        return None
    return round(value, 2)


def _orderability(
    sku: dict[str, Any], verified_stock_field: str | None
) -> bool | None:
    # The approved contract currently contains no inventory/orderability
    # field. Ignore every stock-looking key unless one exact field has been
    # explicitly enabled after separate verification.
    if verified_stock_field is None or verified_stock_field not in sku:
        return None
    value = sku[verified_stock_field]
    if verified_stock_field in _BOOLEAN_STOCK_FIELDS:
        return _strict_bool(value, f"SKU {verified_stock_field}")
    if verified_stock_field in _QUANTITY_STOCK_FIELDS:
        return _strict_nonnegative_int(value, f"SKU {verified_stock_field}") > 0
    if verified_stock_field in _STATUS_STOCK_FIELDS:
        status = _normalise(
            _required_text(value, f"SKU {verified_stock_field}", maximum=64)
        ).replace(" ", "_")
        if status in _TRUE_STOCK_STATUSES:
            return True
        if status in _FALSE_STOCK_STATUSES:
            return False
        raise RuntimeError("AliExpress SKU returned an unknown stock status")
    raise RuntimeError("AliExpress verified stock field is not allowlisted")


def _delivery_text(sku: dict[str, Any], country: str, language: str) -> str | None:
    parts: list[str] = []
    ship_from = _optional_text(sku.get("ship_from_country"), maximum=8)
    minimum = _optional_nonnegative_int(
        _first_present(sku, "min_delivery_days", "delivery_days_min"),
        "minimum delivery days",
    )
    maximum = _optional_nonnegative_int(
        _first_present(sku, "max_delivery_days", "delivery_days_max"),
        "maximum delivery days",
    )
    exact = _optional_nonnegative_int(sku.get("delivery_days"), "delivery days")
    if minimum is not None and maximum is not None and minimum > maximum:
        raise RuntimeError("AliExpress SKU returned an invalid delivery range")
    shipping = sku.get("shipping_fees")
    shipping_fee = None if shipping is None or str(shipping).strip() == "" else _strict_decimal(
        shipping, "shipping fee"
    )

    if language == "FR":
        if ship_from:
            parts.append(f"Expédié depuis {ship_from.upper()}")
        if minimum is not None and maximum is not None:
            parts.append(f"livraison estimée sous {minimum}–{maximum} jours")
        elif exact is not None:
            parts.append(f"livraison estimée sous {exact} jours")
        if shipping_fee is not None:
            parts.append(f"livraison {shipping_fee:.2f} €")
        if not parts:
            parts.append(f"Destination {country}")
    else:
        if ship_from:
            parts.append(f"Verzonden vanuit {ship_from.upper()}")
        if minimum is not None and maximum is not None:
            parts.append(f"geschatte levering {minimum}–{maximum} dagen")
        elif exact is not None:
            parts.append(f"geschatte levering {exact} dagen")
        if shipping_fee is not None:
            parts.append(f"verzendkosten € {shipping_fee:.2f}")
        if not parts:
            parts.append(f"Bestemming {country}")
    return " · ".join(parts)


def _variant_text(sku: dict[str, Any]) -> str:
    values: list[str] = []
    for key in ("color", "size", "sku_properties"):
        value = sku.get(key)
        if isinstance(value, str) and value.strip():
            values.append(value.strip())
        elif isinstance(value, list):
            values.extend(str(part).strip() for part in value if str(part).strip())
        elif isinstance(value, dict):
            values.extend(
                f"{key_name} {key_value}" for key_name, key_value in value.items()
            )
    return " ".join(values)


def _variant_name(title: str, sku: dict[str, Any]) -> str:
    suffixes: list[str] = []
    for key in ("color", "size"):
        value = _optional_text(sku.get(key), maximum=200)
        if value and _normalise(value) not in _normalise(title):
            suffixes.append(value)
    return f"{title} — {' / '.join(suffixes)}" if suffixes else title


def _optional_aliexpress_url(value: Any, *, canonical: bool) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    if not isinstance(value, str) or len(value) > 4_096:
        raise RuntimeError("AliExpress returned an invalid URL")
    try:
        parsed = urlsplit(value.strip())
    except ValueError as exc:
        raise RuntimeError("AliExpress returned an invalid URL") from exc
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme.lower() != "https"
        or parsed.username is not None
        or parsed.password is not None
        or not (host == "aliexpress.com" or host.endswith(".aliexpress.com"))
    ):
        raise RuntimeError("AliExpress returned an unexpected URL host")
    if canonical:
        return urlunsplit(("https", parsed.netloc.lower(), parsed.path, "", ""))
    return urlunsplit(("https", parsed.netloc.lower(), parsed.path, parsed.query, ""))


def _optional_affiliate_url(value: Any) -> str | None:
    try:
        return _optional_aliexpress_url(value, canonical=False)
    except RuntimeError:
        # Promotional links are optional enrichment. A changed or unexpected
        # affiliate host must never be copied to users, but it must not make
        # otherwise authoritative inventory stale either.
        LOG.warning("AliExpress returned an unusable promotional URL; ignoring it")
        return None


def _optional_product_affiliate_url(value: Any, product_id: str) -> str | None:
    url = _optional_affiliate_url(value)
    if url is None:
        return None
    parsed = urlsplit(url)
    match = _ITEM_PATH_RE.fullmatch(parsed.path)
    if match is not None and match.group(1) != product_id:
        LOG.warning("AliExpress returned a promotional URL for another product; ignoring it")
        return None
    return url


def _validated_item_url(value: Any, product_id: str, label: str) -> str:
    url = _optional_aliexpress_url(value, canonical=True)
    if url is None:
        raise RuntimeError(f"AliExpress {label} has no canonical URL")
    parsed = urlsplit(url)
    match = _ITEM_PATH_RE.fullmatch(parsed.path)
    if match is None or match.group(1) != product_id:
        raise RuntimeError(f"AliExpress {label} URL does not match its product id")
    return _canonical_product_url(product_id)


def _canonical_product_url(product_id: str) -> str:
    return f"https://www.aliexpress.com/item/{product_id}.html"


def _cached_candidates(
    payload: dict[str, Any], now: datetime
) -> tuple[list[_Candidate], datetime, bool]:
    rows = payload.get("rows")
    timestamp = payload.get("last_imported")
    source_row_count = payload.get("source_row_count")
    discovery_complete = payload.get("discovery_complete")
    if (
        payload.get("version") != 3
        or not isinstance(rows, list)
        or not isinstance(timestamp, str)
        or isinstance(source_row_count, bool)
        or not isinstance(source_row_count, int)
        or source_row_count != len(rows)
        or not isinstance(discovery_complete, bool)
    ):
        raise RuntimeError("Invalid AliExpress discovery cache")
    try:
        imported = _utc(datetime.fromisoformat(timestamp.replace("Z", "+00:00")))
    except ValueError as exc:
        raise RuntimeError("Invalid AliExpress discovery cache timestamp") from exc
    if imported > now + timedelta(minutes=5):
        raise RuntimeError("AliExpress discovery cache timestamp is in the future")
    candidates: list[_Candidate] = []
    for row in rows:
        if not isinstance(row, dict):
            raise RuntimeError("Invalid AliExpress discovery cache row")
        product_id = _product_id(row.get("product_id"), "cached product")
        candidates.append(
            _Candidate(
                product_id,
                _required_text(row.get("title"), "cached title", maximum=1_000),
                _validated_item_url(
                    row.get("detail_url"), product_id, "cached product"
                ),
                _optional_product_affiliate_url(
                    row.get("promotion_link"), product_id
                ),
            )
        )
    product_ids = [candidate.product_id for candidate in candidates]
    if len(product_ids) > _DISCOVERY_MAX_PRODUCTS or len(set(product_ids)) != len(product_ids):
        raise RuntimeError("Invalid AliExpress discovery cache products")
    return (
        sorted(candidates, key=lambda value: value.product_id),
        imported,
        discovery_complete,
    )


def _offer_product(offer: AliExpressOffer, site: str, country: str, available: bool) -> Product:
    return Product(
        site=site,
        name=offer.name,
        url=offer.url,
        available=available,
        price_eur=offer.price_eur,
        delivery=offer.delivery,
        btu=offer.btu,
        presale=available and offer.presale,
        country=country,
        affiliate_url=offer.affiliate_url,
    )


def _offer_sort_key(offer: AliExpressOffer) -> tuple[bool, float, str]:
    return offer.presale, offer.price_eur, offer.sku_id


def _product_id(value: Any, label: str) -> str:
    text = str(value).strip() if not isinstance(value, bool) else ""
    if _PRODUCT_ID_RE.fullmatch(text) is None:
        raise RuntimeError(f"AliExpress {label} has an invalid product id")
    return text


def _sku_id(value: Any) -> str:
    text = str(value).strip() if not isinstance(value, bool) else ""
    if _SKU_ID_RE.fullmatch(text) is None:
        raise RuntimeError("AliExpress SKU has an invalid id")
    return text


def _required_text(value: Any, label: str, *, maximum: int) -> str:
    if not isinstance(value, str):
        raise RuntimeError(f"AliExpress {label} is invalid")
    text = " ".join(value.split())
    if not text or len(text) > maximum or _has_control_characters(text):
        raise RuntimeError(f"AliExpress {label} is invalid")
    return text


def _optional_text(value: Any, *, maximum: int) -> str | None:
    if value is None or value == "":
        return None
    return _required_text(value, "text field", maximum=maximum)


def _strict_decimal(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise RuntimeError(f"AliExpress {label} is invalid")
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError(f"AliExpress {label} is invalid") from exc
    if not math.isfinite(parsed) or parsed < 0:
        raise RuntimeError(f"AliExpress {label} is invalid")
    return parsed


def _strict_positive_int(value: Any, label: str) -> int:
    parsed = _strict_nonnegative_int(value, label)
    if parsed == 0:
        raise RuntimeError(f"AliExpress {label} is invalid")
    return parsed


def _strict_nonnegative_int(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise RuntimeError(f"AliExpress {label} is invalid")
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError(f"AliExpress {label} is invalid") from exc
    if parsed < 0 or str(value).strip() not in {str(parsed), f"{parsed}.0"}:
        raise RuntimeError(f"AliExpress {label} is invalid")
    return parsed


def _optional_nonnegative_int(value: Any, label: str) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    return _strict_nonnegative_int(value, label)


def _strict_bool(value: Any, label: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalised = value.strip().casefold()
        if normalised in {"true", "yes", "1"}:
            return True
        if normalised in {"false", "no", "0"}:
            return False
    raise RuntimeError(f"AliExpress {label} is invalid")


def _success_value(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, str) and value.strip().casefold() == "true":
        return True
    return False


def _first_present(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def _normalise(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    ascii_text = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", ascii_text).split())


def _has_control_characters(value: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise RuntimeError("AliExpress adapter requires timezone-aware timestamps")
    return value.astimezone(timezone.utc)
