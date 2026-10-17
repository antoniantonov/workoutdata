"""Regression tests for the full-cache transform_all after the id refactor."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from garmin_etl import transform  # noqa: E402
from tests._garmin_sqlite import build_sqlite_cache  # noqa: E402


class TransformAllTests(unittest.TestCase):
    def _config(self, **cache):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db_dir = Path(tmp.name) / "DBs"
        build_sqlite_cache(db_dir, **cache)
        return {"GARMIN_DB_DIR": str(db_dir)}

    def test_full_transform_disambiguates_same_second(self):
        config = self._config(
            activities=[
                ("100", "2026-09-10 08:00:00"),
                ("101", "2026-09-10 08:00:00"),  # same second as 100
            ],
            records=[("100", 0), ("101", 0)],
            sleeps=["2026-09-18"],
        )
        frames = transform.transform_all(config)
        ids = dict(zip(frames["workouts"]["activity_id"], frames["workouts"]["workoutId"]))
        self.assertEqual(ids["100"], "10-09-2026_080000")
        self.assertEqual(ids["101"], "10-09-2026_080000-2")
        # timeseries linkage matches the assigned ids
        ts = dict(zip(frames["timeseries"]["activity_id"], frames["timeseries"]["workoutId"]))
        self.assertEqual(ts["101"], "10-09-2026_080000-2")

    def test_full_transform_can_seed_existing_assignments(self):
        config = self._config(
            activities=[("100", "2026-09-10 08:00:00"), ("101", "2026-09-10 08:00:00")],
            records=[("100", 0), ("101", 0)],
            sleeps=[],
        )
        # 101 already committed as the bare base; 100 must not steal it.
        frames = transform.transform_all(
            config, existing_assignments={"101": "10-09-2026_080000"}
        )
        ids = dict(zip(frames["workouts"]["activity_id"], frames["workouts"]["workoutId"]))
        self.assertEqual(ids["101"], "10-09-2026_080000")
        self.assertEqual(ids["100"], "10-09-2026_080000-2")


if __name__ == "__main__":
    unittest.main()
