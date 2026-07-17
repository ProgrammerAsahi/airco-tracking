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
_PASS_TIERS = {"alerts", "radar"}
_LEGACY_PLAN_TIERS = {
    "weekly_basic": "alerts",
    "monthly_basic": "alerts",
    "weekly_priority": "radar",
    "monthly_priority": "radar",
}
_LEGACY_ENTITLED_STATUSES = {"active", "canceled"}
_DEFAULT_USERS_TABLE = "users"


@dataclass(frozen=True)
class AlertRecipient:
    email: str
    language: str
    delivery_country: str | None = None


def load_alert_recipients(config: Config) -> list[AlertRecipient]:
    """Return users who should receive stock-alert emails.

    Production pass entitlements live in the web app's Azure Table ``users`` table.
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
            if getattr(config, "app_env", "local") == "azure":
                raise RuntimeError(
                    "Cannot load subscriber recipients from Azure users table; refusing legacy fallback"
                ) from exc
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
    if not has_email_alert_entitlement(entity):
        return None
    language = str(entity.get("languagePreference") or fallback_lang or "zh").strip().lower()
    if not supported_lang(language):
        language = "zh"
    delivery_country = str(entity.get("deliveryCountry") or "").strip().lower() or None
    return AlertRecipient(email=email, language=language, delivery_country=delivery_country)


def has_email_alert_entitlement(entity: dict[str, Any], *, now: datetime | None = None) -> bool:
    # Missing means enabled for profiles created before the preference was
    # introduced. An explicit false is an independent opt-out and never
    # changes the user's paid pass or realtime inventory access.
    if entity.get("emailAlertsEnabled") is False:
        return False
    tier, status, expires_at_value = email_alert_entitlement_fields(entity)
    if tier not in _PASS_TIERS or status != "active":
        return False
    if not expires_at_value:
        return False
    try:
        expires_at = datetime.fromisoformat(expires_at_value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return expires_at > reference


def email_alert_entitlement_fields(entity: dict[str, Any]) -> tuple[str, str, str]:
    """Return the normalized alert entitlement stored on a user/projection row.

    The 90-day pass fields are authoritative as soon as any one of them exists.
    This intentionally fails closed for a partial pass write and prevents a
    revoked/refunded pass from falling back to stale recurring-subscription
    fields that may remain during the migration window.

    Rows created before the pass migration remain readable. Their basic and
    priority plans map to ``alerts`` and ``radar`` respectively, and a canceled
    subscription remains active only until its recorded period end.
    """
    pass_fields = ("entitlementTier", "entitlementStatus", "entitlementExpiresAt")
    if any(field in entity for field in pass_fields):
        return (
            str(entity.get("entitlementTier") or "none").strip().lower(),
            str(entity.get("entitlementStatus") or "none").strip().lower(),
            str(entity.get("entitlementExpiresAt") or "").strip(),
        )

    plan = str(entity.get("subscriptionPlan") or entity.get("plan") or "").strip().lower()
    legacy_status = str(
        entity.get("subscriptionStatus") or entity.get("status") or ""
    ).strip().lower()
    period_end = str(
        entity.get("subscriptionCurrentPeriodEnd")
        or entity.get("currentPeriodEnd")
        or entity.get("entitlementEnd")
        or ""
    ).strip()
    tier = _LEGACY_PLAN_TIERS.get(plan, "none")
    status = "active" if legacy_status in _LEGACY_ENTITLED_STATUSES else "none"
    return tier, status, period_end
