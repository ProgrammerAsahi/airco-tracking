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


if __name__ == "__main__":
    unittest.main()
