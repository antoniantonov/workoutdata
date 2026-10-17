"""Unit tests for the pure delta planner and typed window models.

These tests use only the standard library (no pandas / database / network) so
they can run in isolation and cheaply guard the core set-difference semantics.
"""
from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from garmin_etl.delta import (  # noqa: E402
    Backend,
    DestinationInventory,
    RemoteInventory,
    SyncPlan,
    Window,
    compute_network_downloads,
    compute_plan,
    compute_target_delta,
    selected_backends,
    validate_look_back_days,
)

UTC = timezone.utc


class ValidateLookBackDaysTests(unittest.TestCase):
    def test_accepts_positive_int_float_and_str(self) -> None:
        self.assertEqual(validate_look_back_days(30), 30)
        self.assertEqual(validate_look_back_days(7.0), 7)
        self.assertEqual(validate_look_back_days("60"), 60)

    def test_rejects_invalid_values(self) -> None:
        for bad in (None, 0, -1, 1.5, "0", "-1", "1.5", "2e3", "many", "", True, False):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    validate_look_back_days(bad)


class SelectedBackendsTests(unittest.TestCase):
    def test_mapping(self) -> None:
        self.assertEqual(selected_backends("postgres"), (Backend.POSTGRES,))
        self.assertEqual(selected_backends("duckdb"), (Backend.DUCKDB,))
        self.assertEqual(
            selected_backends("both"), (Backend.DUCKDB, Backend.POSTGRES)
        )

    def test_rejects_unknown(self) -> None:
        with self.assertRaises(ValueError):
            selected_backends("sqlite")


class WindowTests(unittest.TestCase):
    def test_thirty_day_default_bounds(self) -> None:
        now = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
        window = Window.create(30, now)
        self.assertEqual(window.end_utc, now)
        self.assertEqual(window.start_utc, now - timedelta(days=30))
        self.assertEqual(window.look_back_days, 30)

    def test_override_changes_window(self) -> None:
        now = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
        self.assertEqual(
            Window.create(7, now).start_utc, now - timedelta(days=7)
        )
        self.assertEqual(
            Window.create(60, now).start_utc, now - timedelta(days=60)
        )

    def test_boundaries_inclusive_and_future_excluded(self) -> None:
        now = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
        window = Window.create(30, now)
        self.assertTrue(window.contains_activity(window.start_utc))  # exact cutoff
        self.assertTrue(window.contains_activity(window.end_utc))  # exact end
        self.assertFalse(
            window.contains_activity(window.start_utc - timedelta(seconds=1))
        )
        self.assertFalse(
            window.contains_activity(window.end_utc + timedelta(seconds=1))
        )  # future

    def test_contains_activity_rejects_naive(self) -> None:
        window = Window.create(30, datetime(2026, 9, 20, 12, 0, tzinfo=UTC))
        with self.assertRaises(ValueError):
            window.contains_activity(datetime(2026, 9, 10, 12, 0))

    def test_contains_activity_converts_non_utc_zone(self) -> None:
        now = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
        window = Window.create(30, now)
        # 2026-09-10 08:00 UTC expressed in a +02:00 zone is still inside.
        other = datetime(2026, 9, 10, 10, 0, tzinfo=timezone(timedelta(hours=2)))
        self.assertTrue(window.contains_activity(other))

    def test_rejects_naive_now(self) -> None:
        with self.assertRaises(ValueError):
            Window.create(30, datetime(2026, 9, 20, 12, 0))

    def test_sleep_dates_touch_n_plus_one_dates(self) -> None:
        now = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
        window = Window.create(2, now)
        # 2026-09-18 .. 2026-09-20 inclusive = 3 dates for a 2-day window.
        self.assertEqual(
            window.sleep_dates(),
            (date(2026, 9, 18), date(2026, 9, 19), date(2026, 9, 20)),
        )

    def test_query_dates_have_guard_band(self) -> None:
        now = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
        window = Window.create(30, now)
        self.assertEqual(window.query_start_date, date(2026, 8, 20))  # -1 guard
        self.assertEqual(window.query_end_date, date(2026, 9, 21))  # +1 guard

    def test_invalid_timezone_rejected(self) -> None:
        with self.assertRaises(ValueError):
            Window.create(30, datetime(2026, 9, 20, tzinfo=UTC), "Not/AZone")

    def test_local_timezone_shifts_sleep_calendar(self) -> None:
        # Just after local midnight in a -05:00 zone the run day differs from UTC.
        now = datetime(2026, 9, 20, 3, 0, tzinfo=UTC)  # 2026-09-19 22:00 in -05:00
        window = Window.create(1, now, "America/New_York")
        self.assertEqual(window.sleep_dates()[-1], date(2026, 9, 19))


class DeltaComputationTests(unittest.TestCase):
    def _window(self) -> Window:
        return Window.create(30, datetime(2026, 9, 20, 12, 0, tzinfo=UTC))

    def test_single_target_missing_only(self) -> None:
        remote = RemoteInventory(frozenset({"A", "B", "C"}), frozenset())
        dest = DestinationInventory(Backend.POSTGRES, frozenset({"A", "B"}), frozenset())
        delta = compute_target_delta(Backend.POSTGRES, remote, dest)
        self.assertEqual(delta.missing_activity_ids, frozenset({"C"}))

    def test_union_of_deltas_example_from_plan(self) -> None:
        # R={A,B,C}; PostgreSQL has {A,B}; DuckDB has {A}.
        remote = RemoteInventory(frozenset({"A", "B", "C"}), frozenset())
        destinations = {
            Backend.POSTGRES: DestinationInventory(
                Backend.POSTGRES, frozenset({"A", "B"}), frozenset()
            ),
            Backend.DUCKDB: DestinationInventory(
                Backend.DUCKDB, frozenset({"A"}), frozenset()
            ),
        }
        plan = compute_plan(self._window(), remote, destinations)
        self.assertEqual(
            plan.target(Backend.POSTGRES).missing_activity_ids, frozenset({"C"})
        )
        self.assertEqual(
            plan.target(Backend.DUCKDB).missing_activity_ids, frozenset({"B", "C"})
        )
        # Acquire the union {B,C} once — not {C} (that would leave DuckDB missing B).
        self.assertEqual(plan.needed_activity_payloads, frozenset({"B", "C"}))
        self.assertFalse(plan.is_empty())

    def test_network_downloads_subtracts_verified_cache(self) -> None:
        needed = frozenset({"B", "C"})
        self.assertEqual(
            compute_network_downloads(needed, {"B"}), frozenset({"C"})
        )
        self.assertEqual(compute_network_downloads(needed, {"B", "C"}), frozenset())

    def test_no_op_when_inventory_present(self) -> None:
        remote = RemoteInventory(
            frozenset({"A", "B"}), frozenset({date(2026, 9, 19)})
        )
        destinations = {
            Backend.DUCKDB: DestinationInventory(
                Backend.DUCKDB, frozenset({"A", "B"}), frozenset({date(2026, 9, 19)})
            ),
        }
        plan = compute_plan(self._window(), remote, destinations)
        self.assertTrue(plan.is_empty())
        self.assertEqual(plan.needed_activity_payloads, frozenset())
        self.assertEqual(plan.needed_sleep_days, frozenset())

    def test_empty_destinations_rejected(self) -> None:
        remote = RemoteInventory(frozenset({"A"}), frozenset())
        with self.assertRaises(ValueError):
            compute_plan(self._window(), remote, {})

    def test_sleep_delta(self) -> None:
        days = frozenset({date(2026, 9, 18), date(2026, 9, 19), date(2026, 9, 20)})
        remote = RemoteInventory(frozenset(), days)
        dest = DestinationInventory(
            Backend.DUCKDB, frozenset(), frozenset({date(2026, 9, 18)})
        )
        delta = compute_target_delta(Backend.DUCKDB, remote, dest)
        self.assertEqual(
            delta.missing_sleep_days,
            frozenset({date(2026, 9, 19), date(2026, 9, 20)}),
        )

    def test_target_backend_mismatch_rejected(self) -> None:
        remote = RemoteInventory(frozenset({"A"}), frozenset())
        dest = DestinationInventory(Backend.DUCKDB, frozenset(), frozenset())
        with self.assertRaises(ValueError):
            compute_target_delta(Backend.POSTGRES, remote, dest)

    def test_plan_requires_targets(self) -> None:
        remote = RemoteInventory(frozenset(), frozenset())
        with self.assertRaises(ValueError):
            SyncPlan(self._window(), remote, tuple())


if __name__ == "__main__":
    unittest.main()
