from __future__ import annotations

import re
from typing import Any

from bs4 import Tag

from ..base import clean_text


PRICE_RE = re.compile(
    r"(?<![\d])(?P<euros>\d{1,3}(?:[\s\u00a0.]\d{3})*|\d{2,5})"
    r"(?:\s*,\s*(?P<cents>\d{2}))?\s*€",
    re.I,
)
BARE_DECIMAL_PRICE_RE = re.compile(r"(?<![\d])(?P<euros>\d{2,5})\s*,\s*(?P<cents>\d{2})(?!\d)")


def is_real_air_conditioner_fr(name: str, extra_text: str = "") -> bool:
    """Keep compressor air conditioners and reject coolers, fans and accessories."""
    name_lower = name.casefold()
    lower = f"{name} {extra_text}".casefold()
    hard_excluded = (
        "rafraîchisseur",
        "rafraichisseur",
        "refroidisseur d'air",
        "aircooler",
        "sans tuyau d'évacuation",
        "sans tuyau d’évacuation",
        "sans tuyau d'evacuation",
        "climatiseur mural",
        "climatiseur commercial",
        "climatiseurs professionnels",
        "climatiseur split ac",
        "autoradio",
        "carplay",
        "android auto",
        "gps pour",
    )
    if any(term in lower for term in hard_excluded):
        return False

    name_excluded = (
        "ventilateur",
        "kit ",
        "kit calfeutrage",
        "kit de fenêtre",
        "kit fenêtre",
        "kit fenetre",
        "kit d'évacuation",
        "kit d’évacuation",
        "kit d’evacuation",
        "tuyau d'évacuation",
        "tuyau d’evacuation",
        "tuyau de",
        "tuyau flexible",
        "gaine de climatiseur",
        "calfeutrage",
        "manchon",
        "filtre",
        "télécommande",
        "telecommande",
        "housse",
        "accessoire",
        "raccord",
        "gaz réfrigérant",
        "gaz refrigerant",
        "réfrigérant r290",
        "refrigerant r290",
        "désodorisant",
        "desodorisant",
    )
    if any(term in name_lower for term in name_excluded):
        return False
    return "climatiseur" in lower or "air condition" in lower


def parse_french_price(text: str, *, minimum: float = 20.0) -> float | None:
    """Parse French price snippets, preferring the current product price.

    Cards often include crossed-out prices, savings and monthly instalments in
    the same text. We skip obvious non-product amounts and return the last
    remaining candidate, which is normally the current price in French PLPs.
    """
    candidates: list[float] = []
    for match in PRICE_RE.finditer(text.replace("\u202f", " ")):
        start, end = match.span()
        before = text[max(0, start - 22) : start].casefold()
        after = text[end : min(len(text), end + 35)].casefold()
        context = f"{before} {after}"
        if "économisez" in before or "economisez" in before or "/mois" in after or "par mois" in after:
            continue
        if "ancien prix" in before or "prix d’origine" in before or "prix d'origine" in before:
            continue
        euros = int(re.sub(r"\D", "", match.group("euros")))
        cents = int(match.group("cents") or 0)
        value = euros + cents / 100
        if value >= minimum:
            candidates.append(value)
    if not candidates and "€" not in text:
        for match in BARE_DECIMAL_PRICE_RE.finditer(text.replace("\u202f", " ")):
            value = int(match.group("euros")) + int(match.group("cents")) / 100
            if value >= minimum:
                candidates.append(value)
    return candidates[-1] if candidates else None


def parse_float(value: Any) -> float | None:
    try:
        return round(float(str(value).replace("\u00a0", "").replace(" ", "").replace(",", ".")), 2)
    except (TypeError, ValueError):
        return None


def meta_price(scope: Tag) -> float | None:
    node = scope.select_one('[itemprop="price"][content]')
    return parse_float(node.get("content")) if node else None


def custom_fields(hit: dict[str, Any]) -> dict[str, str]:
    fields = hit.get("customFields")
    if not isinstance(fields, list):
        return {}
    result: dict[str, str] = {}
    for field in fields:
        if not isinstance(field, dict):
            continue
        key = str(field.get("key", "")).strip()
        if key:
            result[key] = str(field.get("value", "")).strip()
    return result


def first_text(scope: Tag, *selectors: str) -> str:
    for selector in selectors:
        node = scope.select_one(selector)
        if node:
            text = clean_text(node)
            if text:
                return text
    return ""
