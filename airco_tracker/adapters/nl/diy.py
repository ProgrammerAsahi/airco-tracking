from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit
from xml.etree import ElementTree

from bs4 import BeautifulSoup

from ...models import Product
from ..base import (
    Adapter,
    canonical_url,
    clean_text,
    enrich_available_btu,
    parse_btu,
    parse_watt_rating_btu,
)


class DiyStoreAdapter(Adapter):
    """GAMMA/KARWEI category parser with public catalogue failover."""

    minimum_sitemap_product_urls = 100
    algolia_application_id = "4CI6R68JTI"
    algolia_search_key = "862dac9d0c819ca0670befe4a2a8ac28"
    algolia_index = ""
    algolia_category_facet = ""
    product_sitemap_url = ""
    product_sitemap_host = ""

    def fetch_products(self) -> list[Product]:
        try:
            return self._fetch_category_products()
        except Exception as exc:
            if not self.product_sitemap_url or not _is_rate_limited(exc):
                raise
            try:
                return self._fetch_algolia_products()
            except Exception as catalog_exc:
                candidates = self._portable_product_urls_from_sitemap()
                if not candidates:
                    # The official sitemap is a discovery source, not a stock
                    # source.  It can prove that the current catalogue has no
                    # portable-airco candidates, but a URL in it cannot prove
                    # that the product is in stock.
                    return self.verified_empty(
                        source="official_product_sitemap",
                        signal="healthy sitemap contained zero portable-airco candidates",
                    )
                raise RuntimeError(
                    f"{self.site}: category was rate limited, the public catalogue "
                    "lookup failed, and the official product sitemap still lists "
                    f"{len(candidates)} possible portable air conditioner(s); refusing "
                    "to replace inventory with an unverified result"
                ) from catalog_exc

    def _fetch_category_products(self) -> list[Product]:
        products: dict[str, Product] = {}
        for url in self.urls:
            soup = BeautifulSoup(self.fetcher.get(url), "html.parser")
            if not _has_supported_product_tiles(soup):
                raise RuntimeError(
                    f"{self.site}: parser found no supported product tiles; "
                    "site markup may have changed"
                )
            for product in self.parse(soup, url):
                products[product.url] = product
        return enrich_available_btu(self.fetcher, list(products.values()))

    def _fetch_algolia_products(self) -> list[Product]:
        if not self.algolia_index or not self.algolia_category_facet:
            raise RuntimeError(f"{self.site}: public catalogue is not configured")
        payload = self.fetcher.request_json(
            "POST",
            (
                f"https://{self.algolia_application_id.lower()}-dsn.algolia.net/1/indexes/"
                f"{self.algolia_index}/query"
            ),
            headers={
                "X-Algolia-Application-Id": self.algolia_application_id,
                # This is the search-only key published to visitors by the
                # GAMMA/KARWEI storefront, not an administrative credential.
                "X-Algolia-API-Key": self.algolia_search_key,
                "Content-Type": "application/json",
            },
            json_body={
                "query": "",
                "hitsPerPage": 100,
                "facetFilters": [f"slugs:{self.algolia_category_facet}"],
            },
            # This Algolia POST is a read-only catalogue lookup. Opting into
            # bounded POST retries is deliberate and safe.
            retry_read_only_post=True,
            maximum_response_bytes=4 * 1024 * 1024,
        )
        hits = _validated_catalog_hits(self.site, payload)
        products: dict[str, Product] = {}
        for hit in hits:
            name = hit["name"]
            if not self.is_portable_airco(name):
                continue
            url = canonical_url(self.urls[0], hit["url"])
            available = _catalog_online_available(self.site, hit)
            searchable_text = " ".join(
                (
                    name,
                    str(hit.get("description", "")),
                    str(hit.get("commercialDescriptionShort", "")),
                    str(hit.get("type_artikel", "")),
                    str(hit.get("tile_attributes", "")),
                )
            )
            products[url] = Product(
                site=self.site,
                name=name,
                url=url,
                available=available,
                price_eur=None,
                delivery="Online beschikbaar" if available else "Niet online beschikbaar",
                btu=parse_btu(searchable_text) or parse_watt_rating_btu(searchable_text),
            )
        return list(products.values())

    def _portable_product_urls_from_sitemap(self) -> list[str]:
        root = ElementTree.fromstring(self.fetcher.get(self.product_sitemap_url))
        if root.tag.rsplit("}", 1)[-1] != "urlset":
            raise RuntimeError(f"{self.site}: product sitemap root is not urlset")
        product_urls: list[str] = []
        for node in root.iter():
            if node.tag.rsplit("}", 1)[-1] != "loc":
                continue
            url = (node.text or "").strip()
            parsed = urlsplit(url)
            if (
                parsed.scheme != "https"
                or parsed.hostname != self.product_sitemap_host
                or not parsed.path.startswith("/assortiment/")
            ):
                raise RuntimeError(
                    f"{self.site}: product sitemap contains an unexpected URL"
                )
            if "/p/" in parsed.path:
                product_urls.append(url)
            elif "/r/" not in parsed.path:
                # Intergamma product sitemaps also contain known rental
                # entries under /r/.  Ignore those, but fail closed for any
                # other path family so a changed sitemap cannot look empty.
                raise RuntimeError(
                    f"{self.site}: product sitemap contains an unexpected URL path"
                )
        if len(product_urls) < self.minimum_sitemap_product_urls:
            raise RuntimeError(
                f"{self.site}: product sitemap contains only {len(product_urls)} valid "
                "product URLs; refusing to treat it as an authoritative empty catalogue"
            )
        candidates: list[str] = []
        for url in product_urls:
            slug = urlsplit(url).path.rsplit("/p/", 1)[0].rsplit("/", 1)[-1]
            if self.is_portable_airco(slug.replace("-", " ")):
                candidates.append(url)
        return candidates

    def is_portable_airco(self, name: str) -> bool:
        return _is_portable_airco(name)

    def parse(self, soup: BeautifulSoup, page_url: str) -> list[Product]:
        products: dict[str, Product] = {}
        for card in soup.select("article.js-product-tile"):
            link = card.select_one("a.click-mask[href]")
            if link is None:
                continue
            name = str(link.get("title", "")).strip()
            if not self.is_portable_airco(name):
                continue
            href = str(link.get("href", ""))
            state = str(card.get("data-state", "")).strip().upper()
            available = state == "ONLINE_AVAILABLE"
            price = card.select_one('[itemprop="price"][content]')
            try:
                price_eur = float(str(price.get("content"))) if price is not None else None
            except ValueError:
                price_eur = None
            text = clean_text(card)
            url = canonical_url(page_url, href)
            products[url] = Product(
                site=self.site,
                name=name,
                url=url,
                available=available,
                price_eur=price_eur,
                delivery=_delivery(state),
                btu=parse_btu(text) or parse_btu(name) or parse_watt_rating_btu(name),
            )
        return list(products.values())


class GammaAdapter(DiyStoreAdapter):
    site = "GAMMA"
    urls = (
        "https://www.gamma.nl/assortiment/l/"
        "verwarming-isolatie-ventilatie/airco-ventilatoren/airco",
    )
    algolia_index = "prd_products_ms_gamma_nl"
    algolia_category_facet = (
        "/verwarming-isolatie-ventilatie/airco-ventilatoren/airco"
    )
    product_sitemap_url = "https://sitemap.gamma.nl/product.xml"
    product_sitemap_host = "www.gamma.nl"

    def is_portable_airco(self, name: str) -> bool:
        return _is_portable_airco(
            name,
            extra_terms=("draagbare airco", "draagbare airconditioner"),
        )


class KarweiAdapter(DiyStoreAdapter):
    site = "KARWEI"
    urls = ("https://www.karwei.nl/assortiment/l/ventilatie-verwarming/airco",)
    algolia_index = "prd_products_ms_karwei_nl"
    algolia_category_facet = "/ventilatie-verwarming/airco"
    product_sitemap_url = "https://sitemap.karwei.nl/product.xml"
    product_sitemap_host = "www.karwei.nl"


def _is_portable_airco(name: str, *, extra_terms: tuple[str, ...] = ()) -> bool:
    lower = name.lower()
    accessory_terms = (
        "aircooler",
        "luchtkoeler",
        "ventilator",
        "raamafdichting",
        "afvoer",
        "slang",
    )
    if any(term in lower for term in accessory_terms):
        return False
    portable_split_terms = (
        "portasplit",
        "porta split",
        "qsplitmini",
        "qsplit mini",
    )
    if any(term in lower for term in portable_split_terms):
        return True
    if any(term in lower for term in ("split airco", "split-unit", "monoblock")):
        return False
    return any(
        term in lower
        for term in ("mobiele airco", "mobiele airconditioner", *extra_terms)
    )


def _delivery(state: str) -> str:
    return {
        "ONLINE_AVAILABLE": "Online beschikbaar",
        "HAS_STORE_STOCK": "Alleen in de bouwmarkt",
        "CLICK_AND_COLLECT": "Alleen afhalen",
        "HAS_NO_ONLINE_AND_STORE_STOCK": "Niet beschikbaar",
    }.get(state, "Niet online beschikbaar")


def _has_supported_product_tiles(soup: BeautifulSoup) -> bool:
    cards = soup.select("article.js-product-tile")
    return bool(cards) and all(
        card.get("data-state")
        and (link := card.select_one("a.click-mask[href]")) is not None
        and str(link.get("title", "")).strip()
        for card in cards
    )


def _is_rate_limited(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    return bool(
        getattr(response, "status_code", None) == 429
        or "429" in str(exc)
        or "too many 429" in str(exc).casefold()
    )


def _validated_catalog_hits(site: str, payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise RuntimeError(f"{site}: public catalogue returned a non-object response")
    hits = payload.get("hits")
    nb_hits = payload.get("nbHits")
    if (
        not isinstance(hits, list)
        or isinstance(nb_hits, bool)
        or not isinstance(nb_hits, int)
        or nb_hits < 0
        or nb_hits > len(hits)
        or not hits
        or nb_hits == 0
        or not isinstance(payload.get("processingTimeMS"), int)
    ):
        raise RuntimeError(f"{site}: public catalogue response contract changed")
    for hit in hits:
        if (
            not isinstance(hit, dict)
            or not isinstance(hit.get("name"), str)
            or not hit["name"].strip()
            or not isinstance(hit.get("url"), str)
            or not hit["url"].strip()
        ):
            raise RuntimeError(f"{site}: public catalogue product contract changed")
    return hits


def _catalog_online_available(site: str, hit: dict[str, Any]) -> bool:
    availability = hit.get("availability")
    stock_quantity = hit.get("stockQuantity")
    boolean_fields = (
        hit.get("purchasableOnline"),
        hit.get("temporaryOutOfStock"),
        hit.get("hasStock"),
    )
    if (
        any(not isinstance(value, bool) for value in boolean_fields)
        or isinstance(stock_quantity, bool)
        or not isinstance(stock_quantity, (int, float))
        or not isinstance(availability, list)
        or any(not isinstance(value, str) for value in availability)
    ):
        raise RuntimeError(f"{site}: public catalogue stock contract changed")
    return bool(
        hit["purchasableOnline"]
        and not hit["temporaryOutOfStock"]
        and hit["hasStock"]
        and stock_quantity > 0
        and "Online te koop" in availability
    )
