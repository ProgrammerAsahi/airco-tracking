from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
import re
import time
from collections.abc import Callable, Mapping
from typing import Any

import requests


LOG = logging.getLogger(__name__)

PRODUCTION_ENDPOINT = "https://api-sg.aliexpress.com/sync"
PRODUCT_QUERY_METHOD = "aliexpress.affiliate.product.query"
SKU_DETAIL_METHOD = "aliexpress.affiliate.product.sku.detail.get"
PARTNER_ID = "iop-sdk-python-20220609"

_ALLOWED_METHODS = frozenset({PRODUCT_QUERY_METHOD, SKU_DETAIL_METHOD})
_RETRYABLE_HTTP_STATUSES = frozenset({429, 502, 503, 504})
_RETRY_DELAYS_SECONDS = (0.25, 0.5)
_RESERVED_PARAMETERS = frozenset(
    {
        "app_key",
        "format",
        "method",
        "partner_id",
        "session",
        "sign",
        "sign_method",
        "simplify",
        "timestamp",
    }
)
_PARAMETER_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.]{0,127}$")
_LOG_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,96}$")
_MIN_TIMESTAMP_MS = 946_684_800_000  # 2000-01-01T00:00:00Z
_MAX_TIMESTAMP_MS = 9_999_999_999_999
_SKU_PRODUCT_ID_RE = re.compile(r"^[0-9]{5,32}$")
_SKU_ID_RE = re.compile(r"^[0-9]{1,32}$")
_SHIP_TO_COUNTRY_RE = re.compile(r"^[A-Z]{2}$")
_TARGET_CURRENCIES = frozenset(
    {
        "AUD",
        "BRL",
        "CAD",
        "CLP",
        "EUR",
        "GBP",
        "IDR",
        "ILS",
        "INR",
        "JPY",
        "KRW",
        "MXN",
        "SEK",
        "THB",
        "TRY",
        "UAH",
        "USD",
        "VND",
    }
)
_TARGET_LANGUAGES = frozenset(
    {
        "AR",
        "CL",
        "DE",
        "EN",
        "ES",
        "FR",
        "HE",
        "ID",
        "IN",
        "IT",
        "JA",
        "KO",
        "MX",
        "NL",
        "PL",
        "PT",
        "TH",
        "TR",
        "VI",
    }
)


class AliExpressError(RuntimeError):
    """Base class for sanitized AliExpress failures."""


class AliExpressTransportError(AliExpressError):
    """The API could not be reached."""


class AliExpressHttpError(AliExpressError):
    """The API returned a non-success HTTP status."""

    def __init__(
        self,
        method: str,
        status_code: int,
        *,
        request_id: str | None = None,
    ) -> None:
        self.status_code = status_code
        self.request_id = _safe_log_token(request_id)
        suffix = f", request_id={self.request_id}" if self.request_id else ""
        super().__init__(
            f"AliExpress API HTTP failure for {method}: status={status_code}{suffix}"
        )


class AliExpressApiError(AliExpressError):
    """AliExpress accepted the HTTP request but rejected the API operation."""

    def __init__(
        self,
        method: str,
        *,
        code: Any = None,
        sub_code: Any = None,
        request_id: Any = None,
    ) -> None:
        self.code = _safe_log_token(code) or "unknown"
        self.sub_code = _safe_log_token(sub_code)
        self.request_id = _safe_log_token(request_id)
        details = [f"code={self.code}"]
        if self.sub_code:
            details.append(f"sub_code={self.sub_code}")
        if self.request_id:
            details.append(f"request_id={self.request_id}")
        super().__init__(
            f"AliExpress API rejected {method}: " + ", ".join(details)
        )


class AliExpressResponseError(AliExpressError):
    """AliExpress returned an invalid or unexpected response document."""


class AliExpressClient:
    """Minimal, fail-closed client for approved AliExpress Affiliate APIs.

    Credentials and request parameters are deliberately never logged.  The
    client accepts only the two read-only methods used by Airco Tracker and
    posts signed form data to the fixed AliExpress production gateway.
    """

    def __init__(
        self,
        *,
        session: Any | None = None,
        fetcher: Any | None = None,
        app_key: str,
        app_secret: str,
        timeout: float = 10,
        now_ms: Callable[[], int] | None = None,
        sleep: Callable[[float], None] | None = None,
        max_response_bytes: int = 5_000_000,
    ) -> None:
        if (session is None) == (fetcher is None):
            raise ValueError("Pass exactly one AliExpress HTTP transport")
        self._session = session
        self._fetcher = fetcher
        self._app_key = _credential(app_key, "app key", max_length=128)
        self._app_secret = _credential(app_secret, "app secret", max_length=4_096)
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool):
            raise ValueError("AliExpress timeout must be a positive number")
        if not math.isfinite(float(timeout)) or timeout <= 0 or timeout > 120:
            raise ValueError("AliExpress timeout must be between 0 and 120 seconds")
        if (
            isinstance(max_response_bytes, bool)
            or not isinstance(max_response_bytes, int)
            or max_response_bytes <= 0
            or max_response_bytes > 50_000_000
        ):
            raise ValueError("Invalid AliExpress response-size limit")
        if sleep is not None and not callable(sleep):
            raise ValueError("AliExpress sleep hook must be callable")
        self._timeout = float(timeout)
        self._now_ms = now_ms or (lambda: int(time.time() * 1_000))
        self._sleep = sleep or time.sleep
        self._max_response_bytes = max_response_bytes

    def product_query(self, params: Mapping[str, object]) -> dict[str, Any]:
        return self._execute(PRODUCT_QUERY_METHOD, params)

    def product_sku_detail(self, params: Mapping[str, object]) -> dict[str, Any]:
        return self._execute(SKU_DETAIL_METHOD, params)

    def _execute(
        self,
        method: str,
        params: Mapping[str, object],
    ) -> dict[str, Any]:
        if method not in _ALLOWED_METHODS:
            raise ValueError("Unsupported AliExpress API method")
        signed = self._signed_parameters(method, params)
        response, content = self._post_with_retries(method, signed)
        try:
            status_code = getattr(response, "status_code", None)
            if isinstance(status_code, bool) or not isinstance(status_code, int):
                raise AliExpressResponseError(
                    f"AliExpress API returned an invalid HTTP status for {method}"
                )
            request_id = _header_request_id(getattr(response, "headers", None))
            if not 200 <= status_code < 300:
                LOG.warning(
                    "AliExpress HTTP failure method=%s status=%s request_id=%s",
                    method,
                    status_code,
                    request_id or "unknown",
                )
                raise AliExpressHttpError(
                    method,
                    status_code,
                    request_id=request_id,
                )

            if content is None:
                raise AliExpressResponseError(
                    f"AliExpress API returned an invalid body for {method}"
                )
            try:
                payload = json.loads(content)
            except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
                raise AliExpressResponseError(
                    f"AliExpress API returned invalid JSON for {method}"
                ) from None
            if not isinstance(payload, dict):
                raise AliExpressResponseError(
                    f"AliExpress API returned an invalid document for {method}"
                )
            return _unwrap_response(method, payload)
        finally:
            _close_response(response)

    def _post_with_retries(
        self,
        method: str,
        signed: Mapping[str, str],
    ) -> tuple[Any, bytes | None]:
        """POST a read-only request with two short, bounded retries."""

        if self._fetcher is not None:
            try:
                result = self._fetcher.request(
                    "POST",
                    PRODUCTION_ENDPOINT,
                    headers={
                        "Accept": "application/json",
                        "Content-Type": (
                            "application/x-www-form-urlencoded; charset=utf-8"
                        ),
                    },
                    form_data=signed,
                    timeout=self._timeout,
                    # Error responses may legitimately have no body; success
                    # is still required to contain valid JSON below.
                    minimum_response_bytes=0,
                    maximum_response_bytes=self._max_response_bytes,
                    # The Open Platform gateway has returned both JSON and
                    # text/plain media types for JSON payloads. The body is
                    # still strictly parsed as JSON immediately afterwards.
                    allowed_content_types=(
                        "application/json",
                        "text/json",
                        "text/plain",
                    ),
                    # Both approved Affiliate API methods are signed catalogue
                    # reads. This explicit opt-in is the only reason their
                    # POST transport is retried.
                    retry_read_only_post=True,
                    raise_for_status=False,
                )
            except (requests.Timeout, requests.ConnectionError, requests.RequestException):
                LOG.warning("AliExpress transport failure method=%s", method)
                raise AliExpressTransportError(
                    f"AliExpress API transport failure for {method}"
                ) from None
            content = (
                result.content if 200 <= result.status_code < 300 else None
            )
            return result, content

        for attempt in range(len(_RETRY_DELAYS_SECONDS) + 1):
            try:
                response = self._session.post(
                    PRODUCTION_ENDPOINT,
                    headers={
                        "Accept": "application/json",
                        "Content-Type": (
                            "application/x-www-form-urlencoded; charset=utf-8"
                        ),
                    },
                    data=signed,
                    stream=True,
                    timeout=self._timeout,
                )
            except (requests.Timeout, requests.ConnectionError):
                if attempt < len(_RETRY_DELAYS_SECONDS):
                    LOG.warning(
                        "AliExpress transient transport failure method=%s retry=%s",
                        method,
                        attempt + 1,
                    )
                    self._sleep(_RETRY_DELAYS_SECONDS[attempt])
                    continue
                LOG.warning("AliExpress transport failure method=%s", method)
                raise AliExpressTransportError(
                    f"AliExpress API transport failure for {method}"
                ) from None
            except requests.RequestException:
                LOG.warning("AliExpress transport failure method=%s", method)
                raise AliExpressTransportError(
                    f"AliExpress API transport failure for {method}"
                ) from None

            status_code = getattr(response, "status_code", None)
            if (
                isinstance(status_code, int)
                and not isinstance(status_code, bool)
                and status_code in _RETRYABLE_HTTP_STATUSES
                and attempt < len(_RETRY_DELAYS_SECONDS)
            ):
                request_id = _header_request_id(getattr(response, "headers", None))
                LOG.warning(
                    "AliExpress transient HTTP failure method=%s status=%s "
                    "retry=%s request_id=%s",
                    method,
                    status_code,
                    attempt + 1,
                    request_id or "unknown",
                )
                _close_response(response)
                self._sleep(_RETRY_DELAYS_SECONDS[attempt])
                continue
            content: bytes | None = None
            if (
                isinstance(status_code, int)
                and not isinstance(status_code, bool)
                and 200 <= status_code < 300
            ):
                content_length = _content_length(getattr(response, "headers", None))
                if (
                    content_length is not None
                    and content_length > self._max_response_bytes
                ):
                    _close_response(response)
                    raise AliExpressResponseError(
                        f"AliExpress API response exceeded the size limit for {method}"
                    )
                try:
                    content = _bounded_response_content(
                        response, self._max_response_bytes, method
                    )
                except (requests.Timeout, requests.ConnectionError):
                    _close_response(response)
                    if attempt < len(_RETRY_DELAYS_SECONDS):
                        LOG.warning(
                            "AliExpress transient transport failure method=%s retry=%s",
                            method,
                            attempt + 1,
                        )
                        self._sleep(_RETRY_DELAYS_SECONDS[attempt])
                        continue
                    LOG.warning("AliExpress transport failure method=%s", method)
                    raise AliExpressTransportError(
                        f"AliExpress API transport failure for {method}"
                    ) from None
                except requests.RequestException:
                    _close_response(response)
                    LOG.warning("AliExpress transport failure method=%s", method)
                    raise AliExpressTransportError(
                        f"AliExpress API transport failure for {method}"
                    ) from None
                except Exception:
                    _close_response(response)
                    raise
            return response, content

        raise AssertionError("AliExpress retry loop exhausted unexpectedly")

    def _signed_parameters(
        self,
        method: str,
        params: Mapping[str, object],
    ) -> dict[str, str]:
        business = _business_parameters(params)
        _validate_business_parameters(method, business)
        try:
            timestamp = int(self._now_ms())
        except (TypeError, ValueError, OverflowError):
            raise ValueError("Invalid AliExpress timestamp") from None
        if not _MIN_TIMESTAMP_MS <= timestamp <= _MAX_TIMESTAMP_MS:
            raise ValueError("Invalid AliExpress timestamp")
        signed = {
            "app_key": self._app_key,
            "format": "json",
            "method": method,
            "partner_id": PARTNER_ID,
            "sign_method": "sha256",
            "simplify": "false",
            "timestamp": str(timestamp),
            **business,
        }
        signed["sign"] = _sign_parameters(self._app_secret, signed)
        return signed


def _sign_parameters(secret: str, params: Mapping[str, str]) -> str:
    """Return the uppercase IOP HMAC-SHA256 signature.

    Values are signed before form encoding, matching the official Python IOP
    SDK.  ``sign`` itself must never be included in ``params``.
    """

    if "sign" in params:
        raise ValueError("AliExpress sign parameter cannot sign itself")
    canonical = "".join(f"{key}{params[key]}" for key in sorted(params))
    return hmac.new(
        secret.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest().upper()


def _business_parameters(params: Mapping[str, object]) -> dict[str, str]:
    if not isinstance(params, Mapping):
        raise ValueError("AliExpress business parameters must be a mapping")
    normalised: dict[str, str] = {}
    for key, value in params.items():
        if not isinstance(key, str) or _PARAMETER_NAME_RE.fullmatch(key) is None:
            raise ValueError("Invalid AliExpress parameter name")
        if key in _RESERVED_PARAMETERS:
            raise ValueError("AliExpress business parameters contain a reserved name")
        normalised[key] = _parameter_value(value)
    return normalised


def _parameter_value(value: object) -> str:
    if isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, int):
        text = str(value)
    elif isinstance(value, str):
        text = value
    else:
        raise ValueError(
            "AliExpress parameter values must be strings, integers, or booleans"
        )
    if len(text) > 20_000 or any(ord(char) < 32 or ord(char) == 127 for char in text):
        raise ValueError("Invalid AliExpress parameter value")
    return text


def _unwrap_response(method: str, payload: dict[str, Any]) -> dict[str, Any]:
    error = payload.get("error_response")
    if error is not None:
        if not isinstance(error, dict):
            raise AliExpressResponseError(
                f"AliExpress API returned an invalid error for {method}"
            )
        _raise_api_error(method, error)

    direct_code = payload.get("code")
    if direct_code is not None and str(direct_code) not in {"0", "200"}:
        _raise_api_error(method, payload)

    response_key = method.replace(".", "_") + "_response"
    if method == SKU_DETAIL_METHOD:
        return _unwrap_sku_detail_response(method, response_key, payload)

    if response_key in payload:
        envelope = payload[response_key]
        if not isinstance(envelope, dict):
            raise AliExpressResponseError(
                f"AliExpress API returned an invalid response wrapper for {method}"
            )
    elif "resp_result" in payload:
        # Some IOP responses omit the method-named outer wrapper.
        envelope = payload
    else:
        raise AliExpressResponseError(
            f"AliExpress API returned an unexpected response wrapper for {method}"
        )

    nested_error = envelope.get("error_response")
    if nested_error is not None:
        if not isinstance(nested_error, dict):
            raise AliExpressResponseError(
                f"AliExpress API returned an invalid error for {method}"
            )
        _raise_api_error(method, nested_error)

    resp_result = envelope.get("resp_result")
    if not isinstance(resp_result, dict):
        raise AliExpressResponseError(
            f"AliExpress API returned an invalid result for {method}"
        )
    if "resp_code" not in resp_result:
        raise AliExpressResponseError(
            f"AliExpress API returned an incomplete result for {method}"
        )
    resp_code = resp_result["resp_code"]
    if str(resp_code) != "200":
        _raise_api_error(
            method,
            {
                "code": resp_code,
                "sub_code": resp_result.get("sub_code"),
                "request_id": resp_result.get("request_id")
                or envelope.get("request_id")
                or payload.get("request_id"),
            },
        )
    return resp_result


def _unwrap_sku_detail_response(
    method: str,
    response_key: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Normalize both documented SKU Detail response packages.

    With ``simplify=false`` the SKU API nests its business result below the
    method-named response key.  Its streamlined package puts the same business
    result directly below the top-level ``result`` key.  Unlike the Standard
    affiliate methods, neither package uses ``resp_result``.
    """

    if response_key in payload:
        envelope = payload[response_key]
        if not isinstance(envelope, dict):
            raise AliExpressResponseError(
                f"AliExpress API returned an invalid response wrapper for {method}"
            )
        sku_result = envelope.get("result")
    elif "result" in payload:
        sku_result = payload.get("result")
    else:
        raise AliExpressResponseError(
            f"AliExpress API returned an unexpected response wrapper for {method}"
        )

    if not isinstance(sku_result, dict):
        raise AliExpressResponseError(
            f"AliExpress API returned an invalid result for {method}"
        )

    business_code = sku_result.get("code")
    success = sku_result.get("success")
    if business_code is None or success is None:
        raise AliExpressResponseError(
            f"AliExpress API returned an incomplete result for {method}"
        )
    is_success = success is True or (
        isinstance(success, str) and success.lower() == "true"
    )
    if str(business_code) != "200" or not is_success:
        _raise_api_error(
            method,
            {
                "code": business_code,
                "sub_code": sku_result.get("sub_code"),
                "request_id": sku_result.get("request_id")
                or payload.get("request_id"),
            },
        )

    if not isinstance(sku_result.get("result"), dict):
        raise AliExpressResponseError(
            f"AliExpress API returned an invalid SKU document for {method}"
        )
    return sku_result


def _raise_api_error(method: str, error: Mapping[str, Any]) -> None:
    code = error.get("code", error.get("resp_code"))
    sub_code = error.get("sub_code")
    request_id = error.get("request_id")
    safe_code = _safe_log_token(code) or "unknown"
    safe_sub_code = _safe_log_token(sub_code)
    safe_request_id = _safe_log_token(request_id)
    LOG.warning(
        "AliExpress API failure method=%s code=%s sub_code=%s request_id=%s",
        method,
        safe_code,
        safe_sub_code or "unknown",
        safe_request_id or "unknown",
    )
    raise AliExpressApiError(
        method,
        code=safe_code,
        sub_code=safe_sub_code,
        request_id=safe_request_id,
    )


def _credential(value: object, label: str, *, max_length: int) -> str:
    if not isinstance(value, str) or not value or len(value) > max_length:
        raise ValueError(f"Invalid AliExpress {label}")
    if any(ord(char) < 33 or ord(char) == 127 for char in value):
        raise ValueError(f"Invalid AliExpress {label}")
    return value


def _safe_log_token(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value)
    return text if _LOG_TOKEN_RE.fullmatch(text) is not None else None


def _header_request_id(headers: Any) -> str | None:
    if not hasattr(headers, "get"):
        return None
    for key in ("x-request-id", "request-id", "x-acs-request-id"):
        value = headers.get(key)
        safe = _safe_log_token(value)
        if safe:
            return safe
    return None


def _content_length(headers: Any) -> int | None:
    if not hasattr(headers, "get"):
        return None
    value = headers.get("content-length")
    if value is None:
        return None
    try:
        length = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return length if length >= 0 else None


def _bounded_response_content(response: Any, maximum: int, method: str) -> bytes:
    """Read a streamed response without buffering more than ``maximum`` bytes."""

    iterator = getattr(response, "iter_content", None)
    if callable(iterator):
        chunks: list[bytes] = []
        total = 0
        for chunk in iterator(chunk_size=64 * 1024):
            if not isinstance(chunk, bytes):
                raise AliExpressResponseError(
                    f"AliExpress API returned an invalid body for {method}"
                )
            if not chunk:
                continue
            total += len(chunk)
            if total > maximum:
                raise AliExpressResponseError(
                    f"AliExpress API response exceeded the size limit for {method}"
                )
            chunks.append(chunk)
        return b"".join(chunks)

    # Test doubles and non-requests-compatible sessions may expose only a
    # bounded bytes body. Production requests.Response objects use the
    # streaming branch above.
    content = getattr(response, "content", None)
    if not isinstance(content, bytes):
        raise AliExpressResponseError(
            f"AliExpress API returned an invalid body for {method}"
        )
    if len(content) > maximum:
        raise AliExpressResponseError(
            f"AliExpress API response exceeded the size limit for {method}"
        )
    return content


def _close_response(response: Any) -> None:
    close = getattr(response, "close", None)
    if callable(close):
        close()


def _validate_business_parameters(method: str, params: Mapping[str, str]) -> None:
    if method != SKU_DETAIL_METHOD:
        return

    required = {
        "ship_to_country",
        "product_id",
        "target_currency",
        "target_language",
    }
    if not required.issubset(params):
        raise ValueError("AliExpress SKU detail parameters are incomplete")
    if _SHIP_TO_COUNTRY_RE.fullmatch(params["ship_to_country"]) is None:
        raise ValueError("Invalid AliExpress ship-to country")
    if _SKU_PRODUCT_ID_RE.fullmatch(params["product_id"]) is None:
        raise ValueError("Invalid AliExpress product id")
    if params["target_currency"] not in _TARGET_CURRENCIES:
        raise ValueError("Invalid AliExpress target currency")
    if params["target_language"] not in _TARGET_LANGUAGES:
        raise ValueError("Invalid AliExpress target language")

    deliver_info = params.get("need_deliver_info")
    if deliver_info is not None and deliver_info not in {"Yes", "No"}:
        raise ValueError("Invalid AliExpress delivery-info option")

    raw_sku_ids = params.get("sku_ids")
    if raw_sku_ids is None:
        return
    sku_ids = raw_sku_ids.split(",")
    if (
        not 1 <= len(sku_ids) <= 20
        or len(set(sku_ids)) != len(sku_ids)
        or any(_SKU_ID_RE.fullmatch(sku_id) is None for sku_id in sku_ids)
    ):
        raise ValueError("Invalid AliExpress SKU id list")
