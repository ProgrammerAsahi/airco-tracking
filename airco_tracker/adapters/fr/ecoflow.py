from __future__ import annotations

import json
import math
import re
from typing import Any

from bs4 import BeautifulSoup, Tag

from ...models import Product
from ..base import Adapter, clean_text, is_presale_delivery, parse_btu, parse_cooling_watts_btu
from .common import is_real_air_conditioner_fr


_COLLECTION_URL = "https://fr.ecoflow.com/collections/wave-serie/products.json?limit=250"
_PRODUCT_URL = "https://fr.ecoflow.com/products/{handle}"
_WAVE_RE = re.compile(r"\bwave\s*[23]\b", re.I)
_MAX_COLLECTION_BYTES = 8 * 1024 * 1024


class EcoFlowFranceAdapter(Adapter):
    """Track EcoFlow WAVE variants from the store's first-party Shopify data.

    Awin remains the commercial relationship and can later supply affiliate
    deep links.  Stock truth deliberately comes from EcoFlow itself: the
    Shopify variant flag is fresher than a periodically imported affiliate
    feed, while the visible swatch copy is needed to distinguish pre-orders
    from genuinely sold-out bundles.
    """

    site = "EcoFlow France"
    urls = (_COLLECTION_URL,)

    def fetch_products(self) -> list[Product]:
        payload = _collection_payload(self.fetcher)

        raw_products = payload.get("products") if isinstance(payload, dict) else None
        if not isinstance(raw_products, list):
            raise RuntimeError("EcoFlow France: collection response has no product list")
        # Shopify's collection endpoint uses an explicit empty array for a
        # valid, currently empty collection. Treat that as a successful empty
        # snapshot so discontinued or seasonal WAVE stock does not remain
        # stale forever. A nonempty collection whose WAVE schema disappears
        # still fails closed below.
        if not raw_products:
            return []

        products: dict[str, Product] = {}
        for raw_product in raw_products:
            if not isinstance(raw_product, dict) or not _is_wave_air_conditioner(raw_product):
                continue
            handle = str(raw_product.get("handle") or "").strip()
            if not handle:
                raise RuntimeError("EcoFlow France: WAVE product is missing its handle")
            variants = raw_product.get("variants")
            if not isinstance(variants, list):
                raise RuntimeError("EcoFlow France: WAVE product has no variant list")
            if not variants:
                raise RuntimeError("EcoFlow France: WAVE product has an empty variant list")
            page_url = _PRODUCT_URL.format(handle=handle)
            preorder_copy = _variant_preorder_copy(self.fetcher.get(page_url))
            description = _product_description(raw_product)
            base_name = str(raw_product.get("title") or "EcoFlow WAVE").strip()
            btu = (
                _known_wave_btu(base_name)
                or parse_btu(description)
                or parse_cooling_watts_btu(description)
            )
            variant_keys: set[str] = set()
            matched_preorder_keys: set[str] = set()
            for variant in variants:
                if not isinstance(variant, dict):
                    raise RuntimeError("EcoFlow France: invalid WAVE variant record")
                variant_id = str(variant.get("id") or "").strip()
                variant_title = str(variant.get("title") or "").strip()
                # The parent product has already been proven to be a WAVE air
                # conditioner. Keep every purchasable variant, including a
                # future Shopify "Default Title" variant that may not repeat
                # the model name.
                if not variant_id or not variant_title:
                    raise RuntimeError("EcoFlow France: WAVE variant is missing identity fields")
                variant_key = _normalise_variant(variant_title)
                if variant_key in variant_keys:
                    raise RuntimeError("EcoFlow France: duplicate WAVE variant title")
                variant_keys.add(variant_key)
                raw_available = variant.get("available")
                if not isinstance(raw_available, bool):
                    raise RuntimeError(
                        "EcoFlow France: WAVE variant has invalid availability"
                    )
                price_eur = _variant_price(variant)
                delivery = preorder_copy.get(variant_key, "")
                if delivery:
                    matched_preorder_keys.add(variant_key)
                available = raw_available
                # Visible marketing copy alone cannot override Shopify's
                # authoritative sold-out flag. It only classifies an actually
                # orderable variant as presale rather than immediate stock.
                presale = bool(available and delivery and is_presale_delivery(delivery))
                url = f"{page_url}?variant={variant_id}"
                products[url] = Product(
                    site=self.site,
                    name=_variant_name(base_name, variant_title),
                    url=url,
                    available=available,
                    price_eur=price_eur,
                    delivery=delivery or ("En stock" if available else "Épuisé"),
                    btu=btu,
                    presale=presale,
                    country="fr",
                )

            if set(preorder_copy) != matched_preorder_keys:
                raise RuntimeError(
                    "EcoFlow France: preorder copy could not be mapped to variants"
                )

        if not products:
            raise RuntimeError("EcoFlow France: no WAVE air-conditioner variants found")
        return list(products.values())

    def parse(self, soup: BeautifulSoup, page_url: str) -> list[Product]:
        raise NotImplementedError("EcoFlow France uses Shopify JSON, not generic HTML parsing")


def _is_wave_air_conditioner(product: dict[str, Any]) -> bool:
    title = str(product.get("title") or "")
    description = _product_description(product)
    return bool(
        _WAVE_RE.search(f"{title} {product.get('handle', '')}")
        and is_real_air_conditioner_fr(title, description)
    )


def _collection_payload(fetcher: Any) -> Any:
    """Read the JSON endpoint while allowing Shopify's valid tiny empty body.

    The generic HTML fetcher rejects responses below 10 KiB, which is useful
    for detecting anti-bot shells but would also reject Shopify's legitimate
    ``{"products": []}`` off-season response. Production Fetcher instances
    expose their requests session, so use it with explicit JSON/content limits.
    Lightweight parser-test fetchers retain the ordinary ``get`` fallback.
    """
    session = getattr(fetcher, "session", None)
    if session is None:
        raw = fetcher.get(_COLLECTION_URL)
    else:
        response = session.get(
            _COLLECTION_URL,
            headers={"Accept": "application/json"},
            timeout=fetcher.timeout,
        )
        response.raise_for_status()
        content_type = str(response.headers.get("Content-Type") or "").casefold()
        if "application/json" not in content_type:
            raise RuntimeError("EcoFlow France: collection response is not JSON")
        if len(response.content) > _MAX_COLLECTION_BYTES:
            raise RuntimeError("EcoFlow France: collection response is unexpectedly large")
        try:
            raw = response.content.decode(response.encoding or "utf-8")
        except UnicodeDecodeError as exc:
            raise RuntimeError("EcoFlow France: invalid collection response") from exc
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise RuntimeError("EcoFlow France: invalid collection response") from exc


def _product_description(product: dict[str, Any]) -> str:
    raw = str(product.get("body_html") or product.get("description") or "")
    return clean_text(BeautifulSoup(raw, "html.parser"))


def _variant_preorder_copy(page: str) -> dict[str, str]:
    soup = BeautifulSoup(page, "html.parser")
    result: dict[str, str] = {}
    for card in soup.select(".swatch-element"):
        context = clean_text(card)
        if not is_presale_delivery(context):
            continue
        title_node = card.select_one(".swatch-title")
        title = clean_text(title_node) if title_node else ""
        if not title:
            title = str(card.get("data-value") or "").strip()
        if not title:
            raise RuntimeError("EcoFlow France: preorder variant could not be identified")
        result[_normalise_variant(title)] = _preorder_sentence(context)

    # A changed storefront selector must not silently turn visible pre-orders
    # into immediate stock.  Failing the site keeps the previous successful
    # snapshot until the parser is updated.
    if _has_unmapped_visible_preorder_copy(soup):
        raise RuntimeError("EcoFlow France: preorder copy could not be mapped to variants")
    return result


def _has_unmapped_visible_preorder_copy(soup: BeautifulSoup) -> bool:
    """Detect visible preorder text outside the storefront selector we understand."""
    for node in soup.find_all(string=True):
        text = " ".join(str(node).split())
        parent = node.parent
        if (
            not text
            or not is_presale_delivery(text)
            or not isinstance(parent, Tag)
            or not _is_visible(parent)
        ):
            continue
        if "swatch-element" in (parent.get("class") or []):
            continue
        if parent.find_parent(class_="swatch-element") is not None:
            continue
        return True
    return False


def _is_visible(node: Tag) -> bool:
    for ancestor in (node, *node.parents):
        if not isinstance(ancestor, Tag):
            continue
        if ancestor.name in {"script", "style", "noscript", "template"}:
            return False
        if ancestor.has_attr("hidden"):
            return False
        if str(ancestor.get("aria-hidden") or "").casefold() == "true":
            return False
        style = re.sub(r"\s+", "", str(ancestor.get("style") or "").casefold())
        if "display:none" in style or "visibility:hidden" in style:
            return False
    return True


def _preorder_sentence(text: str) -> str:
    match = re.search(
        r"(Précommandez\s+dès\s+maintenant.{0,220}?(?:\d{4}[.]?|\)))",
        text,
        re.I,
    )
    return " ".join((match.group(1) if match else text).split())


def _normalise_variant(value: str) -> str:
    return " ".join(value.casefold().split())


def _variant_name(base_name: str, variant_title: str) -> str:
    if _normalise_variant(base_name) == _normalise_variant(variant_title):
        return base_name
    return f"{base_name} — {variant_title}"


def _variant_price(variant: dict[str, Any]) -> float:
    try:
        raw = variant["price"]
        if isinstance(raw, bool):
            raise TypeError
        if isinstance(raw, int):
            price = raw / 100
        elif isinstance(raw, str):
            normalised = raw.strip().replace("\u00a0", "").replace(" ", "")
            if not normalised:
                raise ValueError
            if "." in normalised or "," in normalised:
                price = float(normalised.replace(",", "."))
            else:
                price = int(normalised) / 100
        else:
            raise TypeError
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("EcoFlow France: WAVE variant has invalid price") from exc

    price = round(price, 2)
    if not math.isfinite(price) or price <= 0:
        raise RuntimeError("EcoFlow France: WAVE variant has invalid price")
    return price


def _known_wave_btu(name: str) -> int | None:
    if re.search(r"\bwave\s*3\b", name, re.I):
        return 6100
    if re.search(r"\bwave\s*2\b", name, re.I):
        return 5100
    return None
