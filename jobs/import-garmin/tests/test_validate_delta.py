"""Tests for delta-scope validation (presence, deep comparison, window parity)."""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from garmin_etl import transform, validate  # noqa: E402
from garmin_etl.storage import duckdb as store  # noqa: E402
from tests._garmin_sqlite import build_sqlite_cache  # noqa: E402


class DeltaValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        db_dir = root / "DBs"
        build_sqlite_cache(
            db_dir,
            activities=[("100", "2026-09-10 08:00:00"), ("101", "2026-09-11 09:00:00")],
            records=[("100", 0), ("100", 1), ("101", 0)],
            sleeps=["2026-09-18"],
        )
        self.config = {
            "GARMIN_DB_DIR": str(db_dir),
            "GARMIN_DUCKDB_PATH": str(root / "garmin.duckdb"),
        }
        self.activity_ids = ["100", "101"]
        self.sleep_days = [date(2026, 9, 18)]
        self.expected = transform.transform_selected(
            self.config, self.activity_ids, ["2026-09-18"]
        )
        store.import_workouts(
            self.expected["workouts"], self.expected["timeseries"], self.config
        )
        store.import_sleep(self.expected["sleep"], self.config)

    def test_presence_and_deep_pass(self):
        validate.validate_delta(
            self.config, "duckdb", self.expected, self.activity_ids, self.sleep_days,
            deep=True,
        )

    def test_missing_planned_activity_fails_presence(self):
        with self.assertRaises(AssertionError):
            validate.validate_delta(
                self.config, "duckdb", self.expected,
                self.activity_ids + ["999"], self.sleep_days, deep=False,
            )

    def test_missing_sleep_day_fails_presence(self):
        with self.assertRaises(AssertionError):
            validate.assert_scope_present(
                self.config, "duckdb", self.activity_ids,
                [date(2026, 9, 18), date(2026, 9, 25)],
            )

    def test_deep_mismatch_fails(self):
        corrupted = {name: frame.copy() for name, frame in self.expected.items()}
        corrupted["workouts"] = corrupted["workouts"].copy()
        corrupted["workouts"].loc[0, "avg_hr"] = 999  # differ from stored value
        actual = validate.load_selected(
            self.config, "duckdb", self.activity_ids, self.sleep_days
        )
        with self.assertRaises(AssertionError):
            validate.assert_scope_matches_source(corrupted, actual, "duckdb")

    def test_parity_detects_missing_timeseries_record(self):
        # A backend missing a record for a shared activity must fail parity
        # (record keys are not intersected away).
        left = validate.load_selected(
            self.config, "duckdb", self.activity_ids, self.sleep_days
        )
        right = {name: frame.copy() for name, frame in left.items()}
        right["timeseries"] = right["timeseries"].iloc[1:].reset_index(drop=True)
        mixed = {"duckdb": left, "postgres": right}
        original = validate.load_selected
        validate.load_selected = lambda config, backend, a, s: mixed[backend]
        try:
            with self.assertRaises(AssertionError):
                validate.validate_window_parity(
                    self.config, ["duckdb", "postgres"], self.activity_ids, self.sleep_days
                )
        finally:
            validate.load_selected = original

    def test_window_parity_pass_and_reconciliation_error(self):
        # Drive the pure comparison via a patched loader (no PostgreSQL needed).
        left = validate.load_selected(
            self.config, "duckdb", self.activity_ids, self.sleep_days
        )
        matching = {b: left for b in ("duckdb", "postgres")}
        original = validate.load_selected
        validate.load_selected = lambda config, backend, a, s: matching[backend]
        try:
            validate.validate_window_parity(
                self.config, ["duckdb", "postgres"], self.activity_ids, self.sleep_days
            )
            mismatched_right = {
                name: frame.copy() for name, frame in left.items()
            }
            mismatched_right["workouts"] = mismatched_right["workouts"].copy()
            mismatched_right["workouts"].loc[0, "avg_hr"] = 12
            mixed = {"duckdb": left, "postgres": mismatched_right}
            validate.load_selected = lambda config, backend, a, s: mixed[backend]
            with self.assertRaises(AssertionError):
                validate.validate_window_parity(
                    self.config, ["duckdb", "postgres"], self.activity_ids, self.sleep_days
                )
        finally:
            validate.load_selected = original


if __name__ == "__main__":
    unittest.main()
