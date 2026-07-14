from __future__ import annotations

import logging
import math
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Protocol, Sequence
from urllib.parse import urlencode

from ...models import Product
from ...partner_feed_store import PartnerFeedCache, build_partner_feed_cache
from ..base import Adapter, parse_btu, parse_cooling_watts_btu
from .common import is_real_air_conditioner_fr


LOG = logging.getLogger(__name__)

_API_ROOT = "https://www.e.leclerc/api/rest/live-api"
_SEARCH_URL = f"{_API_ROOT}/product-search"
_BULK_URL = f"{_API_ROOT}/stores/0100-0000/products-details-by-skus"
_MERCHANT_URL = "https://www.e.leclerc/fp/{sku}"
_AWIN_URL = "https://www.awin1.com/cread.php"
_AWIN_ADVERTISER_ID = "15135"
_AWIN_PUBLISHER_ID = "2981827"

_DISCOVERY_QUERY = "climatiseur mobile"
_DISCOVERY_TTL = timedelta(hours=12)
_DISCOVERY_PAGE_SIZE = 96
_DISCOVERY_MAX_PAGES = 4
_DISCOVERY_DELAY_SECONDS = 1.5
_BULK_BATCH_SIZE = 40
_OFFER_CLOCK_SKEW = timedelta(minutes=5)
_CACHE_NAMESPACE = "eleclerc-fr-live-api-v2"
_CACHE_KEY = "discovery"

_PORTABLE_RE = re.compile(
    r"(?:\bclimatiseurs?\b.{0,56}?\b(?:mobiles?|portables?)\b|"
    r"\b(?:mobile|portable)\s+air\s+conditioners?\b|"
    r"\bporta\s*split\b)",
    re.I,
)
_NON_PORTABLE_RE = re.compile(
    r"\b(?:climatiseur\s+(?:mural|fixe|de\s+toit|cassette)|"
    r"split\s+(?:mural|fixe)|unite\s+exterieure|unit[ée]\s+ext[ée]rieure)\b",
    re.I,
)
_SKU_RE = re.compile(r"^[0-9A-Za-z._-]{4,64}$")
_ISO_FRACTION_RE = re.compile(
    r"\.(?P<fraction>\d+)(?P<timezone>Z|[+-]\d{2}:\d{2})$"
)

_IMMEDIATE_STATUS = "in-stock"
_PRESALE_STATUSES = frozenset(
    {"preorder", "unlimited-preorder", "forthcoming", "future-stock"}
)
_UNAVAILABLE_STATUSES = frozenset(
    {"temporarily-unavailable", "unavailable", "shipped-under"}
)
_KNOWN_STATUSES = frozenset(
    {_IMMEDIATE_STATUS, *_PRESALE_STATUSES, *_UNAVAILABLE_STATUSES}
)


class _LiveApi(Protocol):
    def search(self, query: str, page: int, size: int) -> Any: ...

    def product_details(self, skus: Sequence[str]) -> Any: ...


class _ELeclercLiveApiClient:
    def __init__(self, session: Any, timeout: int) -> None:
        self._session = session
        self._timeout = timeout

    def search(self, query: str, page: int, size: int) -> Any:
        response = self._session.post(
            _SEARCH_URL,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            json={
                "text": query,
                "page": page,
                "size": size,
                "filters": {"type_de_produit": {"value": ["Climatiseur"]}},
            },
            timeout=self._timeout,
        )
        response.raise_for_status()
        return _response_json(response, "search")

    def product_details(self, skus: Sequence[str]) -> Any:
        response = self._session.get(
            _BULK_URL,
            headers={"Accept": "application/json"},
            params=[("skus", sku) for sku in skus],
            timeout=self._timeout,
        )
        response.raise_for_status()
        return _response_json(response, "product details")


class ELeclercFranceAdapter(Adapter):
    """Track E.Leclerc mobile air conditioners from its browser-facing API.

    Product discovery is deliberately infrequent and persisted through the
    same local/Azure cache abstraction used by partner feeds. Stock itself is
    never served from that cache: every scanner run refreshes all known SKUs
    through the compact bulk-details endpoint.
    """

    site = "E.Leclerc France"
    urls = ()

    def __init__(
        self,
        fetcher: Any,
        *,
        cache: PartnerFeedCache | None = None,
        client: _LiveApi | None = None,
        session: Any | None = None,
        now: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        super().__init__(fetcher)
        if client is not None and session is not None:
            raise ValueError("Pass either an E.Leclerc client or a session, not both")
        self._cache = cache
        self._client = (
            client
            if client is not None
            else _ELeclercLiveApiClient(
                session if session is not None else fetcher.session,
                int(fetcher.timeout),
            )
        )
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._sleep = sleep

    def fetch_products(self) -> list[Product]:
        now = _utc(self._now())
        skus = self._known_skus(now)
        if not skus:
            return []

        details: dict[str, dict[str, Any]] = {}
        for batch in _chunks(skus, _BULK_BATCH_SIZE):
            payload = self._client.product_details(batch)
            details.update(_validated_details(payload, batch))

        if set(details) != set(skus):
            missing = sorted(set(skus) - set(details))
            raise RuntimeError(
                "E.Leclerc France: bulk response omitted known SKU(s): "
                + ", ".join(missing)
            )

        products: list[Product] = []
        for sku in skus:
            item = details[sku]
            family = _family_code(item)
            if family != "climatiseur":
                continue
            product = _product_from_item(item, sku, now)
            if product is not None:
                products.append(product)
        if not products:
            raise RuntimeError(
                "E.Leclerc France: no valid mobile air conditioners in bulk response"
            )
        return products

    def parse(self, soup: Any, page_url: str) -> list[Product]:
        raise NotImplementedError("E.Leclerc France uses its first-party JSON API")

    def _known_skus(self, now: datetime) -> list[str]:
        cache = self._cache if self._cache is not None else build_partner_feed_cache()
        cached_skus: list[str] | None = None
        cached_at: datetime | None = None
        try:
            cached = cache.load(_CACHE_NAMESPACE, _CACHE_KEY)
            if cached is not None:
                cached_skus, cached_at = _validate_cache(cached, now)
                if now - cached_at <= _DISCOVERY_TTL:
                    return cached_skus
        except Exception:
            # A corrupt cache is not stock evidence. Rebuild it from the live
            # discovery API instead of leaving the site stale indefinitely.
            LOG.warning(
                "E.Leclerc France discovery cache is invalid; rebuilding it",
                exc_info=True,
            )
            cached_skus = None
            cached_at = None

        try:
            discovered = self._discover_skus()
        except Exception:
            if cached_skus:
                LOG.warning(
                    "E.Leclerc France discovery failed; refreshing the stale known-SKU set",
                    exc_info=True,
                )
                return cached_skus
            raise

        cache.save(
            _CACHE_NAMESPACE,
            _CACHE_KEY,
            {
                "version": 2,
                "last_imported": now.isoformat(),
                "rows": [{"sku": sku} for sku in discovered],
                "source_row_count": len(discovered),
            },
        )
        return discovered

    def _discover_skus(self) -> list[str]:
        discovered: set[str] = set()
        source_total: int | None = None
        page = 1
        while True:
            if page > 1:
                self._sleep(_DISCOVERY_DELAY_SECONDS)
            payload = self._client.search(_DISCOVERY_QUERY, page, _DISCOVERY_PAGE_SIZE)
            items, count, total = _validated_search(payload, page)
            if source_total is None:
                source_total = total
            elif total != source_total:
                raise RuntimeError("E.Leclerc France: search total changed during pagination")
            for item in items:
                if _is_candidate(item):
                    discovered.add(_required_sku(item, "search item"))
            if count == 0 or page * _DISCOVERY_PAGE_SIZE >= total:
                break
            if page >= _DISCOVERY_MAX_PAGES:
                raise RuntimeError("E.Leclerc France: search page cap exceeded")
            page += 1

        if not discovered and source_total != 0:
            raise RuntimeError("E.Leclerc France: discovery found no mobile air conditioners")
        return sorted(discovered)


def _response_json(response: Any, label: str) -> Any:
    try:
        return response.json()
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"E.Leclerc France: invalid {label} JSON") from exc


def _validated_search(
    payload: Any,
    page: int,
) -> tuple[list[dict[str, Any]], int, int]:
    if not isinstance(payload, dict):
        raise RuntimeError("E.Leclerc France: search response is not an object")
    items = payload.get("items")
    count = payload.get("count")
    total = payload.get("total")
    if (
        not isinstance(items, list)
        or not isinstance(count, int)
        or isinstance(count, bool)
        or not isinstance(total, int)
        or isinstance(total, bool)
        or count < 0
        or total < 0
        or count != len(items)
        or count > _DISCOVERY_PAGE_SIZE
        or total > _DISCOVERY_PAGE_SIZE * _DISCOVERY_MAX_PAGES
    ):
        raise RuntimeError("E.Leclerc France: invalid search response schema")
    if page > 1 and count == 0 and (page - 1) * _DISCOVERY_PAGE_SIZE < total:
        raise RuntimeError("E.Leclerc France: search pagination ended unexpectedly")
    if any(not isinstance(item, dict) for item in items):
        raise RuntimeError("E.Leclerc France: search contains an invalid product")
    return items, count, total


def _validated_details(
    payload: Any,
    requested_skus: Sequence[str],
) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, list):
        raise RuntimeError("E.Leclerc France: bulk response is not an array")
    expected = set(requested_skus)
    result: dict[str, dict[str, Any]] = {}
    for item in payload:
        if item is None:
            raise RuntimeError("E.Leclerc France: bulk response contains a missing product")
        if not isinstance(item, dict):
            raise RuntimeError("E.Leclerc France: bulk response contains an invalid product")
        sku = _required_sku(item, "bulk product")
        if sku not in expected or sku in result:
            raise RuntimeError("E.Leclerc France: bulk response contains an unexpected SKU")
        variants = item.get("variants")
        if not isinstance(variants, list) or not variants:
            raise RuntimeError("E.Leclerc France: bulk product has no variants")
        if any(not isinstance(variant, dict) for variant in variants):
            raise RuntimeError("E.Leclerc France: bulk product has an invalid variant")
        if not any(str(variant.get("sku") or "").strip() == sku for variant in variants):
            raise RuntimeError("E.Leclerc France: bulk product has no matching variant")
        result[sku] = item
    missing = expected - set(result)
    if missing:
        raise RuntimeError(
            "E.Leclerc France: bulk response omitted known SKU(s): "
            + ", ".join(sorted(missing))
        )
    return result


def _is_candidate(item: dict[str, Any]) -> bool:
    if _family_code(item) != "climatiseur":
        return False
    name = _required_string(item.get("label"), "search product label")
    return bool(
        _PORTABLE_RE.search(name)
        and not _NON_PORTABLE_RE.search(name)
        and is_real_air_conditioner_fr(name)
    )


def _product_from_item(
    item: dict[str, Any],
    sku: str,
    now: datetime,
) -> Product | None:
    name = _required_string(item.get("label"), "bulk product label")
    text = _item_text(item)
    if not (
        _PORTABLE_RE.search(name)
        and not _NON_PORTABLE_RE.search(name)
        and is_real_air_conditioner_fr(name)
    ):
        return None

    variant = next(
        variant
        for variant in item["variants"]
        if str(variant.get("sku") or "").strip() == sku
    )
    offers = variant.get("offers")
    if not isinstance(offers, list):
        raise RuntimeError("E.Leclerc France: matching variant has no offer list")
    if any(not isinstance(offer, dict) for offer in offers):
        raise RuntimeError("E.Leclerc France: matching variant has an invalid offer")

    immediate: list[_OfferChoice] = []
    presale: list[_OfferChoice] = []
    for offer in offers:
        status = _availability_status(offer)
        if status == _IMMEDIATE_STATUS:
            stock = offer.get("stock")
            if not isinstance(stock, int) or isinstance(stock, bool) or stock < 0:
                raise RuntimeError("E.Leclerc France: in-stock offer has invalid stock")
            if stock == 0:
                continue
            choice = _available_offer(offer, now)
            if choice is not None:
                immediate.append(choice)
        elif status in _PRESALE_STATUSES:
            stock = offer.get("stock")
            if stock is not None and (
                not isinstance(stock, int) or isinstance(stock, bool) or stock < 0
            ):
                raise RuntimeError("E.Leclerc France: presale offer has invalid stock")
            choice = _available_offer(offer, now)
            if choice is not None:
                presale.append(choice)
        elif status not in _UNAVAILABLE_STATUSES:
            raise RuntimeError("E.Leclerc France: unsupported availability status")

    chosen: _OfferChoice | None = None
    is_presale = False
    if immediate:
        chosen = min(immediate, key=_offer_sort_key)
    elif presale:
        chosen = min(presale, key=_offer_sort_key)
        is_presale = True

    merchant_url = _MERCHANT_URL.format(sku=sku)
    return Product(
        site="E.Leclerc France",
        name=name,
        url=_awin_deep_link(merchant_url),
        available=chosen is not None,
        price_eur=chosen.price if chosen else None,
        delivery=(
            f"Précommande — vendu par {chosen.seller}"
            if chosen and is_presale
            else f"En stock — vendu par {chosen.seller}"
            if chosen
            else "Indisponible"
        ),
        btu=parse_btu(f"{name} {text}") or parse_cooling_watts_btu(text),
        presale=is_presale,
        country="fr",
    )


class _OfferChoice:
    def __init__(self, price: float, seller: str, offer_id: str) -> None:
        self.price = price
        self.seller = seller
        self.offer_id = offer_id


def _available_offer(
    offer: dict[str, Any],
    now: datetime,
) -> _OfferChoice | None:
    currency = offer.get("currency")
    shop = offer.get("shop")
    if not isinstance(currency, dict) or currency.get("code") != "EUR":
        raise RuntimeError("E.Leclerc France: available offer has invalid EUR currency")
    if not isinstance(shop, dict):
        raise RuntimeError("E.Leclerc France: available offer has invalid seller")
    seller = str(shop.get("label") or "").strip()
    seller_id = str(shop.get("id") or "").strip()
    if not seller or not seller_id:
        raise RuntimeError("E.Leclerc France: available offer has invalid seller")

    price = _offer_price(offer)
    if price is None:
        raise RuntimeError("E.Leclerc France: available offer has invalid price")
    start = _offer_date(offer, "startDate", required=True)
    end = _offer_date(offer, "endDate", required=False)
    if start is None:
        raise RuntimeError("E.Leclerc France: available offer has invalid start date")
    # The live API stamps some marketplace offers while building the response,
    # a few seconds after this scan captured ``now``. Allow only bounded
    # transport/clock skew; genuinely future offers remain unavailable.
    if start > now + _OFFER_CLOCK_SKEW:
        return None
    if end is not None and end < now:
        return None
    offer_id = str(offer.get("id") or "").strip()
    if not offer_id:
        raise RuntimeError("E.Leclerc France: available offer has invalid identity")
    return _OfferChoice(price, seller, offer_id)


def _offer_price(offer: dict[str, Any]) -> float | None:
    base = offer.get("basePrice")
    if not isinstance(base, dict):
        return None
    price_holder = base.get("discountPrice")
    if isinstance(price_holder, dict):
        price_holder = price_holder.get("price")
    if not isinstance(price_holder, dict):
        price_holder = base.get("price")
    if not isinstance(price_holder, dict):
        return None
    cents = price_holder.get("price")
    if not isinstance(cents, int) or isinstance(cents, bool) or cents <= 0:
        return None
    price = round(cents / 100, 2)
    return price if math.isfinite(price) and price > 0 else None


def _offer_date(
    offer: dict[str, Any],
    key: str,
    *,
    required: bool,
) -> datetime | None:
    raw = offer.get(key)
    if raw in (None, "") and not required:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise RuntimeError(f"E.Leclerc France: available offer has invalid {key}")
    try:
        return _parse_iso_datetime(raw)
    except ValueError as exc:
        raise RuntimeError(
            f"E.Leclerc France: available offer has invalid {key}"
        ) from exc


def _availability_status(offer: dict[str, Any]) -> str:
    fields = offer.get("additionalFields")
    if not isinstance(fields, list):
        raise RuntimeError("E.Leclerc France: offer has no additional fields")
    values: list[str] = []
    for field in fields:
        if not isinstance(field, dict):
            raise RuntimeError("E.Leclerc France: offer has an invalid additional field")
        if field.get("code") == "availability-status":
            value = field.get("value")
            if isinstance(value, str) and value.strip():
                values.append(value.strip())
    if len(values) != 1 or values[0] not in _KNOWN_STATUSES:
        raise RuntimeError("E.Leclerc France: invalid availability status")
    return values[0]


def _family_code(item: dict[str, Any]) -> str:
    family = item.get("family")
    if not isinstance(family, dict):
        raise RuntimeError("E.Leclerc France: product has no family")
    return _required_string(family.get("code"), "product family code")


def _item_text(item: dict[str, Any]) -> str:
    values = [str(item.get("label") or "")]
    for group in item.get("attributeGroups") or []:
        if not isinstance(group, dict):
            continue
        for attribute in group.get("attributes") or []:
            if not isinstance(attribute, dict):
                continue
            value = attribute.get("value")
            if isinstance(value, str):
                values.append(value)
            elif isinstance(value, dict):
                label = value.get("label")
                if isinstance(label, str):
                    values.append(label)
    return " ".join(values)


def _required_sku(item: dict[str, Any], label: str) -> str:
    sku = _required_string(item.get("sku"), f"{label} SKU")
    if _SKU_RE.fullmatch(sku) is None:
        raise RuntimeError(f"E.Leclerc France: invalid {label} SKU")
    return sku


def _required_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"E.Leclerc France: missing {label}")
    return value.strip()


def _validate_cache(payload: dict[str, Any], now: datetime) -> tuple[list[str], datetime]:
    rows = payload.get("rows")
    raw_timestamp = payload.get("last_imported")
    if not isinstance(rows, list) or not isinstance(raw_timestamp, str):
        raise RuntimeError("E.Leclerc France: invalid discovery cache")
    try:
        timestamp = _parse_iso_datetime(raw_timestamp)
    except ValueError as exc:
        raise RuntimeError("E.Leclerc France: invalid discovery cache timestamp") from exc
    if timestamp > now + timedelta(minutes=5):
        raise RuntimeError("E.Leclerc France: discovery cache timestamp is in the future")
    skus: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            raise RuntimeError("E.Leclerc France: invalid discovery cache row")
        skus.append(_required_sku(row, "cached"))
    if len(set(skus)) != len(skus):
        raise RuntimeError("E.Leclerc France: duplicate SKU in discovery cache")
    return sorted(skus), timestamp


def _awin_deep_link(merchant_url: str) -> str:
    query = urlencode(
        {
            "awinmid": _AWIN_ADVERTISER_ID,
            "awinaffid": _AWIN_PUBLISHER_ID,
            "ued": merchant_url,
        }
    )
    return f"{_AWIN_URL}?{query}"


def _offer_sort_key(choice: _OfferChoice) -> tuple[float, str, str]:
    return choice.price, choice.seller.casefold(), choice.offer_id


def _chunks(values: Sequence[str], size: int) -> list[list[str]]:
    return [list(values[index : index + size]) for index in range(0, len(values), size)]


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise RuntimeError("E.Leclerc France: naive timestamp is not allowed")
    return value.astimezone(timezone.utc)


def _parse_iso_datetime(value: str) -> datetime:
    # E.Leclerc emits variable-width .NET fractional seconds. Python 3.9 only
    # accepts three or six digits, so normalise to exactly six: pad short
    # fractions and truncate (never round) long ones.
    normalized = value.strip()
    match = _ISO_FRACTION_RE.search(normalized)
    if match is not None:
        fraction = match.group("fraction")[:6].ljust(6, "0")
        normalized = (
            normalized[: match.start()]
            + f".{fraction}{match.group('timezone')}"
        )
    return _utc(datetime.fromisoformat(normalized.replace("Z", "+00:00")))
