from __future__ import annotations

import unittest

import requests

from airco_tracker.fetch import Fetcher


class _FakeResponse:
    def __init__(
        self,
        content: bytes,
        *,
        status: int = 200,
        content_type: str = "text/html; charset=utf-8",
        headers: dict[str, str] | None = None,
    ) -> None:
        self._content = content
        self.status_code = status
        self.headers = {"Content-Type": content_type, **(headers or {})}
        self.encoding = "utf-8"
        self.closed = False

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error", response=self)

    def iter_content(self, chunk_size: int):
        for offset in range(0, len(self._content), chunk_size):
            yield self._content[offset : offset + chunk_size]

    def close(self) -> None:
        self.closed = True


class FetcherTests(unittest.TestCase):
    def _fetcher_with_responses(self, *responses: _FakeResponse, **kwargs) -> Fetcher:
        fetcher = Fetcher(**kwargs)
        remaining = iter(responses)
        fetcher.session.get = lambda url, **request_kwargs: next(remaining)  # type: ignore[assignment]
        return fetcher

    def _fetcher_with_post_responses(self, *responses_or_errors, **kwargs) -> Fetcher:
        fetcher = Fetcher(**kwargs)
        remaining = iter(responses_or_errors)

        def post(url, **request_kwargs):
            value = next(remaining)
            if isinstance(value, BaseException):
                raise value
            return value

        fetcher.session.post = post  # type: ignore[assignment]
        return fetcher

    def test_returns_streamed_text_for_normal_response(self) -> None:
        body = b"x" * 11_000
        response = _FakeResponse(body)
        fetcher = self._fetcher_with_responses(response)
        self.assertEqual(fetcher.get("https://shop.test/"), body.decode())
        self.assertTrue(response.closed)

    def test_rejects_suspiciously_small_response(self) -> None:
        fetcher = self._fetcher_with_responses(_FakeResponse(b"too small"))
        with self.assertRaisesRegex(RuntimeError, "small"):
            fetcher.get("https://shop.test/")

    def test_rejects_content_length_and_stream_over_limit(self) -> None:
        declared = _FakeResponse(
            b"x" * 11_000,
            headers={"Content-Length": "20000"},
        )
        with self.assertRaisesRegex(RuntimeError, "limit"):
            self._fetcher_with_responses(declared, max_response_bytes=12_000).get(
                "https://shop.test/"
            )
        streamed = _FakeResponse(b"x" * 12_001)
        with self.assertRaisesRegex(RuntimeError, "limit"):
            self._fetcher_with_responses(streamed, max_response_bytes=12_000).get(
                "https://shop.test/"
            )

    def test_rejects_unexpected_or_missing_content_type(self) -> None:
        for content_type in ("image/png", ""):
            with self.subTest(content_type=content_type):
                fetcher = self._fetcher_with_responses(
                    _FakeResponse(b"x" * 11_000, content_type=content_type)
                )
                with self.assertRaisesRegex(RuntimeError, "Content-Type"):
                    fetcher.get("https://shop.test/")

    def test_propagates_http_error(self) -> None:
        fetcher = self._fetcher_with_responses(_FakeResponse(b"x" * 11_000, status=503))
        with self.assertRaises(requests.HTTPError):
            fetcher.get("https://shop.test/")

    def test_follows_same_merchant_redirect_but_rejects_cross_site(self) -> None:
        redirect = _FakeResponse(
            b"",
            status=302,
            headers={"Location": "https://www.shop.test/new"},
        )
        final = _FakeResponse(b"x" * 11_000)
        fetcher = self._fetcher_with_responses(redirect, final)
        self.assertEqual(fetcher.get("https://shop.test/old"), "x" * 11_000)

        redirect = _FakeResponse(
            b"",
            status=302,
            headers={"Location": "https://evil.test/new"},
        )
        fetcher = self._fetcher_with_responses(redirect)
        with self.assertRaisesRegex(RuntimeError, "cross-site"):
            fetcher.get("https://shop.test/old")

    def test_allows_explicit_redirect_host_and_limits_chain(self) -> None:
        redirect = _FakeResponse(
            b"",
            status=301,
            headers={"Location": "https://cdn.partner.test/page"},
        )
        final = _FakeResponse(b"x" * 11_000)
        fetcher = self._fetcher_with_responses(redirect, final)
        self.assertEqual(
            fetcher.get("https://shop.test/", allowed_redirect_hosts=("cdn.partner.test",)),
            "x" * 11_000,
        )

        redirect = _FakeResponse(
            b"",
            status=301,
            headers={"Location": "https://www.shop.test/again"},
        )
        fetcher = self._fetcher_with_responses(redirect, max_redirects=0)
        with self.assertRaisesRegex(RuntimeError, "Too many"):
            fetcher.get("https://shop.test/")

    def test_rejects_insecure_initial_url(self) -> None:
        with self.assertRaises(ValueError):
            Fetcher().get("http://shop.test/")

    def test_rejects_ip_literals_and_hosts_resolving_to_non_public_addresses(self) -> None:
        for url in (
            "https://127.0.0.1/admin",
            "https://169.254.169.254/latest/meta-data/",
            "https://10.20.30.40/internal",
            "https://[::1]/admin",
        ):
            with self.subTest(url=url):
                with self.assertRaisesRegex(ValueError, "IP literals"):
                    Fetcher().get(url)

        for addresses in (
            ("127.0.0.1",),
            ("169.254.169.254",),
            ("10.20.30.40",),
            ("93.184.216.34", "10.0.0.7"),
            ("224.0.0.1",),
            ("0.0.0.0",),
        ):
            with self.subTest(addresses=addresses):
                fetcher = Fetcher(resolver=lambda _host, values=addresses: values)
                fetcher.session.get = lambda *_args, **_kwargs: self.fail(  # type: ignore[assignment]
                    "blocked DNS answers must never reach the HTTP session"
                )
                with self.assertRaisesRegex(ValueError, "non-public"):
                    fetcher.get("https://attacker.example/payload")

    def test_dns_is_revalidated_for_redirects_and_read_only_post_retries(self) -> None:
        redirect = _FakeResponse(
            b"",
            status=302,
            headers={"Location": "https://www.shop.example/next"},
        )
        fetcher = self._fetcher_with_responses(
            redirect,
            resolver=lambda host: (
                ("93.184.216.34",) if host == "shop.example" else ("169.254.169.254",)
            ),
        )
        with self.assertRaisesRegex(ValueError, "non-public"):
            fetcher.get("https://shop.example/start")

        resolutions = iter((("93.184.216.34",), ("10.0.0.9",)))
        retrying = self._fetcher_with_post_responses(
            requests.ConnectionError("first connection failed"),
            _FakeResponse(b'{"ok":true}', content_type="application/json"),
            resolver=lambda _host: next(resolutions),
            sleep=lambda _delay: None,
        )
        with self.assertRaisesRegex(ValueError, "non-public"):
            retrying.request_json(
                "POST",
                "https://api.example/query",
                json_body={"query": "airco"},
                retry_read_only_post=True,
            )

    def test_rejects_boolean_or_out_of_range_transport_limits(self) -> None:
        for kwargs in (
            {"timeout": True},
            {"timeout": 121},
            {"max_response_bytes": True},
            {"max_redirects": True},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    Fetcher(**kwargs)

        with self.assertRaises(ValueError):
            Fetcher().request(None, "https://shop.test/")  # type: ignore[arg-type]

    def test_user_agent_includes_version_identifier(self) -> None:
        fetcher = Fetcher()
        self.assertIn("AircoTracker/", fetcher.session.headers["User-Agent"])

    def test_boundary_size_is_accepted(self) -> None:
        body = b"a" * 10_000
        fetcher = self._fetcher_with_responses(_FakeResponse(body))
        self.assertEqual(fetcher.get("https://shop.test/"), body.decode())

    def test_small_json_and_xml_are_valid_only_when_endpoint_opts_in(self) -> None:
        json_response = _FakeResponse(b'{"ok":true}', content_type="application/json")
        fetcher = self._fetcher_with_responses(json_response)
        self.assertEqual(
            fetcher.request_json("GET", "https://api.shop.test/stock"),
            {"ok": True},
        )

        xml_response = _FakeResponse(b"<urlset/>", content_type="application/xml")
        fetcher = self._fetcher_with_responses(xml_response)
        self.assertEqual(
            fetcher.get_bytes(
                "https://shop.test/sitemap.xml",
                allowed_content_types=("application/xml",),
            ),
            b"<urlset/>",
        )

        html_response = _FakeResponse(b"tiny", content_type="text/html")
        with self.assertRaisesRegex(RuntimeError, "small"):
            self._fetcher_with_responses(html_response).get("https://shop.test/")

    def test_read_only_post_retry_is_explicit_bounded_and_accepts_empty_429(self) -> None:
        retry = _FakeResponse(b"", status=429, content_type="")
        success = _FakeResponse(b'{"hits":[]}', content_type="application/json")
        delays = []
        fetcher = self._fetcher_with_post_responses(
            retry,
            success,
            sleep=delays.append,
        )

        payload = fetcher.request_json(
            "POST",
            "https://api.shop.test/query",
            json_body={"query": "airco"},
            retry_read_only_post=True,
        )

        self.assertEqual(payload, {"hits": []})
        self.assertEqual(delays, [0.25])
        self.assertTrue(retry.closed)
        self.assertTrue(success.closed)

    def test_post_is_not_retried_without_explicit_read_only_opt_in(self) -> None:
        fetcher = self._fetcher_with_post_responses(
            requests.ConnectionError("offline"),
            _FakeResponse(b'{"ok":true}', content_type="application/json"),
            sleep=lambda _delay: self.fail("non-idempotent POST must not sleep/retry"),
        )

        with self.assertRaises(requests.ConnectionError):
            fetcher.request_json(
                "POST",
                "https://api.shop.test/mutate",
                json_body={"operation": "write"},
            )

    def test_sensitive_headers_are_not_forwarded_across_redirect_hosts(self) -> None:
        redirect = _FakeResponse(
            b"",
            status=307,
            headers={"Location": "https://api.vendor.test/query"},
        )
        fetcher = self._fetcher_with_post_responses(redirect)
        with self.assertRaisesRegex(RuntimeError, "sensitive headers"):
            fetcher.request_json(
                "POST",
                "https://api.shop.test/query",
                headers={"Authorization": "Bearer secret"},
                json_body={"query": "airco"},
                allowed_redirect_hosts=("api.vendor.test",),
            )

    def test_per_call_maximum_rejects_small_api_overflow(self) -> None:
        response = _FakeResponse(b'{"payload":"' + b"x" * 100 + b'"}', content_type="application/json")
        fetcher = self._fetcher_with_responses(response)
        with self.assertRaisesRegex(RuntimeError, "limit"):
            fetcher.request_json(
                "GET",
                "https://api.shop.test/stock",
                maximum_response_bytes=32,
            )


if __name__ == "__main__":
    unittest.main()
