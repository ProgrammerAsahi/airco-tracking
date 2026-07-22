from __future__ import annotations

import json
import logging
import unittest
from types import SimpleNamespace

import requests

from airco_tracker.aliexpress import (
    PARTNER_ID,
    PRODUCT_QUERY_METHOD,
    PRODUCTION_ENDPOINT,
    SKU_DETAIL_METHOD,
    AliExpressApiError,
    AliExpressClient,
    AliExpressHttpError,
    AliExpressResponseError,
    AliExpressTransportError,
    _sign_parameters,
)


APP_KEY = "123456"
APP_SECRET = "test_secret"
TIMESTAMP = 1_700_000_000_000


class _Response:
    def __init__(self, payload=None, *, status_code=200, content=None, headers=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._payload = payload
        if content is None:
            content = json.dumps(payload).encode("utf-8")
        self.content = content

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _StreamingResponse(_Response):
    def __init__(self, chunks, *, status_code=200, headers=None):
        super().__init__({}, status_code=status_code, content=b"", headers=headers)
        self._chunks = list(chunks)
        self.closed = False

    def iter_content(self, chunk_size):
        self.chunk_size = chunk_size
        for chunk in self._chunks:
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk

    def close(self):
        self.closed = True


class _Session:
    def __init__(self, response=None, error=None):
        self.response = response or _Response(_wrapped(PRODUCT_QUERY_METHOD))
        self.error = error
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error is not None:
            raise self.error
        return self.response


class _SequenceSession:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _FetcherTransport:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return SimpleNamespace(
            status_code=200,
            headers={"Content-Type": "application/json"},
            content=json.dumps(self.payload).encode("utf-8"),
            url=url,
        )


def _wrapped(method, *, resp_code=200, result=None):
    return {
        method.replace(".", "_") + "_response": {
            "resp_result": {
                "resp_code": resp_code,
                "resp_msg": "success" if str(resp_code) == "200" else "failure",
                "result": result if result is not None else {"ok": True},
            }
        }
    }


def _client(session, **overrides):
    kwargs = {
        "session": session,
        "app_key": APP_KEY,
        "app_secret": APP_SECRET,
        "now_ms": lambda: TIMESTAMP,
        "sleep": lambda _delay: None,
    }
    kwargs.update(overrides)
    return AliExpressClient(**kwargs)


def _sku_params(**overrides):
    params = {
        "ship_to_country": "NL",
        "product_id": "1005001234567890",
        "target_currency": "EUR",
        "target_language": "EN",
        "need_deliver_info": "Yes",
    }
    params.update(overrides)
    return params


class AliExpressSignatureTests(unittest.TestCase):
    def test_sku_detail_fixed_signature_vector(self):
        params = {
            "app_key": APP_KEY,
            "format": "json",
            "method": SKU_DETAIL_METHOD,
            "need_deliver_info": "Yes",
            "partner_id": PARTNER_ID,
            "product_id": "1005001234567890",
            "ship_to_country": "NL",
            "sign_method": "sha256",
            "simplify": "false",
            "sku_ids": "12000041407359776,12000041669723427",
            "target_currency": "EUR",
            "target_language": "EN",
            "timestamp": str(TIMESTAMP),
        }

        self.assertEqual(
            _sign_parameters(APP_SECRET, params),
            "A7EBBA97DA69BE9354B89FA16219AC6763D94FA893B9571AED7A2A8AED9CA686",
        )

    def test_product_query_fixed_signature_vector_signs_space_before_encoding(self):
        params = {
            "app_key": APP_KEY,
            "format": "json",
            "keywords": "portable air conditioner",
            "method": PRODUCT_QUERY_METHOD,
            "page_no": "1",
            "page_size": "10",
            "partner_id": PARTNER_ID,
            "ship_to_country": "FR",
            "sign_method": "sha256",
            "simplify": "false",
            "target_currency": "EUR",
            "target_language": "FR",
            "timestamp": str(TIMESTAMP),
        }

        self.assertEqual(
            _sign_parameters(APP_SECRET, params),
            "848C221222149362E1693BCF01A5E53CC54A56F25FE3C42121C8D2364783FF98",
        )

    def test_sign_parameter_cannot_sign_itself(self):
        with self.assertRaisesRegex(ValueError, "cannot sign itself"):
            _sign_parameters(APP_SECRET, {"app_key": APP_KEY, "sign": "bad"})


class AliExpressClientTests(unittest.TestCase):
    def test_product_query_posts_signed_form_to_fixed_gateway(self):
        session = _Session(
            _Response(_wrapped(PRODUCT_QUERY_METHOD, result={"products": []}))
        )

        result = _client(session).product_query(
            {
                "keywords": "portable air conditioner",
                "page_no": 1,
                "page_size": 10,
                "ship_to_country": "FR",
                "target_currency": "EUR",
                "target_language": "FR",
            }
        )

        self.assertEqual(result["result"], {"products": []})
        self.assertEqual(len(session.calls), 1)
        url, kwargs = session.calls[0]
        self.assertEqual(url, PRODUCTION_ENDPOINT)
        self.assertNotIn("?", url)
        self.assertEqual(kwargs["timeout"], 10.0)
        self.assertTrue(kwargs["stream"])
        self.assertEqual(kwargs["data"]["method"], PRODUCT_QUERY_METHOD)
        self.assertEqual(kwargs["data"]["partner_id"], PARTNER_ID)
        self.assertEqual(kwargs["data"]["timestamp"], str(TIMESTAMP))
        self.assertEqual(kwargs["data"]["sign_method"], "sha256")
        self.assertEqual(len(kwargs["data"]["sign"]), 64)
        self.assertNotIn("session", kwargs["data"])
        self.assertNotIn(APP_SECRET, json.dumps(kwargs["data"]))
        unsigned = {
            key: value for key, value in kwargs["data"].items() if key != "sign"
        }
        self.assertEqual(kwargs["data"]["sign"], _sign_parameters(APP_SECRET, unsigned))

    def test_sku_detail_uses_same_protocol(self):
        payload = {
            "code": "0",
            "aliexpress_affiliate_product_sku_detail_get_response": {
                "result": {
                    "result": {"ae_item_info": {"product_id": "1005001234567890"}},
                    "code": "200",
                    "success": "true",
                }
            },
            "request_id": "sku-request-1",
        }
        session = _Session(_Response(payload))

        result = _client(session).product_sku_detail(
            _sku_params(
                product_id=1005001234567890,
                sku_ids="12000041407359776,12000041669723427",
            )
        )

        self.assertEqual(
            result["result"]["ae_item_info"]["product_id"],
            "1005001234567890",
        )
        data = session.calls[0][1]["data"]
        self.assertEqual(data["method"], SKU_DETAIL_METHOD)
        self.assertEqual(data["product_id"], "1005001234567890")
        self.assertEqual(data["target_currency"], "EUR")
        self.assertEqual(data["target_language"], "EN")
        self.assertEqual(data["need_deliver_info"], "Yes")
        self.assertEqual(
            data["sku_ids"], "12000041407359776,12000041669723427"
        )

    def test_streamlined_sku_detail_response_is_supported(self):
        payload = {
            "result": {
                "result": {"ae_item_info": {"product_id": "1005009876543210"}},
                "code": "200",
                "success": "true",
            },
            "code": "0",
            "request_id": "sku-request-2",
        }

        result = _client(_Session(_Response(payload))).product_sku_detail(
            _sku_params(
                product_id=1005009876543210,
                ship_to_country="FR",
                target_language="FR",
                need_deliver_info="No",
            )
        )

        self.assertEqual(
            result["result"]["ae_item_info"]["product_id"],
            "1005009876543210",
        )

    def test_sku_business_code_405_is_not_an_http_error(self):
        payload = {
            "code": "0",
            "aliexpress_affiliate_product_sku_detail_get_response": {
                "result": {
                    "result": {},
                    "code": "405",
                    "success": "false",
                }
            },
            "request_id": "sku-request-405",
        }

        session = _Session(_Response(payload))
        with self.assertLogs("airco_tracker.aliexpress", level=logging.WARNING):
            with self.assertRaises(AliExpressApiError) as raised:
                _client(session).product_sku_detail(_sku_params())

        self.assertNotIsInstance(raised.exception, AliExpressHttpError)
        self.assertEqual(raised.exception.code, "405")
        self.assertEqual(raised.exception.request_id, "sku-request-405")
        self.assertEqual(len(session.calls), 1)

    def test_live_sku_code_15_sub_code_405_is_preserved(self):
        payload = {
            "code": "0",
            "aliexpress_affiliate_product_sku_detail_get_response": {
                "result": {
                    "result": {},
                    "code": "15",
                    "sub_code": "405",
                    "success": False,
                }
            },
            "request_id": "sku-request-live-405",
        }

        with self.assertLogs("airco_tracker.aliexpress", level=logging.WARNING):
            with self.assertRaises(AliExpressApiError) as raised:
                _client(_Session(_Response(payload))).product_sku_detail(_sku_params())

        self.assertEqual(raised.exception.code, "15")
        self.assertEqual(raised.exception.sub_code, "405")
        self.assertEqual(raised.exception.request_id, "sku-request-live-405")

    def test_sku_detail_contract_rejects_missing_and_invalid_parameters(self):
        client = _client(_Session())
        invalid = (
            {"product_id": "1005001234567890"},
            _sku_params(ship_to_country="nl"),
            _sku_params(product_id="not-a-product"),
            _sku_params(target_currency="CHF"),
            _sku_params(target_language="ZH"),
            _sku_params(need_deliver_info="true"),
            _sku_params(sku_ids="1,1"),
            _sku_params(sku_ids=",".join(str(value) for value in range(1, 22))),
        )

        for params in invalid:
            with self.subTest(params=params):
                with self.assertRaises(ValueError):
                    client.product_sku_detail(params)

        self.assertEqual(client._session.calls, [])

    def test_standard_result_without_resp_code_fails_closed(self):
        payload = {
            PRODUCT_QUERY_METHOD.replace(".", "_") + "_response": {
                "resp_result": {"result": {"products": []}}
            }
        }

        with self.assertRaises(AliExpressResponseError):
            _client(_Session(_Response(payload))).product_query({"keywords": "airco"})

    def test_top_level_resp_result_wrapper_is_supported(self):
        payload = {
            "resp_result": {
                "resp_code": 200,
                "resp_msg": "success",
                "result": {"products": [1]},
            }
        }
        result = _client(_Session(_Response(payload))).product_query(
            {"keywords": "airco"}
        )

        self.assertEqual(result["result"], {"products": [1]})

    def test_reserved_and_invalid_business_parameters_are_rejected(self):
        client = _client(_Session())
        for params in (
            {"app_key": "override"},
            {"sign": "override"},
            {"session": "token"},
            {"bad-key": "value"},
            {"keywords": object()},
            {"keywords": "line\nbreak"},
        ):
            with self.subTest(params=params):
                with self.assertRaises(ValueError):
                    client.product_query(params)

    def test_boolean_values_have_stable_lowercase_encoding(self):
        session = _Session()

        _client(session).product_query({"flag": True, "other_flag": False})

        data = session.calls[0][1]["data"]
        self.assertEqual(data["flag"], "true")
        self.assertEqual(data["other_flag"], "false")

    def test_production_fetcher_path_is_bounded_and_explicitly_read_only(self):
        fetcher = _FetcherTransport(_wrapped(PRODUCT_QUERY_METHOD))
        client = AliExpressClient(
            fetcher=fetcher,
            app_key=APP_KEY,
            app_secret=APP_SECRET,
            now_ms=lambda: TIMESTAMP,
        )

        result = client.product_query({"keywords": "portable air conditioner"})

        self.assertTrue(result["result"]["ok"])
        method, url, request = fetcher.calls[0]
        self.assertEqual((method, url), ("POST", PRODUCTION_ENDPOINT))
        self.assertTrue(request["retry_read_only_post"])
        self.assertFalse(request["raise_for_status"])
        self.assertEqual(request["maximum_response_bytes"], 5_000_000)
        self.assertEqual(request["form_data"]["method"], PRODUCT_QUERY_METHOD)

    def test_http_405_is_sanitized_and_does_not_parse_remote_body(self):
        response = _Response(
            {"error": f"echo {APP_SECRET}"},
            status_code=405,
            headers={"x-request-id": "request-405"},
        )

        with self.assertLogs("airco_tracker.aliexpress", level=logging.WARNING) as logs:
            with self.assertRaises(AliExpressHttpError) as raised:
                _client(_Session(response)).product_query({"keywords": "private-value"})

        self.assertEqual(raised.exception.status_code, 405)
        self.assertEqual(raised.exception.request_id, "request-405")
        combined = " ".join(logs.output) + str(raised.exception)
        self.assertNotIn(APP_SECRET, combined)
        self.assertNotIn(APP_KEY, combined)
        self.assertNotIn("private-value", combined)

    def test_timeout_and_connection_failures_retry_twice_with_bounded_backoff(self):
        session = _SequenceSession(
            [
                requests.Timeout(f"timeout {APP_SECRET}"),
                requests.ConnectionError(f"connection {APP_KEY}"),
                _Response(_wrapped(PRODUCT_QUERY_METHOD)),
            ]
        )
        delays = []

        with self.assertLogs("airco_tracker.aliexpress", level=logging.WARNING) as logs:
            result = _client(session, sleep=delays.append).product_query(
                {"keywords": "private-value"}
            )

        self.assertTrue(result["result"]["ok"])
        self.assertEqual(len(session.calls), 3)
        self.assertEqual(delays, [0.25, 0.5])
        combined = " ".join(logs.output)
        for secret in (APP_SECRET, APP_KEY, "private-value"):
            self.assertNotIn(secret, combined)

    def test_retryable_http_statuses_retry_without_parsing_error_bodies(self):
        for status_code in (429, 502, 503, 504):
            with self.subTest(status_code=status_code):
                session = _SequenceSession(
                    [
                        _Response(
                            {"remote": APP_SECRET},
                            status_code=status_code,
                            headers={"x-request-id": f"retry-{status_code}"},
                        ),
                        _Response(_wrapped(PRODUCT_QUERY_METHOD)),
                    ]
                )
                delays = []

                with self.assertLogs(
                    "airco_tracker.aliexpress", level=logging.WARNING
                ) as logs:
                    result = _client(session, sleep=delays.append).product_query(
                        {"keywords": "private-value"}
                    )

                self.assertTrue(result["result"]["ok"])
                self.assertEqual(len(session.calls), 2)
                self.assertEqual(delays, [0.25])
                combined = " ".join(logs.output)
                for secret in (APP_SECRET, APP_KEY, "private-value"):
                    self.assertNotIn(secret, combined)

    def test_retryable_http_status_stops_after_two_retries(self):
        session = _SequenceSession(
            [
                _Response({"remote": APP_SECRET}, status_code=503),
                _Response({"remote": APP_SECRET}, status_code=503),
                _Response({"remote": APP_SECRET}, status_code=503),
            ]
        )
        delays = []

        with self.assertLogs("airco_tracker.aliexpress", level=logging.WARNING):
            with self.assertRaises(AliExpressHttpError) as raised:
                _client(session, sleep=delays.append).product_query(
                    {"keywords": "private-value"}
                )

        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(len(session.calls), 3)
        self.assertEqual(delays, [0.25, 0.5])

    def test_streamed_body_is_bounded_closed_and_read_failures_are_retried(self):
        payload = json.dumps(_wrapped(PRODUCT_QUERY_METHOD)).encode("utf-8")
        failed = _StreamingResponse([requests.Timeout(f"timeout {APP_SECRET}")])
        succeeded = _StreamingResponse([payload[:20], payload[20:]])
        session = _SequenceSession([failed, succeeded])
        delays = []

        with self.assertLogs("airco_tracker.aliexpress", level=logging.WARNING) as logs:
            result = _client(session, sleep=delays.append).product_query(
                {"keywords": "private-value"}
            )

        self.assertTrue(result["result"]["ok"])
        self.assertEqual(delays, [0.25])
        self.assertTrue(failed.closed)
        self.assertTrue(succeeded.closed)
        self.assertEqual(succeeded.chunk_size, 64 * 1024)
        combined = " ".join(logs.output)
        for secret in (APP_SECRET, APP_KEY, "private-value"):
            self.assertNotIn(secret, combined)

        oversized = _StreamingResponse([b"x" * 51, b"y" * 50])
        with self.assertRaisesRegex(AliExpressResponseError, "size limit"):
            _client(_Session(oversized), max_response_bytes=100).product_query({})
        self.assertTrue(oversized.closed)

    def test_other_http_and_request_errors_are_not_retried(self):
        delays = []
        http_session = _SequenceSession(
            [_Response({"remote": APP_SECRET}, status_code=400)]
        )
        with self.assertLogs("airco_tracker.aliexpress", level=logging.WARNING):
            with self.assertRaises(AliExpressHttpError):
                _client(http_session, sleep=delays.append).product_query({})
        self.assertEqual(len(http_session.calls), 1)
        self.assertEqual(delays, [])

        request_session = _SequenceSession(
            [requests.exceptions.InvalidURL(f"invalid {APP_SECRET}")]
        )
        with self.assertLogs("airco_tracker.aliexpress", level=logging.WARNING):
            with self.assertRaises(AliExpressTransportError):
                _client(request_session, sleep=delays.append).product_query({})
        self.assertEqual(len(request_session.calls), 1)
        self.assertEqual(delays, [])

    def test_error_response_is_sanitized(self):
        payload = {
            "error_response": {
                "type": "ISV",
                "code": "IncompleteSignature",
                "sub_code": "isv.invalid-signature",
                "msg": f"echo {APP_SECRET}",
                "request_id": "request-123",
            }
        }

        with self.assertLogs("airco_tracker.aliexpress", level=logging.WARNING) as logs:
            with self.assertRaises(AliExpressApiError) as raised:
                _client(_Session(_Response(payload))).product_query(
                    {"keywords": "private-value"}
                )

        error = raised.exception
        self.assertEqual(error.code, "IncompleteSignature")
        self.assertEqual(error.sub_code, "isv.invalid-signature")
        self.assertEqual(error.request_id, "request-123")
        combined = " ".join(logs.output) + str(error)
        for secret in (APP_SECRET, APP_KEY, "private-value"):
            self.assertNotIn(secret, combined)

    def test_direct_iop_error_and_resp_result_error_are_rejected(self):
        failures = (
            {"code": "InvalidParameter", "message": "do not log"},
            _wrapped(PRODUCT_QUERY_METHOD, resp_code=400),
        )
        for payload in failures:
            with self.subTest(payload=payload):
                with self.assertLogs("airco_tracker.aliexpress", level=logging.WARNING):
                    with self.assertRaises(AliExpressApiError):
                        _client(_Session(_Response(payload))).product_query({})

    def test_invalid_json_document_wrapper_and_body_size_fail_closed(self):
        cases = (
            _Response(ValueError("invalid"), content=b"not-json"),
            _Response([]),
            _Response({"unexpected": {"result": {}}}),
            _Response(_wrapped(PRODUCT_QUERY_METHOD), content=b"x" * 101),
        )
        for index, response in enumerate(cases):
            with self.subTest(index=index):
                client = _client(
                    _Session(response),
                    max_response_bytes=100 if index == 3 else 5_000_000,
                )
                with self.assertRaises(AliExpressResponseError):
                    client.product_query({})

    def test_transport_exception_is_sanitized(self):
        session = _Session(
            error=requests.Timeout(
                f"timeout app_key={APP_KEY} secret={APP_SECRET} keyword=private-value"
            )
        )

        with self.assertLogs("airco_tracker.aliexpress", level=logging.WARNING) as logs:
            with self.assertRaises(AliExpressTransportError) as raised:
                _client(session).product_query({"keywords": "private-value"})

        combined = " ".join(logs.output) + str(raised.exception)
        for secret in (APP_SECRET, APP_KEY, "private-value"):
            self.assertNotIn(secret, combined)

    def test_invalid_timestamp_fails_before_network(self):
        session = _Session()
        client = _client(session, now_ms=lambda: 123)

        with self.assertRaisesRegex(ValueError, "timestamp"):
            client.product_query({})

        self.assertEqual(session.calls, [])


if __name__ == "__main__":
    unittest.main()
