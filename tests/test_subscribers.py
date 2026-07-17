from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from airco_tracker.subscribers import _recipient_from_entity


class SubscriberTests(unittest.TestCase):
    def test_active_legacy_paid_user_is_alert_recipient(self) -> None:
        future = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        recipient = _recipient_from_entity(
            {
                "email": "User@Example.com",
                "subscriptionPlan": "weekly_basic",
                "subscriptionStatus": "active",
                "subscriptionCurrentPeriodEnd": future,
                "languagePreference": "en",
                "deliveryCountry": "fr",
            },
            fallback_lang="zh",
        )
        self.assertIsNotNone(recipient)
        assert recipient is not None
        self.assertEqual(recipient.email, "user@example.com")
        self.assertEqual(recipient.language, "en")
        self.assertEqual(recipient.delivery_country, "fr")

    def test_canceled_legacy_subscription_keeps_alerts_until_period_end(self) -> None:
        future = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        recipient = _recipient_from_entity(
            {
                "email": "legacy-canceled@example.com",
                "subscriptionPlan": "monthly_priority",
                "subscriptionStatus": "canceled",
                "subscriptionCurrentPeriodEnd": future,
            },
            fallback_lang="en",
        )

        self.assertIsNotNone(recipient)

    def test_expired_or_unsubscribed_user_is_not_recipient(self) -> None:
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        self.assertIsNone(
            _recipient_from_entity(
                {
                    "email": "expired@example.com",
                    "subscriptionPlan": "monthly_priority",
                    "subscriptionStatus": "active",
                    "subscriptionCurrentPeriodEnd": past,
                },
                fallback_lang="zh",
            )
        )

    def test_explicit_email_alert_opt_out_is_not_recipient_but_legacy_is(self) -> None:
        future = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        base = {
            "email": "user@example.com",
            "subscriptionPlan": "weekly_basic",
            "subscriptionStatus": "active",
            "subscriptionCurrentPeriodEnd": future,
        }
        self.assertIsNotNone(_recipient_from_entity(base, fallback_lang="en"))
        self.assertIsNone(
            _recipient_from_entity(
                {**base, "emailAlertsEnabled": False},
                fallback_lang="en",
            )
        )
        self.assertIsNone(
            _recipient_from_entity(
                {
                    "email": "free@example.com",
                    "subscriptionPlan": "none",
                    "subscriptionStatus": "none",
                },
                fallback_lang="zh",
            )
        )

    def test_french_preference_is_preserved_for_alert_delivery(self) -> None:
        future = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        recipient = _recipient_from_entity(
            {
                "email": "user@example.com",
                "entitlementTier": "alerts",
                "entitlementStatus": "active",
                "entitlementExpiresAt": future,
                "languagePreference": "fr",
                "deliveryCountry": "fr",
            },
            fallback_lang="zh",
        )

        self.assertIsNotNone(recipient)
        assert recipient is not None
        self.assertEqual(recipient.language, "fr")
        self.assertEqual(recipient.delivery_country, "fr")

    def test_both_active_pass_tiers_receive_alerts(self) -> None:
        future = (datetime.now(timezone.utc) + timedelta(days=90)).isoformat()
        for tier in ("alerts", "radar"):
            with self.subTest(tier=tier):
                recipient = _recipient_from_entity(
                    {
                        "email": f"{tier}@example.com",
                        "entitlementTier": tier,
                        "entitlementStatus": "active",
                        "entitlementExpiresAt": future,
                    },
                    fallback_lang="en",
                )
                self.assertIsNotNone(recipient)

    def test_expired_refunded_and_revoked_passes_do_not_receive_alerts(self) -> None:
        future = (datetime.now(timezone.utc) + timedelta(days=90)).isoformat()
        past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        cases = (
            ("expired", future),
            ("refunded", future),
            ("revoked", future),
            ("active", past),
        )
        for status, expires_at in cases:
            with self.subTest(status=status, expires_at=expires_at):
                self.assertIsNone(
                    _recipient_from_entity(
                        {
                            "email": "inactive@example.com",
                            "entitlementTier": "radar",
                            "entitlementStatus": status,
                            "entitlementExpiresAt": expires_at,
                        },
                        fallback_lang="en",
                    )
                )

    def test_pass_fields_override_stale_legacy_subscription_fields(self) -> None:
        future = (datetime.now(timezone.utc) + timedelta(days=90)).isoformat()
        self.assertIsNone(
            _recipient_from_entity(
                {
                    "email": "refunded@example.com",
                    "entitlementTier": "radar",
                    "entitlementStatus": "refunded",
                    "entitlementExpiresAt": future,
                    "subscriptionPlan": "monthly_priority",
                    "subscriptionStatus": "active",
                    "subscriptionCurrentPeriodEnd": future,
                },
                fallback_lang="en",
            )
        )


if __name__ == "__main__":
    unittest.main()
