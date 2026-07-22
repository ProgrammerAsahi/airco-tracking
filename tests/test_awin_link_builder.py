from __future__ import annotations

import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlencode

import requests

from airco_tracker.awin import AwinLinkBuilderClient


NOW = datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc)
PUBLISHER = "2981827"
ADVERTISER = "62319"
ONE = "https://fr.trotec.com/shop/pac-one.html"
TWO = "https://fr.trotec.com/shop/pac-two.html"


def _awin_url(destination: str, **overrides: str) -> str:
    values = {
        "awinmid": ADVERTISER,
        "awinaffid": PUBLISHER,
        "clickref": "",
        "ued": destination,
    }
    values.update(overrides)
    return "https://www.awin1.com/cread.php?" + urlencode(values)


def _item(
    destination: str,
    *,
    status: int = 200,
    url: str | None = None,
    documented_shape: bool = False,
):
    request = {
        "advertiserId": int(ADVERTISER),
        "destinationUrl": destination,
    }
    body = {
        "request": request,
        **({"url": url or _awin_url(destination)} if status == 200 else {}),
    }
    item = {
        "status": status,
        "body": body,
    }
    if documented_shape:
        body.pop("request")
        item["request"] = request
    return item


def _cache_payload(*rows, requested=(ONE,), imported=NOW):
    return {
        "version": 2,
        "last_imported": imported.isoformat(),
        "requested_urls": list(requested),
        "rows": list(rows),
        "source_row_count": len(requested),
    }


def _cache_row(destination=ONE, url=None):
    return {
        "canonical_url": destination,
        "affiliate_url": url or _awin_url(destination),
    }


class _Cache:
    def __init__(self, value=None, *, save_error=None):
        self.value = value
        self.save_error = save_error
        self.loads = []
        self.saves = []

    def load(self, namespace, key):
        self.loads.append((namespace, key))
        return self.value

    def save(self, namespace, key, value):
        if self.save_error is not None:
            raise self.save_error
        self.saves.append((namespace, key, value))
        self.value = value


class _Response:
    def __init__(self, payload=None, *, status=200):
        self.payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


class _Session:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


class _Fetcher:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def request_json(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.payload


def _client(session, cache, **overrides):
    values = {
        "session": session,
        "cache": cache,
        "cache_namespace": "awin-trotec-fr-links-v1",
        "cache_key": "links",
        "publisher_id": PUBLISHER,
        "advertiser_id": ADVERTISER,
        "bearer_token": "test-secret-token",
        "now": lambda: NOW,
    }
    values.update(overrides)
    return AwinLinkBuilderClient(**values)


class AwinLinkBuilderTests(unittest.TestCase):
    def test_generates_official_batch_links_and_adds_explicit_no_consent(self):
        session = _Session(_Response({"responses": [_item(ONE), _item(TWO)]}))
        cache = _Cache()

        links = _client(session, cache).links_for([ONE, TWO])

        self.assertEqual(set(links), {ONE, TWO})
        for destination, link in links.items():
            query = parse_qs(link.split("?", 1)[1], keep_blank_values=True)
            self.assertEqual(query["awinmid"], [ADVERTISER])
            self.assertEqual(query["awinaffid"], [PUBLISHER])
            self.assertEqual(query["ued"], [destination])
            self.assertEqual(query["cons"], ["0"])
        endpoint, request = session.calls[0]
        self.assertEqual(
            endpoint,
            "https://api.awin.com/publishers/2981827/linkbuilder/generate-batch",
        )
        self.assertEqual(request["headers"]["Authorization"], "Bearer test-secret-token")
        self.assertEqual(
            request["json"],
            {
                "requests": [
                    {"advertiserId": 62319, "destinationUrl": ONE},
                    {"advertiserId": 62319, "destinationUrl": TWO},
                ]
            },
        )
        self.assertEqual(cache.value["requested_urls"], [ONE, TWO])
        self.assertEqual(cache.value["source_row_count"], 2)

    def test_production_fetcher_path_is_bounded_and_single_attempt(self):
        fetcher = _Fetcher({"responses": [_item(ONE)]})
        client = AwinLinkBuilderClient(
            fetcher=fetcher,
            cache=_Cache(),
            cache_namespace="awin-trotec-fr-links-v1",
            cache_key="links",
            publisher_id=PUBLISHER,
            advertiser_id=ADVERTISER,
            bearer_token="test-secret-token",
            now=lambda: NOW,
        )

        links = client.links_for([ONE])

        self.assertEqual(set(links), {ONE})
        method, endpoint, request = fetcher.calls[0]
        self.assertEqual(method, "POST")
        self.assertIn("/linkbuilder/generate-batch", endpoint)
        self.assertEqual(request["maximum_response_bytes"], 2 * 1024 * 1024)
        self.assertNotIn("retry_read_only_post", request)

    def test_accepts_documented_top_level_echo_shape(self):
        links = _client(
            _Session(
                _Response(
                    {"responses": [_item(ONE, documented_shape=True)]}
                )
            ),
            _Cache(),
        ).links_for([ONE])

        self.assertEqual(set(links), {ONE})

    def test_fresh_cache_suppresses_repeated_api_calls(self):
        cache = _Cache(_cache_payload(_cache_row()))
        session = _Session()

        first = _client(session, cache).links_for([ONE])
        second = _client(session, cache).links_for([ONE])

        self.assertEqual(first, second)
        self.assertEqual(parse_qs(first[ONE].split("?", 1)[1])["cons"], ["0"])
        self.assertEqual(session.calls, [])

    def test_partial_failure_is_negative_cached_for_the_ttl(self):
        response = _Response({"responses": [_item(ONE), _item(TWO, status=400)]})
        cache = _Cache()
        session = _Session(response)
        client = _client(session, cache)

        links = client.links_for([ONE, TWO])
        again = client.links_for([ONE, TWO])

        self.assertEqual(set(links), {ONE})
        self.assertEqual(again, links)
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(cache.value["source_row_count"], 2)
        self.assertEqual(len(cache.value["rows"]), 1)

    def test_network_failure_uses_only_validated_cached_links(self):
        stale = _cache_payload(
            _cache_row(),
            requested=(ONE,),
            imported=NOW - timedelta(days=2),
        )
        session = _Session(_Response(status=503))

        links = _client(session, _Cache(stale)).links_for([ONE])

        self.assertEqual(set(links), {ONE})
        self.assertEqual(parse_qs(links[ONE].split("?", 1)[1])["cons"], ["0"])

    def test_invalid_cached_link_is_discarded_before_failed_refresh(self):
        stale = _cache_payload(
            _cache_row(url="https://evil.example/phish"),
            requested=(ONE,),
            imported=NOW - timedelta(days=2),
        )
        session = _Session(_Response(status=503))

        with self.assertLogs("airco_tracker.awin", level="WARNING"):
            links = _client(session, _Cache(stale)).links_for([ONE])

        self.assertEqual(links, {})

    def test_invalid_api_link_fails_closed_without_saving(self):
        bad = _item(ONE, url="https://evil.example/phish")
        cache = _Cache()

        with self.assertLogs("airco_tracker.awin", level="WARNING"):
            links = _client(
                _Session(_Response({"responses": [bad]})), cache
            ).links_for([ONE])

        self.assertEqual(links, {})
        self.assertEqual(cache.saves, [])

    def test_wrong_advertiser_publisher_or_destination_fails_closed(self):
        bad_urls = (
            _awin_url(ONE, awinmid="999"),
            _awin_url(ONE, awinaffid="999"),
            _awin_url(TWO),
        )
        for bad_url in bad_urls:
            with self.subTest(url=bad_url):
                links = _client(
                    _Session(_Response({"responses": [_item(ONE, url=bad_url)]})),
                    _Cache(),
                ).links_for([ONE])
                self.assertEqual(links, {})

        wrong_request = _item(ONE)
        wrong_request["body"]["request"]["advertiserId"] = 999
        links = _client(
            _Session(_Response({"responses": [wrong_request]})),
            _Cache(),
        ).links_for([ONE])
        self.assertEqual(links, {})

    def test_control_characters_credentials_and_nonstandard_port_fail_closed(self):
        bad_urls = (
            _awin_url(ONE) + "\nForged: value",
            _awin_url(ONE).replace("www.awin1.com", "user@www.awin1.com"),
            _awin_url(ONE).replace("www.awin1.com", "www.awin1.com:444"),
        )
        for bad_url in bad_urls:
            with self.subTest(url=bad_url):
                links = _client(
                    _Session(_Response({"responses": [_item(ONE, url=bad_url)]})),
                    _Cache(),
                ).links_for([ONE])
                self.assertEqual(links, {})

    def test_cache_save_failure_does_not_discard_generated_links(self):
        cache = _Cache(save_error=RuntimeError("blob unavailable"))
        with self.assertLogs("airco_tracker.awin", level="WARNING"):
            links = _client(
                _Session(_Response({"responses": [_item(ONE)]})), cache
            ).links_for([ONE])
        self.assertEqual(set(links), {ONE})

    def test_rejects_more_than_the_documented_batch_limit(self):
        urls = [f"https://fr.trotec.com/shop/pac-{index}.html" for index in range(101)]
        with self.assertRaisesRegex(ValueError, "too many"):
            _client(_Session(), _Cache()).links_for(urls)

    def test_concurrent_cold_calls_generate_one_batch(self):
        cache = _Cache()

        class SlowSession(_Session):
            def post(self, url, **kwargs):
                time.sleep(0.04)
                return super().post(url, **kwargs)

        session = SlowSession(_Response({"responses": [_item(ONE)]}))
        client = _client(session, cache)
        results = []

        threads = [threading.Thread(target=lambda: results.append(client.links_for([ONE]))) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(len(session.calls), 1)
        self.assertEqual(results, [{ONE: results[0][ONE]}, {ONE: results[0][ONE]}])

    def test_secret_is_never_logged_on_http_failure(self):
        with self.assertLogs("airco_tracker.awin", level="WARNING") as captured:
            links = _client(
                _Session(_Response(status=401)), _Cache()
            ).links_for([ONE])
        self.assertEqual(links, {})
        self.assertNotIn("test-secret-token", "\n".join(captured.output))


if __name__ == "__main__":
    unittest.main()
