"""Coordinator tests with a fake adapter/acquirer and a real DuckDB backend.

The GarminDB SQLite cache is pre-populated to stand in for source_import having
already staged+imported the selected payloads, so the coordinator's discovery →
delta → selective transform → delta-write → validation path is exercised end to
end without network or a real GarminDB import.
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
    def __init__(self, valid_days):
        self._valid_days = set(valid_days)
        self.activity_calls = []
        self.sleep_calls = []
        self.imported = False

    def acquire_activities(self, needed):
        self.activity_calls.append(set(str(n) for n in needed))
        return _Result(acquired=frozenset(str(n) for n in needed))

    def acquire_sleep(self, needed):
        self.sleep_calls.append(set(needed))
        valid = frozenset(d for d in needed if d in self._valid_days)
        empty = frozenset(d for d in needed if d not in self._valid_days)
        return _Result(sleep_valid=valid, sleep_empty=empty)

    def import_into_cache(self, staged=None):
        self.imported = True


class CoordinatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.db_dir = self.root / "local_data" / "garmin_sqlite" / "DBs"
        build_sqlite_cache(
            self.db_dir,
            activities=[
                ("100", "2026-09-10 08:00:00"),
                ("101", "2026-09-11 09:00:00"),
                ("102", "2026-09-12 10:00:00"),
            ],
            records=[("100", 0), ("101", 0), ("102", 0)],
            sleeps=["2026-09-18", "2026-09-19", "2026-09-20"],
        )
        self.config = {
            "JOB_DIR": str(self.root),
            "DATABASE_TYPE": "duckdb",
            "GARMIN_DB_DIR": str(self.db_dir),
            "GARMIN_DUCKDB_PATH": str(self.root / "local_data" / "garmin.duckdb"),
            "GARMIN_LOCAL_TIMEZONE": "UTC",
            "GARMIN_LOOK_BACK_DAYS": 30,
        }
        self.valid_days = set(Window.create(30, NOW).sleep_dates()) & {
            __import__("datetime").date(2026, 9, 18),
            __import__("datetime").date(2026, 9, 19),
            __import__("datetime").date(2026, 9, 20),
        }

    def _sync(self, adapter, acquirer):
        return GarminSync(
            self.config,
            [Backend.DUCKDB],
            adapter=adapter,
            acquirer=acquirer,
            clock=lambda: NOW,
            use_locks=False,
        )

    def test_single_target_delta_loads_only_missing(self):
        # 100 already present in DuckDB → only 101,102 acquired and loaded.
        store.import_workouts(
            make_workouts([("100", "10-09-2026_080000")]),
            make_timeseries([("100", "10-09-2026_080000")]),
            self.config,
        )
        adapter = FakeAdapter({"100", "101", "102"})
        acquirer = FakeAcquirer(self.valid_days)
        report = self._sync(adapter, acquirer).run(look_back_days=30)

        self.assertFalse(report.no_op)
        self.assertEqual(acquirer.activity_calls, [{"101", "102"}])
        self.assertEqual(store.get_existing_activity_ids(self.config), {"100", "101", "102"})
        self.assertEqual(report.targets[Backend.DUCKDB].loaded_activities, 2)
        # sleep 18/19/20 were missing and valid → loaded
        self.assertEqual(
            store.get_existing_sleep_days(
                self.config,
                __import__("datetime").date(2026, 9, 1),
                __import__("datetime").date(2026, 9, 30),
            ),
            self.valid_days,
        )

    def test_no_op_when_all_candidate_keys_present(self):
        # Pre-populate every candidate activity AND every candidate sleep day.
        store.import_workouts(
            make_workouts([
                ("100", "10-09-2026_080000"),
                ("101", "11-09-2026_090000"),
                ("102", "12-09-2026_100000"),
            ]),
            make_timeseries([
                ("100", "10-09-2026_080000"),
                ("101", "11-09-2026_090000"),
                ("102", "12-09-2026_100000"),
            ]),
            self.config,
        )
        all_days = Window.create(30, NOW).sleep_dates()
        store.import_sleep(make_sleep(all_days), self.config)

        adapter = FakeAdapter({"100", "101", "102"})
        acquirer = FakeAcquirer(self.valid_days)
        report = self._sync(adapter, acquirer).run(look_back_days=30)

        self.assertTrue(report.no_op)
        self.assertEqual(acquirer.activity_calls, [])  # no downloads
        self.assertFalse(acquirer.imported)  # no cache import

    def test_repeated_run_probes_sleep_but_downloads_no_activities(self):
        # All activities present; only empty sleep dates remain missing.
        store.import_workouts(
            make_workouts([
                ("100", "10-09-2026_080000"),
                ("101", "11-09-2026_090000"),
                ("102", "12-09-2026_100000"),
            ]),
            make_timeseries([
                ("100", "10-09-2026_080000"),
                ("101", "11-09-2026_090000"),
                ("102", "12-09-2026_100000"),
            ]),
            self.config,
        )
        store.import_sleep(make_sleep(sorted(self.valid_days)), self.config)

        adapter = FakeAdapter({"100", "101", "102"})
        acquirer = FakeAcquirer(self.valid_days)  # only 18/19/20 are ever valid
        report = self._sync(adapter, acquirer).run(look_back_days=30)

        # Not a full no-op (empty candidate days are still probed), but zero
        # activity downloads and zero new activity rows.
        self.assertEqual(acquirer.activity_calls, [set()])
        self.assertEqual(report.targets[Backend.DUCKDB].loaded_activities, 0)
        self.assertEqual(report.targets[Backend.DUCKDB].loaded_sleep, 0)

    def test_only_valid_sleep_is_loaded(self):
        import datetime as _dt

        # No activities missing; sleep 18/19 valid, 20 empty on the server.
        store.import_workouts(
            make_workouts([
                ("100", "10-09-2026_080000"),
                ("101", "11-09-2026_090000"),
                ("102", "12-09-2026_100000"),
            ]),
            make_timeseries([
                ("100", "10-09-2026_080000"),
                ("101", "11-09-2026_090000"),
                ("102", "12-09-2026_100000"),
            ]),
            self.config,
        )
        adapter = FakeAdapter({"100", "101", "102"})
        acquirer = FakeAcquirer({_dt.date(2026, 9, 18), _dt.date(2026, 9, 19)})
        report = self._sync(adapter, acquirer).run(look_back_days=30)

        present = store.get_existing_sleep_days(
            self.config, _dt.date(2026, 9, 1), _dt.date(2026, 9, 30)
        )
        self.assertEqual(present, {_dt.date(2026, 9, 18), _dt.date(2026, 9, 19)})
        self.assertNotIn(_dt.date(2026, 9, 20), present)  # empty stays retryable

    def test_locks_do_not_break_a_duckdb_run(self):
        store.import_workouts(
            make_workouts([("100", "10-09-2026_080000")]),
            make_timeseries([("100", "10-09-2026_080000")]),
            self.config,
        )
        adapter = FakeAdapter({"100", "101"})
        acquirer = FakeAcquirer(self.valid_days)
        sync = GarminSync(
            self.config, [Backend.DUCKDB], adapter=adapter, acquirer=acquirer,
            clock=lambda: NOW, use_locks=True,
        )
        report = sync.run(look_back_days=30)
        self.assertIn("101", store.get_existing_activity_ids(self.config))


if __name__ == "__main__":
    unittest.main()
