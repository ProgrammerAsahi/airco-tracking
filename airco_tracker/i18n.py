"""Internationalisation for alert emails.

Three languages are supported: Chinese (``zh``), Dutch (``nl``) and English
(``en``). ``EMAIL_LANG`` selects which one is used at runtime; the default is
Chinese to match the project author's preference.

Each entry is keyed by a stable message identifier. ``translate`` looks up the
key for the requested language and applies ``str.format`` with any keyword
arguments, so callers pass values like ``count=len(products)``.
"""

from __future__ import annotations

from typing import Any

SUPPORTED_LANGS = ("zh", "nl", "en")
DEFAULT_LANG = "zh"

# Keys map to {lang: template}. Templates use str.format placeholders.
MESSAGES: dict[str, dict[str, str]] = {
    "subject_alert": {
        "zh": "🔥 {count} 台便携空调恢复库存",
        "nl": "🔥 {count} mobiele airco's weer op voorraad",
        "en": "🔥 {count} portable air conditioners back in stock",
    },
    "subject_test": {
        "zh": "[Airco tracker] 邮件测试成功",
        "nl": "[Airco tracker] testmail verzonden",
        "en": "[Airco tracker] test email sent",
    },
    "body_intro": {
        "zh": "检测到以下可配送到荷兰的便携空调：",
        "nl": "De volgende mobiele airco's kunnen weer naar een Nederlands adres worden bezorgd:",
        "en": "The following portable air conditioners can again be delivered to a Dutch address:",
    },
    "price_unknown": {
        "zh": "价格未知",
        "nl": "prijs onbekend",
        "en": "price unknown",
    },
    "view_link": {
        "zh": "立即查看并下单",
        "nl": "Nu bekijken en bestellen",
        "en": "View and order now",
    },
    "body_footer": {
        "zh": "库存变化很快，请在购买前再次确认价格和配送日期。",
        "nl": "De voorraad verandert snel; controleer prijs en leverdatum voordat je bestelt.",
        "en": "Stock changes quickly; please confirm the price and delivery date before buying.",
    },
    "html_title": {
        "zh": "便携空调恢复库存",
        "nl": "Mobiele airco's weer op voorraad",
        "en": "Portable air conditioners back in stock",
    },
    "test_body": {
        "zh": "Airco Tracker NL 邮件配置正常。",
        "nl": "De e-mailconfiguratie van Airco Tracker NL werkt naar behoren.",
        "en": "Airco Tracker NL email configuration is working.",
    },
    "test_body_html": {
        "zh": "<p><strong>Airco Tracker NL</strong> 邮件配置正常。</p>",
        "nl": "<p><strong>Airco Tracker NL</strong>-e-mailconfiguratie werkt naar behoren.</p>",
        "en": "<p><strong>Airco Tracker NL</strong> email configuration is working.</p>",
    },
    "delivery_in_stock": {
        "zh": "页面显示可购买",
        "nl": "Bestelbaar volgens de pagina",
        "en": "Orderable according to the page",
    },
    "delivery_out_of_stock": {
        "zh": "已售罄",
        "nl": "Uitverkocht",
        "en": "Sold out",
    },
}


def supported_lang(lang: str) -> bool:
    return lang in SUPPORTED_LANGS


def translate(lang: str, key: str, **kwargs: Any) -> str:
    """Return the localised template for ``key`` formatted with ``kwargs``.

    Falls back to the default language, then to the key itself, so a missing
    translation never crashes email delivery.
    """
    bundle = MESSAGES.get(key, {})
    template = bundle.get(lang) or bundle.get(DEFAULT_LANG) or key
    return template.format(**kwargs)
