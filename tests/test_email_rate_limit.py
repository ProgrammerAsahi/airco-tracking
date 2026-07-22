from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

from azure.core.exceptions import (
    ResourceExistsError,
    ResourceModifiedError,
    ResourceNotFoundError,
)

from airco_tracker.email_rate_limit import (
    AzureTableEmailRateLimiter,
    LocalEmailRateLimiter,
    build_email_rate_limiter,
)


class _Entity(dict):
    def __init__(self, values, etag: str) -> None:
        super().__init__(values)
        self.metadata = {"etag": etag}


class _Table:
    def __init__(self) -> None:
        self.values = None
        self.version = 0
        self.conflict_once = False

    def get_entity(self, partition_key, row_key):
        if self.values is None:
            raise ResourceNotFoundError("missing")
        self._check_keys(partition_key, row_key)
        return _Entity(dict(self.values), str(self.version))

    def create_entity(self, values):
        if self.values is not None:
            raise ResourceExistsError("exists")
        self.values = dict(values)
        self.version += 1

    def update_entity(self, values, *, etag, **_kwargs):
        if self.values is None:
            raise ResourceNotFoundError("missing")
        if self.conflict_once:
            self.conflict_once = False
            self.values["nextAllowedAtMs"] += 5000
            self.version += 1
            raise ResourceModifiedError("contended")
        if str(etag) != str(self.version):
            raise ResourceModifiedError("contended")
        self.values.update(values)
        self.version += 1

    @staticmethod
    def _check_keys(partition_key, row_key) -> None:
        if partition_key != "email" or row_key != "acs-global":
            raise AssertionError("unexpected rate-limit entity key")


class AzureTableEmailRateLimiterTests(unittest.TestCase):
    def test_separate_replicas_share_one_aggregate_send_schedule(self) -> None:
        table = _Table()
        now = [100.0]
        sleeps: list[float] = []

        def sleep(seconds: float) -> None:
            sleeps.append(seconds)
            now[0] += seconds

        first = AzureTableEmailRateLimiter(
            table=table,
            wall_time=lambda: now[0],
            sleep=sleep,
        )
        second = AzureTableEmailRateLimiter(
            table=table,
            wall_time=lambda: now[0],
            sleep=sleep,
        )

        first.wait(5)
        second.wait(5)

        self.assertEqual(sleeps, [5.0])
        self.assertEqual(table.values["nextAllowedAtMs"], 110000)

    def test_etag_conflict_reloads_and_reserves_after_competing_replica(self) -> None:
        table = _Table()
        now = [100.0]
        sleeps: list[float] = []

        def sleep(seconds: float) -> None:
            sleeps.append(seconds)
            now[0] += seconds

        limiter = AzureTableEmailRateLimiter(
            table=table,
            wall_time=lambda: now[0],
            sleep=sleep,
        )
        limiter.wait(5)
        table.conflict_once = True

        AzureTableEmailRateLimiter(
            table=table,
            wall_time=lambda: now[0],
            sleep=sleep,
        ).wait(5)

        self.assertEqual(sleeps, [10.0])
        self.assertEqual(table.values["nextAllowedAtMs"], 115000)

    def test_builder_defaults_to_local_and_rejects_unknown_backend(self) -> None:
        self.assertIsInstance(
            build_email_rate_limiter(SimpleNamespace()),
            LocalEmailRateLimiter,
        )
        with self.assertRaisesRegex(ValueError, "EMAIL_RATE_LIMIT_BACKEND"):
            build_email_rate_limiter(
                SimpleNamespace(email_rate_limit_backend="unknown")
            )

    def test_azure_template_wires_table_coordination_and_multi_replica_guard(self) -> None:
        root = Path(__file__).parents[1]
        foundation = (root / "infra" / "foundation.bicep").read_text(encoding="utf-8")
        job = (root / "infra" / "job.bicep").read_text(encoding="utf-8")

        self.assertIn("name: 'emailratelimit'", foundation)
        self.assertIn("scope: emailRateLimitTable", foundation)
        self.assertIn("EMAIL_RATE_LIMIT_BACKEND", job)
        self.assertIn("EMAIL_RATE_LIMIT_TABLE", job)
        self.assertIn(
            "emailRateLimitBackend == 'azure_table' ? emailMaxReplicas : 1",
            job,
        )
        self.assertIn("maxReplicas: safeEmailMaxReplicas", job)


if __name__ == "__main__":
    unittest.main()
