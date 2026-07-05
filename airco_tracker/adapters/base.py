from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import replace
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag

from ..fetch import Fetcher
from ..models import Product
from .schema import product_json_ld


LOG = logging.getLogger(__name__)

# Delivery text markers that indicate a product is a presale or has a
# multi-week lead time — not immediate stock. Used by is_presale_delivery()
# and by adapters that need to separate presale from in-stock products.
PRESALE_MARKERS = (
    "voorbestelling",
    "pre-order",
    "pre order",
    "levering vanaf",
    "leverbaar vanaf",
    "verzending vanaf",
    "weken",  # multi-week lead time, e.g. "Binnen 3-5 weken leverbaar"
    "binnenkort beschikbaar",
    "tijdelijk niet beschikbaar",
)


def is_presale_delivery(text: str) -> bool:
    """Return True if the delivery text indicates a presale or multi-week lead time."""
    lower = text.lower()
    return any(marker in lower for marker in PRESALE_MARKERS)

PRICE_PATTERNS = (
    re.compile(r"(?:€|EUR)\s*([\d.]+)\s*[,.]\s*(\d{2})", re.I),
    re.compile(r"(?:€|EUR)\s*([\d.]+)\s*,?\s*[-–]", re.I),
    re.compile(r"prijs[^\d]{0,40}([\d.]+)['’]?\s*euro(?:\s+en\s+['’]?(\d+)['’]?\s+cent)?", re.I),
    re.compile(r"\b([\d.]{2,})\s*[,.]\s*(\d{2})\b"),
    re.compile(r"\b([\d.]{2,})\s*,?\s*[-–]"),
)
BTU_RE = re.compile(r"(?<![\d.,])(\d{1,2}[., ]\d{3}|\d{3,5})(?!\d)\s*BTU\b", re.I)
BTU_AFTER_LABEL_RE = re.compile(
    r"(?:koelcapaciteit|koelvermogen|maximaal\s+koelvermogen)"
    r"[^\d]{0,40}\bBTU(?:\s*/\s*[hu])?[^\d]{0,15}"
    r"(\d{1,2}[., ]\d{3}|\d{3,5})(?!\d)",
    re.I,
)
COOLING_LABEL = r"(?:koelvermogen|koelcapaciteit|cooling\s+capacity)"
COOLING_WATTS_AFTER_RE = re.compile(
    rf"{COOLING_LABEL}.{{0,50}}?(\d+(?:[.,]\d+)?)\s*(kW|Watt|W)\b",
    re.I,
)
COOLING_WATTS_BEFORE_RE = re.compile(
    rf"(\d+(?:[.,]\d+)?)\s*(kW|Watt|W)\b.{{0,50}}?{COOLING_LABEL}",
    re.I,
)
LABELED_BTU_PATTERNS = (
    re.compile(
        rf"{COOLING_LABEL}.{{0,60}}?"
        r"(\d{1,2}[., ]\d{3}|\d{3,5})(?!\d)\s*BTU\b",
        re.I,
    ),
    re.compile(
        r"(?<![\d.,])(\d{1,2}[., ]\d{3}|\d{3,5})(?!\d)\s*BTU(?:\s*/\s*[hu])?"
        rf".{{0,40}}?{COOLING_LABEL}",
        re.I,
    ),
    BTU_AFTER_LABEL_RE,
)
WATT_RATING_RE = re.compile(r"(\d{1,2}[.,]?\d{3}|\d{3,5})\s*W\b", re.I)
KNOWN_MODEL_BTU = (
    (re.compile(r"\barcticmove\s+1500(?:\s*w)?\b", re.I), 5118),
    (re.compile(r"\bqlima\s+p\s*3020\b", re.I), 6824),
    (re.compile(r"\bqlima\s+p\s*326\b", re.I), 9000),
    (re.compile(r"\bqlima\s+p\s*335\b", re.I), 12000),
    (re.compile(r"\bcomfee\b.*?smart\s*cool\s+12[. ]?000\b", re.I), 12000),
    (re.compile(r"\bcomfee\b.*?\b9000\s+pro\b", re.I), 9000),
)


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
    if shorthand:
        return int(shorthand.group(1)) * 1000
    after_label = BTU_AFTER_LABEL_RE.search(text)
    if after_label:
        return int(re.sub(r"\D", "", after_label.group(1)))
    for pattern, btu in KNOWN_MODEL_BTU:
        if pattern.search(text):
            return btu
    return None


def parse_cooling_watts_btu(text: str) -> int | None:
    """Convert only explicitly labelled cooling-capacity watts to BTU/h."""
    match = COOLING_WATTS_AFTER_RE.search(text) or COOLING_WATTS_BEFORE_RE.search(text)
    if not match:
        return None
    value = float(match.group(1).replace(",", "."))
    watts = value * 1000 if match.group(2).lower() == "kw" else value
    if not 300 <= watts <= 10_000:
        return None
    return round(watts * 3.412)


def parse_watt_rating_btu(text: str) -> int | None:
    """Convert a trusted product-title cooling rating such as ``3500W``."""
    match = WATT_RATING_RE.search(text)
    if not match:
        return None
    watts = int(re.sub(r"\D", "", match.group(1)))
    if not 1000 <= watts <= 6000:
        return None
    return round(watts * 3.412)


def parse_product_page_btu(page: str) -> int | None:
    """Read product-specific structured data, then labelled visible specs."""
    soup = BeautifulSoup(page, "html.parser")
    try:
        data = product_json_ld(soup)
    except RuntimeError:
        data = {}
    if data:
        structured = json.dumps(data, ensure_ascii=False)
        parsed = parse_btu(structured) or parse_cooling_watts_btu(structured)
        if parsed is not None:
            return parsed

    main = soup.find("main") or soup
    text = clean_text(main)
    for pattern in LABELED_BTU_PATTERNS:
        match = pattern.search(text)
        if match:
            return int(re.sub(r"\D", "", match.group(1)))
    return parse_cooling_watts_btu(text)


def enrich_available_btu(fetcher: Fetcher, products: list[Product]) -> list[Product]:
    """Fetch details only for alert-eligible products whose BTU is unknown."""
    enriched: list[Product] = []
    for product in products:
        if not product.available or product.btu is not None:
            enriched.append(product)
            continue
        try:
            btu = parse_product_page_btu(fetcher.get(product.url))
        except Exception as exc:
            LOG.warning("BTU enrichment failed for %s: %s", product.url, exc)
            enriched.append(product)
            continue
        enriched.append(replace(product, btu=btu) if btu is not None else product)
    return enriched


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
        return enrich_available_btu(self.fetcher, list(products.values()))

    @abstractmethod
    def parse(self, soup: BeautifulSoup, page_url: str) -> list[Product]:
        raise NotImplementedError
