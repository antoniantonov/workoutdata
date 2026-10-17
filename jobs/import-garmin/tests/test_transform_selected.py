"""Tests for the selective (delta) transform entry point."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from garmin_etl import transform  # noqa: E402
from tests._garmin_sqlite import build_sqlite_cache  # noqa: E402


class TransformSelectedTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_dir = Path(self._tmp.name) / "DBs"
        build_sqlite_cache(
            self.db_dir,
            activities=[
                ("100", "2026-09-10 08:00:00"),
                ("101", "2026-09-11 09:00:00"),
                ("102", "2026-09-12 10:00:00"),
            ],
            records=[("100", 0), ("100", 1), ("101", 0), ("102", 0), ("102", 1)],
            sleeps=["2026-09-18", "2026-09-19", "2026-09-20"],
        )
        self.config = {"GARMIN_DB_DIR": str(self.db_dir)}

    def test_selects_only_requested_activities_and_days(self):
        frames = transform.transform_selected(
            self.config, ["100", "102"], ["2026-09-19"]
        )
        self.assertEqual(set(frames["workouts"]["activity_id"]), {"100", "102"})
        # 101's records must never be materialized (limited local work).
        self.assertEqual(set(frames["timeseries"]["activity_id"]), {"100", "102"})
        self.assertEqual(len(frames["timeseries"]), 4)  # 2 for 100 + 2 for 102
        self.assertEqual(len(frames["sleep"]), 1)

    def test_base_workout_id_from_start_time(self):
        frames = transform.transform_selected(self.config, ["100"], [])
        self.assertEqual(
            frames["workouts"].iloc[0]["workoutId"], "10-09-2026_080000"
        )

    def test_empty_selection_needs_no_source_db(self):
        # Point at a non-existent DB dir; empty selection must still succeed.
        config = {"GARMIN_DB_DIR": str(Path(self._tmp.name) / "nonexistent")}
        frames = transform.transform_selected(config, [], [])
        self.assertTrue(frames["workouts"].empty)
        self.assertTrue(frames["timeseries"].empty)
        self.assertTrue(frames["sleep"].empty)
        self.assertEqual(list(frames["workouts"].columns), transform.WORKOUT_COLUMNS)

    def test_activities_only_returns_empty_sleep(self):
        frames = transform.transform_selected(self.config, ["101"], [])
        self.assertEqual(set(frames["workouts"]["activity_id"]), {"101"})
        self.assertTrue(frames["sleep"].empty)

    def test_assign_workout_ids_hook_applied(self):
        def assign(workouts, timeseries):
            workouts = workouts.copy()
            workouts["workoutId"] = workouts["workoutId"] + "-x"
            return workouts, timeseries

        frames = transform.transform_selected(
            self.config, ["100"], [], assign_workout_ids=assign
        )
        self.assertTrue(frames["workouts"].iloc[0]["workoutId"].endswith("-x"))

    def test_missing_activity_ids_are_simply_absent(self):
        # Requesting an id not in the cache yields no row for it (coordinator
        # validation catches an un-imported planned key, not the transform).
        frames = transform.transform_selected(self.config, ["100", "999"], [])
        self.assertEqual(set(frames["workouts"]["activity_id"]), {"100"})


if __name__ == "__main__":
    unittest.main()
