"""DuckDB storage tests: read-only inventories and delta-only writes."""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from garmin_etl.storage import duckdb as store  # noqa: E402
from tests._frames import make_sleep, make_timeseries, make_workouts  # noqa: E402


class DuckDBInventoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config = {"GARMIN_DUCKDB_PATH": str(Path(self._tmp.name) / "garmin.duckdb")}

    def _import(self, workout_specs, days=()):
        store.import_workouts(
            make_workouts(workout_specs), make_timeseries(workout_specs), self.config
        )
        if days:
            store.import_sleep(make_sleep(days), self.config)

    def test_absent_database_is_empty_not_error(self):
        self.assertEqual(store.get_existing_activity_ids(self.config), set())
        self.assertEqual(
            store.get_existing_sleep_days(self.config, date(2026, 9, 1), date(2026, 9, 30)),
            set(),
        )
        self.assertEqual(store.get_workout_id_assignments(self.config), {})

    def test_activity_ids_all_and_by_candidate(self):
        self._import([("1", "w1"), ("2", "w2"), ("3", "w3")])
        self.assertEqual(store.get_existing_activity_ids(self.config), {"1", "2", "3"})
        self.assertEqual(
            store.get_existing_activity_ids(self.config, ["2", "3", "99"]), {"2", "3"}
        )

    def test_empty_candidate_list_means_none_not_all(self):
        self._import([("1", "w1"), ("2", "w2")])
        self.assertEqual(store.get_existing_activity_ids(self.config, []), set())

    def test_sleep_days_within_range(self):
        self._import([("1", "w1")], days=[date(2026, 9, 18), date(2026, 9, 25)])
        self.assertEqual(
            store.get_existing_sleep_days(self.config, date(2026, 9, 17), date(2026, 9, 20)),
            {date(2026, 9, 18)},
        )

    def test_workout_id_assignments(self):
        self._import([("1", "w1"), ("2", "w2")])
        self.assertEqual(
            store.get_workout_id_assignments(self.config), {"1": "w1", "2": "w2"}
        )
        self.assertEqual(
            store.get_workout_id_assignments(self.config, ["2"]), {"2": "w2"}
        )
        self.assertEqual(store.get_workout_id_assignments(self.config, []), {})

    def test_inventory_read_only_does_not_create_file(self):
        # Reading an absent destination must not create a writable DB file.
        store.get_existing_activity_ids(self.config)
        self.assertFalse(Path(self.config["GARMIN_DUCKDB_PATH"]).exists())


class DuckDBDeltaWriteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config = {"GARMIN_DUCKDB_PATH": str(Path(self._tmp.name) / "garmin.duckdb")}

    def _timeseries_count(self, activity_id: str) -> int:
        import duckdb

        con = duckdb.connect(self.config["GARMIN_DUCKDB_PATH"], read_only=True)
        try:
            return con.execute(
                "SELECT COUNT(*) FROM garmin_timeseries WHERE activity_id = ?",
                [activity_id],
            ).fetchone()[0]
        finally:
            con.close()

    def test_zero_sample_replacement_clears_old_timeseries(self):
        # First import: activity 1 with 3 samples.
        store.import_workouts(
            make_workouts([("1", "w1")]), make_timeseries([("1", "w1")]), self.config
        )
        self.assertEqual(self._timeseries_count("1"), 3)
        # Re-import activity 1 as a zero-sample activity (metadata only). The old
        # timeseries must be removed even though the incoming timeseries is empty.
        empty_ts = make_timeseries([])
        store.import_workouts(make_workouts([("1", "w1")]), empty_ts, self.config)
        self.assertEqual(self._timeseries_count("1"), 0)
        self.assertEqual(store.get_existing_activity_ids(self.config), {"1"})

    def test_orphan_timeseries_rejected(self):
        workouts = make_workouts([("1", "w1")])
        # timeseries references activity 2, absent from metadata.
        bad_ts = make_timeseries([("2", "w2")])
        with self.assertRaises(ValueError):
            store.import_workouts(workouts, bad_ts, self.config)

    def test_empty_delta_is_no_op(self):
        store.import_workouts(
            make_workouts([("1", "w1"), ("2", "w2")]),
            make_timeseries([("1", "w1"), ("2", "w2")]),
            self.config,
        )
        before = store.get_existing_activity_ids(self.config)
        # Empty delta must not change existing rows.
        store.import_workouts(make_workouts([]), make_timeseries([]), self.config)
        self.assertEqual(store.get_existing_activity_ids(self.config), before)
        self.assertEqual(self._timeseries_count("1"), 3)

    def test_delta_write_only_touches_selected_activity(self):
        store.import_workouts(
            make_workouts([("1", "w1"), ("2", "w2")]),
            make_timeseries([("1", "w1"), ("2", "w2")]),
            self.config,
        )
        # Re-import only activity 1; activity 2's rows must be untouched.
        store.import_workouts(
            make_workouts([("1", "w1")]), make_timeseries([("1", "w1")]), self.config
        )
        self.assertEqual(self._timeseries_count("2"), 3)
        self.assertEqual(store.get_existing_activity_ids(self.config), {"1", "2"})


if __name__ == "__main__":
    unittest.main()
