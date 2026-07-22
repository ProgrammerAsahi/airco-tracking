from __future__ import annotations

import json
import logging
import os
from dataclasses import replace
from datetime import timedelta
from typing import Any
from urllib.parse import urlencode, urlsplit

from ...awin import AwinLinkBuilderClient
from ...fetch import Fetcher
from ...models import Product
from ...partner_feed_store import build_partner_feed_cache
from ..base import is_presale_delivery, parse_btu, parse_cooling_watts_btu
from .common import is_real_air_conditioner_fr, parse_float


LOG = logging.getLogger(__name__)

_AWIN_ADVERTISER_ID = "62319"
_AWIN_PUBLISHER_ID = "2981827"
_AWIN_CACHE_NAMESPACE = "awin-trotec-fr-links-v1"
_AWIN_CACHE_KEY = "links"
_AWIN_CACHE_TTL = timedelta(days=1)


class TrotecFranceAdapter:
    site = "Trotec France"
    search_url = "https://fr.trotec.com/shop/catalogsearch/result/?q=climatiseur"
    query_url = "https://{app_id}-dsn.algolia.net/1/indexes/*/queries"

    def __init__(
        self,
        fetcher: Fetcher,
        *,
        awin_client: AwinLinkBuilderClient | None = None,
    ) -> None:
        self.fetcher = fetcher
        self._awin_client = awin_client

    def fetch_products(self) -> list[Product]:
        config = _algolia_config(self.fetcher.get(self.search_url))
        app_id = _required_string(config, "applicationId")
        api_key = _required_string(config, "apiKey")
        index_name = _required_string(config, "baseIndexName") + "_products"
        params = urlencode({"query": "climatiseur", "hitsPerPage": 150, "page": 0})
        payload = self.fetcher.request_json(
            "POST",
            self.query_url.format(app_id=app_id.lower()),
            headers={
                "Content-Type": "application/json",
                "X-Algolia-Application-Id": app_id,
                "X-Algolia-API-Key": api_key,
            },
            json_body={"requests": [{"indexName": index_name, "params": params}]},
            # Algolia queries are read-only despite using POST.
            retry_read_only_post=True,
            maximum_response_bytes=4 * 1024 * 1024,
        )
        try:
            hits = payload["results"][0]["hits"]
        except (KeyError, TypeError, IndexError):
            raise RuntimeError("Trotec France search returned an invalid response")
        products: dict[str, Product] = {}
        for hit in hits:
            product = _parse_hit(hit)
            if product is not None:
                products[product.url] = product
        if not products:
            raise RuntimeError("Trotec France search returned no air conditioners")
        # Complete first-party discovery and stock classification before the
        # optional partner call. Only an API-generated, validated Awin link is
        # attached; failures keep the canonical Trotec URL as purchase target.
        awin_links = self._awin_links(products)
        return [
            replace(product, affiliate_url=awin_links.get(product.url))
            for product in products.values()
        ]

    def _awin_links(self, products: dict[str, Product]) -> dict[str, str]:
        try:
            client = self._awin_client or _build_awin_client(self.fetcher)
            return client.links_for(products) if client is not None else {}
        except Exception:
            # Link generation must never make first-party inventory stale.
            # Keep this log generic because the API token is a credential.
            LOG.warning(
                "Trotec France Awin links are unavailable; using canonical URLs"
            )
            return {}


def _build_awin_client(fetcher: Fetcher) -> AwinLinkBuilderClient | None:
    bearer_token = os.getenv("AWIN_PUBLISHER_API_TOKEN", "").strip()
    if not bearer_token:
        return None

    common: dict[str, Any] = {
        "fetcher": fetcher,
        "cache": build_partner_feed_cache(),
        "cache_namespace": _AWIN_CACHE_NAMESPACE,
        "cache_key": _AWIN_CACHE_KEY,
        "ttl": _AWIN_CACHE_TTL,
        "timeout": min(fetcher.timeout, 10),
    }
    return AwinLinkBuilderClient(
        publisher_id=_AWIN_PUBLISHER_ID,
        advertiser_id=_AWIN_ADVERTISER_ID,
        bearer_token=bearer_token,
        **common,
    )


def _algolia_config(page: str) -> dict[str, Any]:
    marker = "window.algoliaConfig = "
    start = page.find(marker)
    if start == -1:
        raise RuntimeError("Trotec France page did not contain public search settings")
    start += len(marker)
    end = _json_object_end(page, start)
    try:
        data = json.loads(page[start:end])
    except json.JSONDecodeError as exc:
        raise RuntimeError("Trotec France search settings were invalid") from exc
    if not isinstance(data, dict):
        raise RuntimeError("Trotec France search settings were invalid")
    return data


def _json_object_end(text: str, start: int) -> int:
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index + 1
    raise RuntimeError("Trotec France search settings were incomplete")


def _parse_hit(
    hit: dict[str, Any],
) -> Product | None:
    if not isinstance(hit, dict):
        return None
    name = str(hit.get("name", "")).strip()
    url = str(hit.get("url", "")).strip()
    details = _details(hit)
    if not name or not url or not _is_trotec_air_conditioner(name, details):
        return None
    _validate_trotec_url(url)
    status = str(hit.get("availability_status", "")).strip()
    lower_status = status.casefold()
    has_immediate_status = lower_status in {"en stock", "stock limité", "stock limite"}
    has_presale_status = is_presale_delivery(status)
    has_unavailable_status = lower_status in {
        "actuellement indisponible",
        "délai de livraison sur demande",
        "delai de livraison sur demande",
    }
    if not (has_immediate_status or has_presale_status or has_unavailable_status):
        raise RuntimeError("Trotec France product has an invalid availability status")
    sold_out = _sold_out(hit.get("sold_out"))
    if sold_out is None:
        raise RuntimeError("Trotec France product has an invalid sold_out signal")
    immediate = sold_out is False and has_immediate_status
    presale = sold_out is False and has_presale_status
    return Product(
        site="Trotec France",
        name=name,
        url=url,
        available=immediate or presale,
        price_eur=_price(hit.get("price")),
        delivery=status or None,
        btu=parse_btu(f"{name} {details}") or parse_cooling_watts_btu(details),
        presale=presale,
        country="fr",
    )


def _sold_out(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if not isinstance(value, str):
        return None
    key = value.strip().casefold()
    if key in {"oui", "yes", "true", "1"}:
        return True
    if key in {"non", "no", "false", "0"}:
        return False
    return None


def _validate_trotec_url(value: str) -> None:
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise RuntimeError("Trotec France product has an invalid URL")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise RuntimeError("Trotec France product has an invalid URL") from exc
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").lower() != "fr.trotec.com"
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or not parsed.path.startswith("/shop/")
    ):
        raise RuntimeError("Trotec France product has an invalid URL")


def _is_trotec_air_conditioner(name: str, details: str) -> bool:
    name_lower = name.casefold()
    details_lower = details.casefold()
    if not is_real_air_conditioner_fr(name, details):
        return False
    # Trotec's Algolia result set also contains accessories and spare parts
    # whose category path includes "Climatiseur mobile". Require the product
    # name itself to be an air conditioner; category text alone is not enough.
    if (
        "climatiseur" not in name_lower
        and "appareil de climatisation local" not in name_lower
    ):
        return False
    # Trotec also indexes fixed industrial equipment.  The portable PAC-S
    # split remains valid because its category is explicitly mobile; the
    # professional/industrial category is not.
    if "climatisation professionnelle et industrielle" in details_lower:
        return False
    if (
        "appareil de climatisation local" in name_lower
        and "climatiseur mobile" not in details_lower
    ):
        return False
    accessory_prefixes = (
        "adaptateur",
        "airlock",
        "anneaux",
        "bouchon",
        "buse",
        "câble",
        "cable",
        "capteur",
        "collier",
        "échangeur",
        "echangeur",
        "passage",
        "panneau",
        "rail",
        "rallonge",
        "roue",
        "tuyau",
    )
    return not name_lower.startswith(accessory_prefixes)


def _details(hit: dict[str, Any]) -> str:
    values: list[str] = []
    for key in (
        "main_characteristic_1_value",
        "main_characteristic_2_value",
        "main_characteristic_3_value",
        "cooling_capacity_range_idx",
    ):
        value = hit.get(key)
        if value:
            values.append(str(value))
    categories = hit.get("categories_without_path")
    if isinstance(categories, list):
        values.extend(str(item) for item in categories)
    return " ".join(values)


def _price(value: Any) -> float | None:
    try:
        return parse_float(value["EUR"]["default"])
    except (KeyError, TypeError):
        return None


def _required_string(mapping: dict[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"Trotec France search settings did not contain {key}")
    return value
