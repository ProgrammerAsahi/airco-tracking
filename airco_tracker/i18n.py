"""Internationalisation for alert emails.

Four languages are supported: Chinese (``zh``), Dutch (``nl``), English
(``en``) and French (``fr``). ``EMAIL_LANG`` selects which one is used at
runtime; the default is Chinese to match the project author's preference.

Translations are loaded from Azure Table Storage (scope="email") when
configured, with a bundled JSON file as fallback. See ``i18n_table.py``.
"""

from __future__ import annotations

from typing import Any

from .i18n_table import load_translations

SUPPORTED_LANGS = ("zh", "nl", "en", "fr")
DEFAULT_LANG = "zh"


def _messages() -> dict[str, dict[str, str]]:
    """Return the email-scoped translations, cached by i18n_table."""
    return load_translations("email")


def supported_lang(lang: str) -> bool:
    return lang in SUPPORTED_LANGS


def translate(lang: str, key: str, **kwargs: Any) -> str:
    """Return the localised template for ``key`` formatted with ``kwargs``.

    Falls back to the default language, then to the key itself, so a missing
    translation never crashes email delivery.
    """
    bundle = _messages().get(key, {})
    template = bundle.get(lang) or bundle.get(DEFAULT_LANG) or key
    return template.format(**kwargs)
