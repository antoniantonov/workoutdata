"""Integration tests for the `both` path: union-of-deltas, partial-success
recovery, and cross-backend workoutId consistency.

PostgreSQL is unavailable in this environment, so the second backend is a second
isolated DuckDB file injected via the coordinator's ``storage_resolver``. This
exercises the real per-backend delta/transform/write/inventory logic and exact
per-id acquisition counts without a live PostgreSQL server (that path is covered
by the containerized suite in CI).
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from garmin_etl.delta import Backend, RemoteInventory, Window  # noqa: E402
from garmin_etl.storage import duckdb as store  # noqa: E402
from garmin_etl.sync import GarminSync  # noqa: E402
from tests._frames import make_sleep, make_timeseries, make_workouts  # noqa: E402
from tests._garmin_sqlite import build_sqlite_cache  # noqa: E402

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)


class _Result:
    def __init__(self, **kwargs):
        for name in (
            "acquired", "reused", "no_fit", "failed",
            "sleep_valid", "sleep_reused", "sleep_empty", "sleep_provisional",
        ):
            setattr(self, name, frozenset(kwargs.get(name, ())))


class FakeAdapter:
    def __init__(self, activity_ids):
        self._activity_ids = frozenset(activity_ids)

    def build_remote_inventory(self, window):
        return RemoteInventory(self._activity_ids, frozenset(window.sleep_dates()))


class FakeAcquirer:
    def __init__(self):
        self.activity_calls = []

    def acquire_activities(self, needed):
        self.activity_calls.append(set(str(n) for n in needed))
        return _Result(acquired=frozenset(str(n) for n in needed))

    def acquire_sleep(self, needed):
        return _Result()  # no valid sleep in these tests

    def import_into_cache(self, staged=None):
        pass


class DuckAt:
    """A storage module bound to a specific DuckDB file path."""

    def __init__(self, path, *, fail_writes=False):
        self._override = {"GARMIN_DUCKDB_PATH": str(path)}
        self._fail_writes = fail_writes

    def _cfg(self, config):
        return {**config, **self._override}

    def get_existing_activity_ids(self, config, candidate_ids=None):
        return store.get_existing_activity_ids(self._cfg(config), candidate_ids)

    def get_existing_sleep_days(self, config, start_day, end_day):
        return store.get_existing_sleep_days(self._cfg(config), start_day, end_day)

    def get_workout_id_assignments(self, config, activity_ids=None):
        return store.get_workout_id_assignments(self._cfg(config), activity_ids)

    def import_workouts(self, workouts, timeseries, config):
        if self._fail_writes:
            raise RuntimeError("simulated backend failure")
        return store.import_workouts(workouts, timeseries, self._cfg(config))

    def import_sleep(self, sleep, config):
        return store.import_sleep(sleep, self._cfg(config))


class SyncBothTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.db_dir = self.root / "local_data" / "garmin_sqlite" / "DBs"
        build_sqlite_cache(
            self.db_dir,
            activities=[
                ("1", "2026-09-10 08:00:00"),
                ("2", "2026-09-11 09:00:00"),
                ("3", "2026-09-12 10:00:00"),
            ],
            records=[("1", 0), ("2", 0), ("3", 0)],
            sleeps=[],
        )
        self.config = {
            "JOB_DIR": str(self.root),
            "DATABASE_TYPE": "duckdb",
            "GARMIN_DB_DIR": str(self.db_dir),
            "GARMIN_LOCAL_TIMEZONE": "UTC",
            "GARMIN_LOOK_BACK_DAYS": 30,
        }
        self.duck_path = self.root / "duck.duckdb"       # Backend.DUCKDB
        self.pg_path = self.root / "pg.duckdb"           # stands in for Backend.POSTGRES
        self.duck = DuckAt(self.duck_path)

    def _resolver(self, pg):
        def resolve(backend):
            return self.duck if backend == Backend.DUCKDB else pg
        return resolve

    def _seed(self, storage: DuckAt, specs):
        storage.import_workouts(make_workouts(specs), make_timeseries(specs), self.config)

    def test_deep_validate_noop_invokes_parity(self):
        import garmin_etl.validate as validate_module

        specs = [
            ("1", "10-09-2026_080000"),
            ("2", "11-09-2026_090000"),
            ("3", "12-09-2026_100000"),
        ]
        all_days = Window.create(30, NOW).sleep_dates()
        pg = DuckAt(self.pg_path)
        self._seed(self.duck, specs)
        self._seed(pg, specs)
        self.duck.import_sleep(make_sleep(all_days), self.config)
        pg.import_sleep(make_sleep(all_days), self.config)

        calls = []
        original = validate_module.validate_window_parity
        validate_module.validate_window_parity = lambda *a, **k: calls.append(a)
        try:
            sync = GarminSync(
                self.config, [Backend.DUCKDB, Backend.POSTGRES],
                adapter=FakeAdapter({"1", "2", "3"}), acquirer=FakeAcquirer(),
                clock=lambda: NOW, use_locks=False, storage_resolver=self._resolver(pg),
            )
            report = sync.run(look_back_days=30, deep_validate=True)
            self.assertTrue(report.no_op)
            self.assertEqual(len(calls), 1)  # parity runs even on a no-op --validate

            # Without --validate, a no-op run must NOT invoke parity.
            calls.clear()
            sync2 = GarminSync(
                self.config, [Backend.DUCKDB, Backend.POSTGRES],
                adapter=FakeAdapter({"1", "2", "3"}), acquirer=FakeAcquirer(),
                clock=lambda: NOW, use_locks=False, storage_resolver=self._resolver(pg),
            )
            sync2.run(look_back_days=30, deep_validate=False)
            self.assertEqual(calls, [])
        finally:
            validate_module.validate_window_parity = original

    def test_union_acquired_once_and_each_backend_loads_its_own_delta(self):
        # DuckDB has {1}; PostgreSQL has {1,2}; remote {1,2,3}.
        self._seed(self.duck, [("1", "10-09-2026_080000")])
        pg = DuckAt(self.pg_path)
        self._seed(pg, [("1", "10-09-2026_080000"), ("2", "11-09-2026_090000")])

        acquirer = FakeAcquirer()
        sync = GarminSync(
            self.config, [Backend.DUCKDB, Backend.POSTGRES],
            adapter=FakeAdapter({"1", "2", "3"}), acquirer=acquirer,
            clock=lambda: NOW, use_locks=False, storage_resolver=self._resolver(pg),
        )
        report = sync.run(look_back_days=30)

        # Acquire the union {2,3} exactly once.
        self.assertEqual(acquirer.activity_calls, [{"2", "3"}])
        # DuckDB loaded {2,3}; PostgreSQL loaded {3}.
        self.assertEqual(report.targets[Backend.DUCKDB].loaded_activities, 2)
        self.assertEqual(report.targets[Backend.POSTGRES].loaded_activities, 1)
        self.assertEqual(self.duck.get_existing_activity_ids(self.config), {"1", "2", "3"})
        self.assertEqual(pg.get_existing_activity_ids(self.config), {"1", "2", "3"})

    def test_workout_ids_are_consistent_across_backends(self):
        self._seed(self.duck, [("1", "10-09-2026_080000")])
        pg = DuckAt(self.pg_path)
        self._seed(pg, [("1", "10-09-2026_080000")])
        sync = GarminSync(
            self.config, [Backend.DUCKDB, Backend.POSTGRES],
            adapter=FakeAdapter({"1", "2", "3"}), acquirer=FakeAcquirer(),
            clock=lambda: NOW, use_locks=False, storage_resolver=self._resolver(pg),
        )
        sync.run(look_back_days=30)
        duck_ids = self.duck.get_workout_id_assignments(self.config)
        pg_ids = pg.get_workout_id_assignments(self.config)
        self.assertEqual(duck_ids, pg_ids)

    def test_persistent_map_keeps_ids_consistent_across_separate_runs(self):
        # Same-second activities 10 and 20. A DuckDB-only run sees only 20; a
        # later PostgreSQL-only run sees 10 and 20. The shared persistent map
        # keeps 20's id stable everywhere and gives the newly-seen 10 the suffix.
        db_dir = self.root / "collide_DBs"
        build_sqlite_cache(
            db_dir,
            activities=[
                ("10", "2026-09-10 08:00:00"),
                ("20", "2026-09-10 08:00:00"),  # same second as 10
            ],
            records=[("10", 0), ("20", 0)],
            sleeps=[],
        )
        config = {**self.config, "GARMIN_DB_DIR": str(db_dir)}
        duck = DuckAt(self.root / "c_duck.duckdb")
        pg = DuckAt(self.root / "c_pg.duckdb")

        GarminSync(
            config, [Backend.DUCKDB],
            adapter=FakeAdapter({"20"}), acquirer=FakeAcquirer(),
            clock=lambda: NOW, use_locks=False, storage_resolver=lambda b: duck,
        ).run(look_back_days=30)
        duck_ids = duck.get_workout_id_assignments(config)
        self.assertEqual(duck_ids, {"20": "10-09-2026_080000"})

        GarminSync(
            config, [Backend.POSTGRES],
            adapter=FakeAdapter({"10", "20"}), acquirer=FakeAcquirer(),
            clock=lambda: NOW, use_locks=False, storage_resolver=lambda b: pg,
        ).run(look_back_days=30)
        pg_ids = pg.get_workout_id_assignments(config)
        self.assertEqual(pg_ids["20"], "10-09-2026_080000")   # consistent with DuckDB
        self.assertEqual(pg_ids["10"], "10-09-2026_080000-2")  # new activity suffixed
        self.assertEqual(pg_ids["20"], duck_ids["20"])

    def test_partial_success_reports_failure_and_retry_repairs_only_outstanding(self):
        self._seed(self.duck, [("1", "10-09-2026_080000")])
        failing_pg = DuckAt(self.pg_path, fail_writes=True)
        self._seed(DuckAt(self.pg_path), [("1", "10-09-2026_080000")])  # pre-existing pg data

        # Attempt 1: PostgreSQL write fails after DuckDB commits.
        sync = GarminSync(
            self.config, [Backend.DUCKDB, Backend.POSTGRES],
            adapter=FakeAdapter({"1", "2", "3"}), acquirer=FakeAcquirer(),
            clock=lambda: NOW, use_locks=False,
            storage_resolver=self._resolver(failing_pg),
        )
        with self.assertRaises(RuntimeError):
            sync.run(look_back_days=30)
        # The partial-failure path surfaces which backend committed vs. failed.
        self.assertEqual(sync.last_report.failed_backend, Backend.POSTGRES)
        self.assertTrue(sync.last_report.targets[Backend.DUCKDB].committed)
        # DuckDB committed the delta; PostgreSQL did not.
        self.assertEqual(self.duck.get_existing_activity_ids(self.config), {"1", "2", "3"})
        healthy_pg = DuckAt(self.pg_path)
        self.assertEqual(healthy_pg.get_existing_activity_ids(self.config), {"1"})

        # Attempt 2 (retry) with a healthy PostgreSQL: DuckDB is already complete
        # (no-op), only PostgreSQL is repaired.
        acquirer2 = FakeAcquirer()
        sync2 = GarminSync(
            self.config, [Backend.DUCKDB, Backend.POSTGRES],
            adapter=FakeAdapter({"1", "2", "3"}), acquirer=acquirer2,
            clock=lambda: NOW, use_locks=False,
            storage_resolver=self._resolver(healthy_pg),
        )
        report = sync2.run(look_back_days=30)
        self.assertEqual(report.targets[Backend.DUCKDB].loaded_activities, 0)
        self.assertEqual(report.targets[Backend.POSTGRES].loaded_activities, 2)
        self.assertEqual(healthy_pg.get_existing_activity_ids(self.config), {"1", "2", "3"})


if __name__ == "__main__":
    unittest.main()
