from __future__ import annotations


REGION_DELIVERY_COUNTRIES = {
    "eu": {
        "at", "be", "bg", "hr", "cy", "cz", "dk", "ee", "fi", "fr", "de",
        "gr", "hu", "ie", "it", "lv", "lt", "lu", "mt", "nl", "pl", "pt",
        "ro", "sk", "si", "es", "se",
    },
    "eea": {
        "at", "be", "bg", "hr", "cy", "cz", "dk", "ee", "fi", "fr", "de",
        "gr", "hu", "ie", "it", "lv", "lt", "lu", "mt", "nl", "pl", "pt",
        "ro", "sk", "si", "es", "se", "is", "li", "no",
    },
    "benelux": {"be", "lu", "nl"},
    "dach": {"at", "ch", "de"},
    "nordics": {"dk", "fi", "is", "no", "se"},
}


def coverage_reaches_country(coverage: set[str] | frozenset[str] | tuple[str, ...], country: str | None) -> bool:
    normalized = (country or "").strip().lower()
    if not normalized:
        return True
    tokens = {str(value).strip().lower() for value in coverage}
    if normalized in tokens:
        return True
    return any(normalized in REGION_DELIVERY_COUNTRIES.get(token, set()) for token in tokens)
