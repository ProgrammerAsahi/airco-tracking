from __future__ import annotations

import unittest

import requests

from airco_tracker.fetch import Fetcher


class _FakeResponse:
    def __init__(self, content: bytes, text: str, status: int = 200):
        self.content = content
        self.text = text
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error", response=self)


class FetcherTests(unittest.TestCase):
    def _fetcher_with_response(self, response: _FakeResponse) -> Fetcher:
        fetcher = Fetcher()
        # Replace only the get call so retry/headers setup still runs in __init__.
        fetcher.session.get = lambda url, timeout=None: response  # type: ignore[assignment]
        return fetcher

    def test_returns_text_for_normal_response(self) -> None:
        body = "x" * 11_000
        fetcher = self._fetcher_with_response(_FakeResponse(body.encode(), body))
        self.assertEqual(fetcher.get("https://shop.test/"), body)

    def test_rejects_suspiciously_small_response(self) -> None:
        body = "too small"
        fetcher = self._fetcher_with_response(_FakeResponse(body.encode(), body))
        with self.assertRaises(RuntimeError):
            fetcher.get("https://shop.test/")

    def test_propagates_http_error(self) -> None:
        body = "x" * 11_000
        fetcher = self._fetcher_with_response(_FakeResponse(body.encode(), body, status=503))
        with self.assertRaises(requests.HTTPError):
            fetcher.get("https://shop.test/")

    def test_user_agent_includes_version_identifier(self) -> None:
        fetcher = Fetcher()
        self.assertIn("AircoTracker/", fetcher.session.headers["User-Agent"])

    def test_boundary_size_is_accepted(self) -> None:
        # Exactly 10_000 bytes is the threshold; >= is accepted.
        body = "a" * 10_000
        fetcher = self._fetcher_with_response(_FakeResponse(body.encode(), body))
        self.assertEqual(fetcher.get("https://shop.test/"), body)


if __name__ == "__main__":
    unittest.main()
