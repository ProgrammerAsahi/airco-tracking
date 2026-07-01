from __future__ import annotations

import gzip
from xml.etree import ElementTree


def sitemap_locations(content: bytes) -> list[str]:
    try:
        raw = gzip.decompress(content) if content.startswith(b"\x1f\x8b") else content
        root = ElementTree.fromstring(raw)
    except (OSError, ElementTree.ParseError) as exc:
        raise RuntimeError("product sitemap was invalid") from exc
    return [
        (node.text or "").strip()
        for node in root.findall(".//{*}loc")
        if (node.text or "").strip()
    ]
