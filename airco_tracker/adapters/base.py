from __future__ import annotations

import re
from abc import ABC, abstractmethod
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag

from ..fetch import Fetcher
from ..models import Product


PRICE_PATTERNS = (
    re.compile(r"(?:€|EUR)\s*([\d.]+)\s*[,.]\s*(\d{2})", re.I),
    re.compile(r"(?:€|EUR)\s*([\d.]+)\s*,?\s*[-–]", re.I),
    re.compile(r"prijs[^\d]{0,40}([\d.]+)['’]?\s*euro(?:\s+en\s+['’]?(\d+)['’]?\s+cent)?", re.I),
    re.compile(r"\b([\d.]{2,})\s*[,.]\s*(\d{2})\b"),
    re.compile(r"\b([\d.]{2,})\s*,?\s*[-–]"),
)
BTU_RE = re.compile(r"(?<![\d.,])(\d{1,2}[., ]\d{3}|\d{3,5})(?!\d)\s*BTU\b", re.I)


def clean_text(node: Tag) -> str:
    return " ".join(node.get_text(" ", strip=True).split())


def canonical_url(base: str, href: str) -> str:
    parts = urlsplit(urljoin(base, href))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def parse_price(text: str) -> float | None:
    for pattern in PRICE_PATTERNS:
        match = pattern.search(text)
        if match:
            euros = int(match.group(1).replace(".", ""))
            cents = int(match.group(2) or 0) if match.lastindex and match.lastindex > 1 else 0
            return euros + cents / 100
    return None


def parse_btu(text: str) -> int | None:
    match = BTU_RE.search(text)
    if match:
        return int(re.sub(r"\D", "", match.group(1)))
    shorthand = re.search(r"\b(\d{1,2})K\s*BTU\b", text, re.I)
    return int(shorthand.group(1)) * 1000 if shorthand else None


def product_context(link: Tag, href_fragment: str, markers: tuple[str, ...]) -> Tag:
    """Find the smallest ancestor containing this product and an availability signal."""
    current: Tag = link
    fallback: Tag = link.parent if isinstance(link.parent, Tag) else link
    for _ in range(12):
        parent = current.parent
        if not isinstance(parent, Tag):
            break
        current = parent
        text = clean_text(current).lower()
        hrefs = {
            a.get("href", "").split("?", 1)[0]
            for a in current.select(f'a[href*="{href_fragment}"]')
        }
        if len(hrefs) == 1:
            fallback = current
            if any(marker in text for marker in markers):
                return current
        elif len(hrefs) > 1:
            break
    return fallback


def product_name(card: Tag, href: str) -> str:
    candidates = card.select(f'a[href="{href}"], a[href^="{href}?"]')
    for candidate in candidates:
        text = clean_text(candidate)
        if text:
            return text
        image = candidate.find("img", alt=True)
        if image and image.get("alt"):
            return str(image["alt"]).strip()
    heading = card.find(["h2", "h3"])
    if heading:
        return clean_text(heading)
    image = card.find("img", alt=True)
    return str(image["alt"]).strip() if image else "Onbekende mobiele airco"


class Adapter(ABC):
    site: str
    urls: tuple[str, ...]

    def __init__(self, fetcher: Fetcher) -> None:
        self.fetcher = fetcher

    def fetch_products(self) -> list[Product]:
        products: dict[str, Product] = {}
        for url in self.urls:
            soup = BeautifulSoup(self.fetcher.get(url), "html.parser")
            for product in self.parse(soup, url):
                products[product.url] = product
        if not products:
            raise RuntimeError(f"{self.site}: parser found no products; site markup may have changed")
        return list(products.values())

    @abstractmethod
    def parse(self, soup: BeautifulSoup, page_url: str) -> list[Product]:
        raise NotImplementedError
