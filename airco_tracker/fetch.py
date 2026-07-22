from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import urljoin, urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .url_security import (
    HostResolver,
    normalized_https_url,
    redirect_host_allowed,
    validate_public_https_url,
)


LOG = logging.getLogger(__name__)
_DEFAULT_MIN_RESPONSE_BYTES = 10_000
_DEFAULT_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_ALLOWED_CONTENT_TYPES = frozenset(
    {
        "application/json",
        "application/ld+json",
        "application/gzip",
        "application/octet-stream",  # compressed retailer sitemaps
        "application/x-gzip",
        "application/xhtml+xml",
        "application/xml",
        "text/html",
        "text/plain",
        "text/xml",
    }
)
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
_SENSITIVE_HEADERS = frozenset({"authorization", "cookie", "proxy-authorization"})


def _package_version() -> str:
    try:
        return version("airco-tracker")
    except PackageNotFoundError:  # Running from source without install.
        return "0.0.0+dev"


@dataclass(frozen=True)
class FetchResult:
    """Bounded response metadata for clients that need non-2xx bodies.

    Retail adapters should normally use :meth:`Fetcher.get`,
    :meth:`Fetcher.get_bytes` or :meth:`Fetcher.request_json`. The result form
    exists for the AliExpress gateway, where a sanitized API error depends on
    the HTTP status and request-id header.
    """

    status_code: int
    headers: Mapping[str, str]
    content: bytes
    url: str

    @property
    def text(self) -> str:
        return _decode_text(self.content, None)

    def json(self) -> Any:
        try:
            return json.loads(self.content)
        except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Invalid JSON response from {self.url}") from exc


class Fetcher:
    """Single hardened network boundary for retailer and partner requests.

    All response bodies are streamed into a strict byte budget, redirects are
    followed manually after strict host validation, and callers declare the minimum
    body size and accepted media types appropriate to the endpoint. Ordinary
    HTML keeps the anti-bot-shell 10 KiB minimum; compact JSON/XML endpoints
    deliberately opt into a smaller minimum.
    """

    def __init__(
        self,
        timeout: int = 25,
        *,
        max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
        max_redirects: int = 3,
        sleep: Callable[[float], None] | None = None,
        resolver: HostResolver | None = None,
    ) -> None:
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(float(timeout))
            or timeout <= 0
            or timeout > 120
            or isinstance(max_response_bytes, bool)
            or not isinstance(max_response_bytes, int)
            or max_response_bytes <= 0
            or max_response_bytes > 100 * 1024 * 1024
            or isinstance(max_redirects, bool)
            or not isinstance(max_redirects, int)
            or max_redirects < 0
        ):
            raise ValueError("Invalid fetcher limits")
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes
        self.max_redirects = max_redirects
        self._sleep = sleep or time.sleep
        self._resolver = resolver
        self.session = requests.Session()
        # GET is idempotent and receives the ordinary urllib3 retry policy.
        # POST is never included here: individual read-only search/API calls
        # must opt into the explicit, bounded retry flag below.
        retry = Retry(
            total=2,
            connect=2,
            read=2,
            backoff_factor=1.0,
            status_forcelist=tuple(_RETRYABLE_STATUSES),
            allowed_methods=("GET",),
            respect_retry_after_header=True,
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 Chrome/124 Safari/537.36 "
                    f"AircoTracker/{_package_version()}"
                ),
                "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.7",
            }
        )

    @property
    def user_agent(self) -> str:
        """Return the shared public user agent without exposing the session."""

        return str(self.session.headers.get("User-Agent", ""))

    def get(
        self,
        url: str,
        *,
        allowed_redirect_hosts: tuple[str, ...] = (),
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
        minimum_response_bytes: int = _DEFAULT_MIN_RESPONSE_BYTES,
        maximum_response_bytes: int | None = None,
        allowed_content_types: Iterable[str] | None = None,
    ) -> str:
        """Fetch bounded text; HTML defaults to the 10 KiB shell guard."""

        return self.request_text(
            "GET",
            url,
            allowed_redirect_hosts=allowed_redirect_hosts,
            headers=headers,
            timeout=timeout,
            minimum_response_bytes=minimum_response_bytes,
            maximum_response_bytes=maximum_response_bytes,
            allowed_content_types=allowed_content_types,
        )

    def get_bytes(
        self,
        url: str,
        *,
        allowed_redirect_hosts: tuple[str, ...] = (),
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, object] | Sequence[tuple[str, object]] | None = None,
        timeout: float | None = None,
        minimum_response_bytes: int = 1,
        maximum_response_bytes: int | None = None,
        allowed_content_types: Iterable[str] | None = None,
    ) -> bytes:
        return self.request(
            "GET",
            url,
            allowed_redirect_hosts=allowed_redirect_hosts,
            headers=headers,
            params=params,
            timeout=timeout,
            minimum_response_bytes=minimum_response_bytes,
            maximum_response_bytes=maximum_response_bytes,
            allowed_content_types=allowed_content_types,
        ).content

    def request_text(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> str:
        result = self.request(method, url, **kwargs)
        return _decode_text(result.content, _charset(result.headers))

    def request_json(
        self,
        method: str,
        url: str,
        *,
        minimum_response_bytes: int = 1,
        maximum_response_bytes: int | None = None,
        allowed_content_types: Iterable[str] = (
            "application/json",
            "application/ld+json",
        ),
        **kwargs: Any,
    ) -> Any:
        result = self.request(
            method,
            url,
            minimum_response_bytes=minimum_response_bytes,
            maximum_response_bytes=maximum_response_bytes,
            allowed_content_types=allowed_content_types,
            **kwargs,
        )
        return result.json()

    def request(
        self,
        method: str,
        url: str,
        *,
        allowed_redirect_hosts: tuple[str, ...] = (),
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, object] | Sequence[tuple[str, object]] | None = None,
        json_body: Any = None,
        form_data: Mapping[str, object] | Sequence[tuple[str, object]] | None = None,
        timeout: float | None = None,
        minimum_response_bytes: int = _DEFAULT_MIN_RESPONSE_BYTES,
        maximum_response_bytes: int | None = None,
        allowed_content_types: Iterable[str] | None = None,
        retry_read_only_post: bool = False,
        raise_for_status: bool = True,
    ) -> FetchResult:
        """Perform one bounded GET or POST through the hardened transport.

        ``retry_read_only_post`` is intentionally opt-in. It may be used only
        for logically read-only catalogue/search calls (including signed
        affiliate product queries), never for writes, payments or mutations.
        """

        if not isinstance(method, str):
            raise ValueError("Fetcher method must be text")
        request_method = method.strip().upper()
        if request_method not in {"GET", "POST"}:
            raise ValueError("Fetcher supports only GET and POST")
        if retry_read_only_post and request_method != "POST":
            raise ValueError("Read-only POST retries are valid only for POST")
        request_timeout = _positive_timeout(timeout, self.timeout)
        maximum = self.max_response_bytes if maximum_response_bytes is None else maximum_response_bytes
        minimum, maximum = _body_limits(minimum_response_bytes, maximum)
        content_types = _content_types(allowed_content_types)
        current = normalized_https_url(url, max_length=2_000)
        origin_host = (urlsplit(current).hostname or "").lower()
        request_headers = _headers(headers)
        attempts = 3 if retry_read_only_post else 1

        for attempt in range(attempts):
            try:
                result = self._request_once(
                    request_method,
                    current,
                    origin_host=origin_host,
                    allowed_redirect_hosts=allowed_redirect_hosts,
                    headers=request_headers,
                    params=params,
                    json_body=json_body,
                    form_data=form_data,
                    timeout=request_timeout,
                    # Retryable API gateways often return an empty 429/503
                    # body. Read that bounded status first; enforce the
                    # caller's minimum only after a successful final status.
                    minimum=0 if retry_read_only_post else minimum,
                    maximum=maximum,
                    content_types=content_types,
                    raise_for_status=raise_for_status and not retry_read_only_post,
                )
            except (requests.Timeout, requests.ConnectionError):
                if attempt + 1 >= attempts:
                    raise
                self._sleep(0.25 * (2**attempt))
                continue
            if (
                retry_read_only_post
                and result.status_code in _RETRYABLE_STATUSES
                and attempt + 1 < attempts
            ):
                self._sleep(_retry_delay(result.headers, attempt))
                continue
            if retry_read_only_post and raise_for_status and not 200 <= result.status_code < 300:
                _raise_result_status(result)
            if 200 <= result.status_code < 300 and len(result.content) < minimum:
                raise RuntimeError(f"Suspiciously small response from {result.url}")
            return result
        raise RuntimeError(f"Request attempts exhausted for {url}")

    def _request_once(
        self,
        method: str,
        url: str,
        *,
        origin_host: str,
        allowed_redirect_hosts: tuple[str, ...],
        headers: dict[str, str],
        params: Mapping[str, object] | Sequence[tuple[str, object]] | None,
        json_body: Any,
        form_data: Mapping[str, object] | Sequence[tuple[str, object]] | None,
        timeout: float,
        minimum: int,
        maximum: int,
        content_types: frozenset[str],
        raise_for_status: bool,
    ) -> FetchResult:
        current = url
        current_method = method
        current_headers = dict(headers)
        current_params = params
        current_json = json_body
        current_form = form_data
        previous_host = origin_host
        for redirect_count in range(self.max_redirects + 1):
            # Resolve immediately before every connection attempt. This is
            # deliberately repeated for POST retries and each redirect hop so
            # a hostname that changes from a public to a private answer cannot
            # reuse an earlier security decision.
            current = validate_public_https_url(
                current,
                resolver=self._resolver,
                max_length=2_000,
            )
            LOG.info("Fetching %s", current)
            call = self.session.get if current_method == "GET" else self.session.post
            kwargs: dict[str, Any] = {
                "headers": current_headers,
                "timeout": timeout,
                "stream": True,
                "allow_redirects": False,
            }
            if current_params is not None:
                kwargs["params"] = current_params
            if current_method == "POST":
                if current_json is not None and current_form is not None:
                    raise ValueError("Pass either json_body or form_data, not both")
                if current_json is not None:
                    kwargs["json"] = current_json
                if current_form is not None:
                    kwargs["data"] = current_form
            response = call(current, **kwargs)
            try:
                status = getattr(response, "status_code", None)
                if isinstance(status, bool) or not isinstance(status, int):
                    raise RuntimeError(f"Invalid HTTP status from {current}")
                if status in _REDIRECT_STATUSES:
                    if redirect_count >= self.max_redirects:
                        raise RuntimeError(f"Too many redirects while fetching {url}")
                    location = response.headers.get("Location", "")
                    destination = normalized_https_url(
                        urljoin(current, location), max_length=2_000
                    )
                    destination_host = (urlsplit(destination).hostname or "").lower()
                    if not redirect_host_allowed(
                        origin_host,
                        destination_host,
                        allowed_redirect_hosts,
                    ):
                        raise RuntimeError(
                            f"Refusing cross-site redirect from {origin_host} "
                            f"to {destination_host}"
                        )
                    if destination_host != previous_host and any(
                        key.casefold() in _SENSITIVE_HEADERS for key in current_headers
                    ):
                        raise RuntimeError(
                            "Refusing to forward sensitive headers across hosts"
                        )
                    # GET redirects remain GET. 307/308 preserve POST bodies;
                    # 301/302/303 are converted to GET and entity headers are
                    # removed, matching common user-agent semantics.
                    if current_method == "POST" and status in {301, 302, 303}:
                        current_method = "GET"
                        current_json = None
                        current_form = None
                        current_headers = {
                            key: value
                            for key, value in current_headers.items()
                            if key.casefold() not in {"content-type", "content-length"}
                        }
                    current = destination
                    current_params = None
                    previous_host = destination_host
                    continue

                if raise_for_status:
                    response.raise_for_status()
                # A caller that explicitly handles non-success statuses may
                # need only the status/request-id; gateways sometimes omit a
                # media type on their empty error body. It remains byte-capped.
                if 200 <= status < 300 or raise_for_status:
                    _validate_content_type(response, current, content_types)
                content = _read_bounded_bytes(response, current, minimum, maximum)
                return FetchResult(
                    status,
                    requests.structures.CaseInsensitiveDict(response.headers),
                    content,
                    current,
                )
            finally:
                response.close()
        raise RuntimeError(f"Too many redirects while fetching {url}")


def _validate_content_type(
    response: requests.Response,
    url: str,
    allowed: frozenset[str],
) -> None:
    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    if content_type not in allowed:
        raise RuntimeError(
            f"Unexpected Content-Type {content_type or '<missing>'} from {url}"
        )


def _read_bounded_bytes(
    response: requests.Response,
    url: str,
    minimum: int,
    maximum: int,
) -> bytes:
    raw_length = response.headers.get("Content-Length", "").strip()
    if raw_length:
        try:
            content_length = int(raw_length)
        except ValueError as exc:
            raise RuntimeError(f"Invalid Content-Length from {url}") from exc
        if content_length < 0 or content_length > maximum:
            raise RuntimeError(f"Response from {url} exceeds the {maximum}-byte limit")

    chunks: list[bytes] = []
    size = 0
    for chunk in response.iter_content(chunk_size=64 * 1024):
        if not chunk:
            continue
        size += len(chunk)
        if size > maximum:
            raise RuntimeError(f"Response from {url} exceeds the {maximum}-byte limit")
        chunks.append(chunk)
    if size < minimum:
        raise RuntimeError(f"Suspiciously small response from {url}")
    return b"".join(chunks)


def _decode_text(body: bytes, encoding: str | None) -> str:
    try:
        return body.decode(encoding or "utf-8")
    except (LookupError, UnicodeDecodeError):
        return body.decode("utf-8", errors="replace")


def _charset(headers: Mapping[str, str]) -> str | None:
    content_type = str(headers.get("Content-Type", ""))
    for section in content_type.split(";")[1:]:
        key, separator, value = section.strip().partition("=")
        if separator and key.casefold() == "charset" and value.strip():
            return value.strip().strip('"')
    return None


def _body_limits(minimum: int, maximum: int) -> tuple[int, int]:
    if (
        isinstance(minimum, bool)
        or not isinstance(minimum, int)
        or isinstance(maximum, bool)
        or not isinstance(maximum, int)
        or minimum < 0
        or maximum <= 0
        or minimum > maximum
        or maximum > 100 * 1024 * 1024
    ):
        raise ValueError("Invalid response-size limits")
    return minimum, maximum


def _positive_timeout(value: float | None, default: float) -> float:
    candidate = float(default if value is None else value)
    if not math.isfinite(candidate) or candidate <= 0 or candidate > 120:
        raise ValueError("Invalid request timeout")
    return candidate


def _headers(values: Mapping[str, str] | None) -> dict[str, str]:
    if values is None:
        return {}
    result: dict[str, str] = {}
    for key, value in values.items():
        if (
            not isinstance(key, str)
            or not key.strip()
            or not isinstance(value, str)
            or "\r" in key
            or "\n" in key
            or "\r" in value
            or "\n" in value
        ):
            raise ValueError("Invalid request header")
        result[key] = value
    return result


def _content_types(values: Iterable[str] | None) -> frozenset[str]:
    if values is None:
        return _ALLOWED_CONTENT_TYPES
    result = frozenset(str(value).strip().casefold() for value in values)
    if not result or any(not value or "/" not in value for value in result):
        raise ValueError("Invalid content-type allow-list")
    return result


def _retry_delay(headers: Mapping[str, str], attempt: int) -> float:
    raw = str(headers.get("Retry-After", "")).strip()
    if raw.isdigit():
        return min(float(raw), 5.0)
    return 0.25 * (2**attempt)


def _raise_result_status(result: FetchResult) -> None:
    response = requests.Response()
    response.status_code = result.status_code
    response.url = result.url
    response.headers.update(result.headers)
    response._content = result.content
    response.raise_for_status()
