from __future__ import annotations

import logging

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


LOG = logging.getLogger(__name__)


class Fetcher:
    def __init__(self, timeout: int = 25) -> None:
        self.timeout = timeout
        self.session = requests.Session()
        retry = Retry(
            total=2,
            connect=2,
            read=2,
            backoff_factor=1.0,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET",),
            respect_retry_after_header=True,
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 Chrome/124 Safari/537.36 "
                    "AircoTrackerNL/0.1"
                ),
                "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.7",
            }
        )

    def get(self, url: str) -> str:
        LOG.info("Fetching %s", url)
        response = self.session.get(url, timeout=self.timeout)
        response.raise_for_status()
        if len(response.content) < 10_000:
            raise RuntimeError(f"Suspiciously small response from {url}")
        return response.text
