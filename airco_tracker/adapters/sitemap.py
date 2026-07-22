from __future__ import annotations

import gzip
from xml.etree import ElementTree

from ..url_security import validate_discovered_merchant_url


def sitemap_locations(content: bytes, *, site: str | None = None) -> list[str]:
    try:
        raw = gzip.decompress(content) if content.startswith(b"\x1f\x8b") else content
        root = ElementTree.fromstring(raw)
    except (OSError, ElementTree.ParseError) as exc:
        raise RuntimeError("product sitemap was invalid") from exc
    locations = [
        (node.text or "").strip()
        for node in root.findall(".//{*}loc")
        if (node.text or "").strip()
    ]
    if site is not None:
        return [
            validate_discovered_merchant_url(location, site=site)
            for location in locations
        ]
    return locations
