from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from airco_tracker.subscribers import _recipient_from_entity


class SubscriberTests(unittest.TestCase):
    def test_active_paid_user_is_alert_recipient(self) -> None:
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
                "subscriptionPlan": "monthly_basic",
                "subscriptionStatus": "active",
                "subscriptionCurrentPeriodEnd": future,
                "languagePreference": "fr",
                "deliveryCountry": "fr",
            },
            fallback_lang="zh",
        )

        self.assertIsNotNone(recipient)
        assert recipient is not None
        self.assertEqual(recipient.language, "fr")
        self.assertEqual(recipient.delivery_country, "fr")


if __name__ == "__main__":
    unittest.main()
