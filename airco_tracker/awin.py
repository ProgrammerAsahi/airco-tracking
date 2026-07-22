from __future__ import annotations

import logging
import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit

from .partner_feed_store import PartnerFeedCache


LOG = logging.getLogger(__name__)

_AWIN_API_ROOT = "https://api.awin.com"
_ID_RE = re.compile(r"^[1-9][0-9]{0,15}$")
_MAX_DESTINATIONS = 100
_LINK_LOCK = threading.RLock()


class AwinLinkBuilderClient:
    """Generate and cache official Awin tracking links for canonical URLs.

    The bearer token is sent only in the Authorization header and is never
    logged. A valid cache suppresses repeated calls from the ten-minute stock
    scanner; one batch request can cover at most 100 canonical URLs, matching
    Awin's documented batch limit.
    """

    def __init__(
        self,
        *,
        session: Any | None = None,
        fetcher: Any | None = None,
        cache: PartnerFeedCache,
        cache_namespace: str,
        cache_key: str,
        publisher_id: str | int,
        advertiser_id: str | int,
        bearer_token: str,
        ttl: timedelta = timedelta(days=1),
        timeout: int = 10,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if ttl <= timedelta(0) or timeout <= 0:
            raise ValueError("Invalid Awin Link Builder configuration")
        if (session is None) == (fetcher is None):
            raise ValueError("Pass exactly one Awin HTTP transport")
        self._session = session
        self._fetcher = fetcher
        self._cache = cache
        self._cache_namespace = cache_namespace
        self._cache_key = cache_key
        self._publisher_id = _awin_id(publisher_id, "publisher")
        self._advertiser_id = _awin_id(advertiser_id, "advertiser")
        self._bearer_token = _secret_token(bearer_token)
        self._ttl = ttl
        self._timeout = timeout
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._batch_url = (
            f"{_AWIN_API_ROOT}/publishers/{self._publisher_id}/"
            "linkbuilder/generate-batch"
        )

    def links_for(
        self,
        destination_urls: Iterable[str],
        *,
        force: bool = False,
    ) -> dict[str, str]:
        destinations = _destinations(destination_urls)
        if not destinations:
            return {}
        with _LINK_LOCK:
            return self._links_for_locked(destinations, force=force)

    def _links_for_locked(
        self,
        destinations: tuple[str, ...],
        *,
        force: bool,
    ) -> dict[str, str]:
        now = _as_utc(self._now())
        cached: dict[str, str] = {}
        cache_document: dict[str, Any] | None = None
        try:
            cache_document = self._cache.load(
                self._cache_namespace, self._cache_key
            )
            if cache_document is not None:
                cached = _links_from_cache(
                    cache_document,
                    publisher_id=self._publisher_id,
                    advertiser_id=self._advertiser_id,
                )
        except Exception:
            LOG.warning("Awin Link Builder cache is invalid; attempting a safe refresh")
            cache_document = None
            cached = {}

        if (
            not force
            and cache_document is not None
            and _cache_destinations(cache_document) == set(destinations)
            and now - _cache_imported_at(cache_document) <= self._ttl
        ):
            return {url: cached[url] for url in destinations if url in cached}

        try:
            generated = self._generate(destinations)
        except Exception:
            LOG.warning("Awin Link Builder request failed; using validated cached links")
            return {url: cached[url] for url in destinations if url in cached}

        payload = {
            "version": 2,
            "last_imported": now.isoformat(),
            "requested_urls": list(destinations),
            "rows": [
                {"canonical_url": url, "affiliate_url": generated[url]}
                for url in destinations
                if url in generated
            ],
            # This is the number requested, not the number that Awin accepted.
            # It lets the cache remember partial/negative results for the TTL.
            "source_row_count": len(destinations),
        }
        try:
            self._cache.save(self._cache_namespace, self._cache_key, payload)
        except Exception:
            LOG.warning("Awin Link Builder cache could not be saved")
        return generated

    def _generate(self, destinations: tuple[str, ...]) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._bearer_token}",
            "Content-Type": "application/json",
        }
        body = {
            "requests": [
                {
                    "advertiserId": int(self._advertiser_id),
                    "destinationUrl": url,
                }
                for url in destinations
            ]
        }
        if self._fetcher is not None:
            # Link generation is deterministic for the same destinations but
            # is not required for stock truth. Keep it single-attempt rather
            # than treating every POST as retryable.
            payload = self._fetcher.request_json(
                "POST",
                self._batch_url,
                headers=headers,
                json_body=body,
                timeout=self._timeout,
                minimum_response_bytes=1,
                maximum_response_bytes=2 * 1024 * 1024,
            )
        else:
            # Backwards-compatible injected transport used only by isolated
            # client tests. Production construction always supplies Fetcher.
            response = self._session.post(
                self._batch_url,
                headers=headers,
                json=body,
                timeout=self._timeout,
            )
            response.raise_for_status()
            try:
                payload = response.json()
            except (TypeError, ValueError) as exc:
                raise RuntimeError("Awin Link Builder returned invalid JSON") from exc
        if not isinstance(payload, dict) or not isinstance(
            payload.get("responses"), list
        ):
            raise RuntimeError("Awin Link Builder returned an invalid response")

        expected = set(destinations)
        seen: set[str] = set()
        links: dict[str, str] = {}
        for entry in payload["responses"]:
            if not isinstance(entry, dict) or not isinstance(entry.get("body"), dict):
                raise RuntimeError("Awin Link Builder returned an invalid item")
            body = entry["body"]
            # Awin currently nests the echoed request inside ``body`` while
            # its public batch example shows it at item level. Accept exactly
            # those two shapes and keep validating the echoed advertiser and
            # destination before trusting the returned tracking link.
            request = entry.get("request", body.get("request"))
            if not isinstance(request, dict):
                raise RuntimeError("Awin Link Builder returned an invalid item")
            request_advertiser = request.get("advertiserId")
            if (
                isinstance(request_advertiser, bool)
                or str(request_advertiser) != self._advertiser_id
            ):
                raise RuntimeError("Awin Link Builder returned an unexpected item")
            destination = canonicalise_product_url(request.get("destinationUrl"))
            if destination not in expected or destination in seen:
                raise RuntimeError("Awin Link Builder returned an unexpected item")
            seen.add(destination)
            status = entry.get("status")
            if isinstance(status, bool) or not isinstance(status, int):
                raise RuntimeError("Awin Link Builder returned an invalid status")
            if status != 200:
                continue
            links[destination] = _validated_awin_link(
                body.get("url"),
                destination_url=destination,
                publisher_id=self._publisher_id,
                advertiser_id=self._advertiser_id,
            )
        return links


def canonicalise_product_url(value: Any) -> str:
    """Normalise a canonical merchant URL without redirects or fuzzy matching."""

    if not isinstance(value, str) or not value.strip() or len(value) > 2_000:
        raise ValueError("Invalid product URL")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("Invalid product URL")
    parsed = urlsplit(value.strip())
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("Invalid product URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Invalid product URL")
    host = parsed.hostname.lower()
    port = parsed.port
    if port not in (None, 443):
        host = f"{host}:{port}"
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return urlunsplit(("https", host, path, parsed.query, ""))


def _validated_awin_link(
    value: Any,
    *,
    destination_url: str,
    publisher_id: str,
    advertiser_id: str,
) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 4_096:
        raise RuntimeError("Awin Link Builder returned an invalid URL")
    candidate = value.strip()
    if any(ord(char) < 32 or ord(char) == 127 for char in candidate):
        raise RuntimeError("Awin Link Builder returned an invalid URL")
    parsed = urlsplit(candidate)
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").lower() not in {"awin1.com", "www.awin1.com"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
        or parsed.path != "/cread.php"
        or parsed.fragment
    ):
        raise RuntimeError("Awin Link Builder returned an invalid URL")
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        query.get("awinmid") != [advertiser_id]
        or query.get("awinaffid") != [publisher_id]
        or query.get("ued") != [destination_url]
    ):
        raise RuntimeError("Awin Link Builder returned an unexpected URL")

    # No CMP is deployed yet. Awin treats an omitted value as consent, so the
    # privacy-safe choice is to explicitly suppress cookie/click-ID tracking.
    pairs = [(key, item) for key, item in parse_qsl(parsed.query, keep_blank_values=True) if key != "cons"]
    pairs.append(("cons", "0"))
    return urlunsplit(("https", parsed.netloc.lower(), parsed.path, urlencode(pairs), ""))


def _destinations(values: Iterable[str]) -> tuple[str, ...]:
    ordered: list[str] = []
    seen: set[str] = set()
    for value in values:
        destination = canonicalise_product_url(value)
        if destination not in seen:
            seen.add(destination)
            ordered.append(destination)
    if len(ordered) > _MAX_DESTINATIONS:
        raise ValueError("Awin Link Builder batch contains too many destinations")
    return tuple(ordered)


def _links_from_cache(
    value: dict[str, Any],
    *,
    publisher_id: str,
    advertiser_id: str,
) -> dict[str, str]:
    _cache_imported_at(value)
    requested = _cache_destinations(value)
    rows = value.get("rows")
    if not isinstance(rows, list) or len(rows) > _MAX_DESTINATIONS:
        raise RuntimeError("Invalid Awin Link Builder cache")
    links: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise RuntimeError("Invalid Awin Link Builder cache")
        destination = canonicalise_product_url(row.get("canonical_url"))
        if destination not in requested or destination in links:
            raise RuntimeError("Invalid Awin Link Builder cache")
        links[destination] = _validated_awin_link(
            row.get("affiliate_url"),
            destination_url=destination,
            publisher_id=publisher_id,
            advertiser_id=advertiser_id,
        )
    return links


def _cache_destinations(value: dict[str, Any]) -> set[str]:
    requested = value.get("requested_urls")
    if not isinstance(requested, list) or len(requested) > _MAX_DESTINATIONS:
        raise RuntimeError("Invalid Awin Link Builder cache")
    destinations = [canonicalise_product_url(item) for item in requested]
    if len(set(destinations)) != len(destinations):
        raise RuntimeError("Invalid Awin Link Builder cache")
    return set(destinations)


def _cache_imported_at(value: dict[str, Any]) -> datetime:
    raw = value.get("last_imported")
    if not isinstance(raw, str):
        raise RuntimeError("Invalid Awin Link Builder cache timestamp")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RuntimeError("Invalid Awin Link Builder cache timestamp") from exc
    if parsed.tzinfo is None:
        raise RuntimeError("Invalid Awin Link Builder cache timestamp")
    return parsed.astimezone(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("Awin Link Builder clock must be timezone-aware")
    return value.astimezone(timezone.utc)


def _awin_id(value: str | int, label: str) -> str:
    candidate = str(value)
    if _ID_RE.fullmatch(candidate) is None:
        raise ValueError(f"Invalid Awin {label} ID")
    return candidate


def _secret_token(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 4_096:
        raise ValueError("Invalid Awin bearer token")
    return value.strip()
