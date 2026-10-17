"""PostgreSQL-backed storage tests (read-only inventories + delta-safe writes).

These run only when a disposable PostgreSQL is provided via ``GARMIN_TEST_PG_DSN``
(for example the Compose ``postgres`` service). They are skipped otherwise so the
default suite stays free of Docker and external services. The tests create and
drop their own ``garmin_`` tables, so point the DSN at a throwaway database.
"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests._frames import make_sleep, make_timeseries, make_workouts  # noqa: E402

DSN = os.getenv("GARMIN_TEST_PG_DSN")


@unittest.skipUnless(DSN, "set GARMIN_TEST_PG_DSN to a throwaway PostgreSQL to run")
class PostgresStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        from garmin_etl.storage import postgres as store

        self.store = store
        self.config = {"POSTGRES_CONNECTION_STRING": DSN}
        self._drop()

    def tearDown(self) -> None:
        self._drop()

    def _drop(self) -> None:
        conn = self.store.get_postgres_connection(self.config)
        try:
            with conn.cursor() as cur:
                cur.execute("DROP TABLE IF EXISTS garmin_timeseries")
                cur.execute("DROP TABLE IF EXISTS garmin_workout_metadata")
                cur.execute("DROP TABLE IF EXISTS garmin_sleep")
            conn.commit()
        finally:
            conn.close()

    def _timeseries_count(self, activity_id: str) -> int:
        conn = self.store.get_postgres_connection(self.config)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM garmin_timeseries WHERE activity_id = %s",
                    (activity_id,),
                )
                return cur.fetchone()[0]
        finally:
            conn.close()

    def test_absent_tables_are_empty_not_error(self):
        self.assertEqual(self.store.get_existing_activity_ids(self.config), set())
        self.assertEqual(
            self.store.get_existing_sleep_days(self.config, date(2026, 9, 1), date(2026, 9, 30)),
            set(),
        )
        self.assertEqual(self.store.get_workout_id_assignments(self.config), {})

    def test_inventories_and_candidate_filter(self):
        self.store.import_workouts(
            make_workouts([("1", "w1"), ("2", "w2"), ("3", "w3")]),
            make_timeseries([("1", "w1"), ("2", "w2"), ("3", "w3")]),
            self.config,
        )
        self.store.import_sleep(make_sleep([date(2026, 9, 18), date(2026, 9, 25)]), self.config)
        self.assertEqual(self.store.get_existing_activity_ids(self.config), {"1", "2", "3"})
        self.assertEqual(
            self.store.get_existing_activity_ids(self.config, ["2", "9"]), {"2"}
        )
        self.assertEqual(self.store.get_existing_activity_ids(self.config, []), set())
        self.assertEqual(
            self.store.get_existing_sleep_days(self.config, date(2026, 9, 17), date(2026, 9, 20)),
            {date(2026, 9, 18)},
        )
        self.assertEqual(
            self.store.get_workout_id_assignments(self.config, ["2"]), {"2": "w2"}
        )

    def test_zero_sample_replacement_clears_old_timeseries(self):
        self.store.import_workouts(
            make_workouts([("1", "w1")]), make_timeseries([("1", "w1")]), self.config
        )
        self.assertEqual(self._timeseries_count("1"), 3)
        self.store.import_workouts(
            make_workouts([("1", "w1")]), make_timeseries([]), self.config
        )
        self.assertEqual(self._timeseries_count("1"), 0)
        self.assertEqual(self.store.get_existing_activity_ids(self.config), {"1"})

    def test_orphan_timeseries_rejected(self):
        with self.assertRaises(ValueError):
            self.store.import_workouts(
                make_workouts([("1", "w1")]), make_timeseries([("2", "w2")]), self.config
            )

    def test_empty_delta_is_no_op(self):
        self.store.import_workouts(
            make_workouts([("1", "w1")]), make_timeseries([("1", "w1")]), self.config
        )
        self.store.import_workouts(make_workouts([]), make_timeseries([]), self.config)
        self.assertEqual(self.store.get_existing_activity_ids(self.config), {"1"})
        self.assertEqual(self._timeseries_count("1"), 3)


if __name__ == "__main__":
    unittest.main()
