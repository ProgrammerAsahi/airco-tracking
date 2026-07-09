from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .azure_auth import default_azure_credential, table_endpoint_from_storage_url
from .config import Config
from .i18n import supported_lang


LOG = logging.getLogger(__name__)

_USER_PARTITION = "user"
_PAID_PLANS = {"weekly_basic", "weekly_priority", "monthly_basic", "monthly_priority"}
_ENTITLED_STATUSES = {"active", "canceled"}
_DEFAULT_USERS_TABLE = "users"


@dataclass(frozen=True)
class AlertRecipient:
    email: str
    language: str
    delivery_country: str | None = None


def load_alert_recipients(config: Config) -> list[AlertRecipient]:
    """Return users who should receive stock-alert emails.

    Production subscriptions live in the web app's Azure Table ``users`` table.
    Local development and emergency fallback still use the legacy ``EMAIL_TO``
    setting so the scraper remains runnable without the web app tables.
    """
    if config.azure_storage_account_url:
        try:
            recipients = _load_user_table_recipients(config)
            if recipients:
                return recipients
            LOG.info("No active subscriber recipients found in Azure users table")
            return []
        except Exception as exc:
            LOG.warning("Cannot load subscriber recipients from Azure users table: %s", exc)

    if config.email_to:
        return [
            AlertRecipient(
                email=config.email_to,
                language=config.email_lang if supported_lang(config.email_lang) else "zh",
                delivery_country=None,
            )
        ]
    return []


def _load_user_table_recipients(config: Config) -> list[AlertRecipient]:
    from azure.data.tables import TableClient

    table_name = _users_table_name(config)
    table = TableClient(
        endpoint=table_endpoint_from_storage_url(config.azure_storage_account_url),
        table_name=table_name,
        credential=default_azure_credential(),
    )
    recipients: dict[str, AlertRecipient] = {}
    for entity in table.query_entities(f"PartitionKey eq '{_USER_PARTITION}'"):
        recipient = _recipient_from_entity(entity, fallback_lang=config.email_lang)
        if recipient is not None:
            recipients[recipient.email] = recipient
    return list(recipients.values())


def _users_table_name(config: Config) -> str:
    return config.auth_users_table or _DEFAULT_USERS_TABLE


def _recipient_from_entity(entity: dict[str, Any], *, fallback_lang: str) -> AlertRecipient | None:
    email = str(entity.get("email") or "").strip().lower()
    if "@" not in email:
        return None
    if not _has_email_alert_entitlement(entity):
        return None
    language = str(entity.get("languagePreference") or fallback_lang or "zh").strip().lower()
    if not supported_lang(language):
        language = "zh"
    delivery_country = str(entity.get("deliveryCountry") or "").strip().lower() or None
    return AlertRecipient(email=email, language=language, delivery_country=delivery_country)


def _has_email_alert_entitlement(entity: dict[str, Any]) -> bool:
    plan = str(entity.get("subscriptionPlan") or "").strip()
    status = str(entity.get("subscriptionStatus") or "").strip()
    if plan not in _PAID_PLANS or status not in _ENTITLED_STATUSES:
        return False
    period_end = str(entity.get("subscriptionCurrentPeriodEnd") or "").strip()
    if not period_end:
        return False
    try:
        expires_at = datetime.fromisoformat(period_end.replace("Z", "+00:00"))
    except ValueError:
        return False
    return expires_at > datetime.now(timezone.utc)
