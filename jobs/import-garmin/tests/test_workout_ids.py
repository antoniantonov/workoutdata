"""Tests for the shared canonical workoutId assignment policy."""
from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from garmin_etl.transform import (  # noqa: E402
    assign_workout_ids,
    compute_workout_id_map,
    merge_existing_assignments,
)
from tests._frames import make_timeseries, make_workouts  # noqa: E402

BASE = "10-09-2026_080000"
DT = datetime(2026, 9, 10, 8, 0, 0)


class WorkoutIdAssignmentTests(unittest.TestCase):
    def test_no_collision_keeps_base_ids(self):
        workouts = make_workouts([
            ("1", "10-09-2026_080000", datetime(2026, 9, 10, 8, 0, 0)),
            ("2", "11-09-2026_090000", datetime(2026, 9, 11, 9, 0, 0)),
        ])
        result = compute_workout_id_map(workouts)
        self.assertEqual(result, {"1": "10-09-2026_080000", "2": "11-09-2026_090000"})

    def test_same_second_collision_suffixes_later_activity(self):
        workouts = make_workouts([
            ("1", BASE, DT),
            ("2", BASE, DT),
        ])
        result = compute_workout_id_map(workouts)
        self.assertEqual(result, {"1": BASE, "2": f"{BASE}-2"})

    def test_existing_assignment_is_preserved_not_renumbered(self):
        # Activity 1 already committed as the bare base; a new activity 2 with
        # the same base must take -2 while 1 is left untouched.
        workouts = make_workouts([("1", BASE, DT), ("2", BASE, DT)])
        result = compute_workout_id_map(workouts, existing_assignments={"1": BASE})
        self.assertEqual(result["1"], BASE)
        self.assertEqual(result["2"], f"{BASE}-2")

    def test_new_activity_avoids_all_reserved_suffixes(self):
        # base and base-2 are both already committed → new activity gets base-3.
        workouts = make_workouts([("9", BASE, DT)])
        existing = {"1": BASE, "2": f"{BASE}-2"}
        result = compute_workout_id_map(workouts, existing_assignments=existing)
        self.assertEqual(result["9"], f"{BASE}-3")

    def test_cross_backend_assignment_is_deterministic(self):
        # Same committed seed + same new activities → identical assignment,
        # so separate backend runs agree.
        workouts = make_workouts([("2", BASE, DT), ("3", BASE, DT)])
        existing = {"1": BASE}
        first = compute_workout_id_map(workouts, existing_assignments=existing)
        second = compute_workout_id_map(workouts, existing_assignments=existing)
        self.assertEqual(first, second)
        self.assertEqual(first, {"2": f"{BASE}-2", "3": f"{BASE}-3"})

    def test_assign_rewrites_both_frames(self):
        workouts = make_workouts([("1", BASE, DT), ("2", BASE, DT)])
        timeseries = make_timeseries([("1", BASE), ("2", BASE)])
        new_workouts, new_timeseries = assign_workout_ids(workouts, timeseries)
        mapping = dict(zip(new_workouts["activity_id"], new_workouts["workoutId"]))
        self.assertEqual(mapping, {"1": BASE, "2": f"{BASE}-2"})
        ts_map = {
            a: w
            for a, w in zip(new_timeseries["activity_id"], new_timeseries["workoutId"])
        }
        self.assertEqual(ts_map, {"1": BASE, "2": f"{BASE}-2"})

    def test_merge_existing_assignments_rejects_conflict(self):
        with self.assertRaises(ValueError):
            merge_existing_assignments({"1": BASE}, {"1": f"{BASE}-2"})

    def test_merge_existing_assignments_rejects_reverse_conflict(self):
        # Two different activities must never share the same workoutId.
        with self.assertRaises(ValueError):
            merge_existing_assignments({"1": BASE}, {"2": BASE})

    def test_merge_existing_assignments_unions(self):
        merged = merge_existing_assignments({"1": BASE}, {"2": f"{BASE}-2"})
        self.assertEqual(merged, {"1": BASE, "2": f"{BASE}-2"})


if __name__ == "__main__":
    unittest.main()
