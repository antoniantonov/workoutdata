"""Tests for the polar.garmin module.

Tests cover:
- reader.py: reading from GarminDB SQLite (using in-memory SQLite databases)
- importer.py: importing into DuckDB (using temporary files)
- Module exports
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest
import pandas as pd

# Add repository root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def garmin_db_dir(tmp_path):
    """Create minimal GarminDB SQLite databases for testing."""
    # garmin.db – sleep + daily_summary
    garmin_db = tmp_path / "garmin.db"
    con = sqlite3.connect(str(garmin_db))
    con.execute("""
        CREATE TABLE sleep (
            day TEXT PRIMARY KEY,
            start TEXT,
            "end" TEXT,
            total_sleep TEXT,
            deep_sleep TEXT,
            light_sleep TEXT,
            rem_sleep TEXT,
            awake TEXT,
            avg_spo2 REAL,
            avg_rr REAL,
            avg_stress REAL,
            score INTEGER,
            qualifier TEXT
        )
    """)
    con.execute("""
        INSERT INTO sleep VALUES (
            '2024-01-01', '2024-01-01 22:00:00', '2024-01-02 06:30:00',
            '07:00:00', '01:30:00', '04:00:00', '01:00:00', '00:30:00',
            96.5, 14.2, 25.0, 85, 'Good'
        )
    """)
    con.execute("""
        INSERT INTO sleep VALUES (
            '2024-01-02', '2024-01-02 23:00:00', '2024-01-03 07:00:00',
            '07:30:00', '02:00:00', '04:00:00', '01:00:00', '00:30:00',
            97.0, 13.8, 22.0, 88, 'Good'
        )
    """)
    con.execute("""
        CREATE TABLE daily_summary (
            day TEXT PRIMARY KEY,
            hr_min INTEGER,
            hr_max INTEGER,
            rhr INTEGER,
            stress_avg INTEGER,
            steps INTEGER,
            distance REAL,
            calories_total INTEGER,
            calories_active INTEGER,
            spo2_avg REAL
        )
    """)
    con.execute("""
        INSERT INTO daily_summary VALUES (
            '2024-01-01', 48, 142, 52, 28, 8200, 6.3, 2100, 400, 96.5
        )
    """)
    con.commit()
    con.close()

    # garmin_monitoring.db – monitoring_hr
    monitoring_db = tmp_path / "garmin_monitoring.db"
    con = sqlite3.connect(str(monitoring_db))
    con.execute("""
        CREATE TABLE monitoring_hr (
            timestamp TEXT PRIMARY KEY,
            heart_rate INTEGER NOT NULL
        )
    """)
    rows = [
        ("2024-01-01 08:00:00", 62),
        ("2024-01-01 08:01:00", 64),
        ("2024-01-01 08:02:00", 63),
    ]
    con.executemany("INSERT INTO monitoring_hr VALUES (?, ?)", rows)
    con.commit()
    con.close()

    # garmin_activities.db – activities
    activities_db = tmp_path / "garmin_activities.db"
    con = sqlite3.connect(str(activities_db))
    con.execute("""
        CREATE TABLE activities (
            activity_id TEXT PRIMARY KEY,
            name TEXT,
            sport TEXT,
            start_time TEXT,
            stop_time TEXT,
            elapsed_time TEXT,
            distance REAL,
            avg_hr INTEGER,
            max_hr INTEGER,
            calories INTEGER,
            avg_speed REAL,
            max_speed REAL,
            start_lat REAL,
            start_long REAL,
            stop_lat REAL,
            stop_long REAL
        )
    """)
    con.execute("""
        INSERT INTO activities VALUES (
            'ACT001', 'Morning Run', 'running',
            '2024-01-01 07:00:00', '2024-01-01 07:45:00', '00:45:00',
            8.5, 148, 172, 520, 11.3, 14.2,
            51.5074, -0.1278, 51.5080, -0.1270
        )
    """)
    con.commit()
    con.close()

    return tmp_path


@pytest.fixture
def duckdb_config(tmp_path):
    """Config dict pointing at a temporary DuckDB file."""
    return {
        'DATABASE_TYPE': 'duckdb',
        'DUCKDB_PATH': tmp_path / "test.duckdb",
    }


# ---------------------------------------------------------------------------
# reader tests
# ---------------------------------------------------------------------------

class TestReader:
    def test_read_heart_rate_returns_dataframe(self, garmin_db_dir):
        from polar.garmin.reader import read_heart_rate
        df = read_heart_rate(garmin_db_dir)
        assert isinstance(df, pd.DataFrame)
        assert list(df.columns) == ['timestamp', 'heart_rate']
        assert len(df) == 3

    def test_read_heart_rate_date_filter(self, garmin_db_dir):
        from polar.garmin.reader import read_heart_rate
        df = read_heart_rate(garmin_db_dir, start_date='2024-01-01', end_date='2024-01-01')
        assert len(df) == 3

    def test_read_sleep_returns_dataframe(self, garmin_db_dir):
        from polar.garmin.reader import read_sleep
        df = read_sleep(garmin_db_dir)
        assert isinstance(df, pd.DataFrame)
        assert 'day' in df.columns
        assert 'total_sleep' in df.columns
        assert 'end_time' in df.columns  # aliased from 'end'
        assert len(df) == 2

    def test_read_sleep_date_filter(self, garmin_db_dir):
        from polar.garmin.reader import read_sleep
        df = read_sleep(garmin_db_dir, start_date='2024-01-02', end_date='2024-01-02')
        assert len(df) == 1
        assert df.iloc[0]['day'] == '2024-01-02'

    def test_read_activities_returns_dataframe(self, garmin_db_dir):
        from polar.garmin.reader import read_activities
        df = read_activities(garmin_db_dir)
        assert isinstance(df, pd.DataFrame)
        assert 'activity_id' in df.columns
        assert 'start_lat' in df.columns
        assert 'start_long' in df.columns
        assert len(df) == 1

    def test_read_activities_contains_gps(self, garmin_db_dir):
        from polar.garmin.reader import read_activities
        df = read_activities(garmin_db_dir)
        row = df.iloc[0]
        assert abs(row['start_lat'] - 51.5074) < 1e-4
        assert abs(row['start_long'] - (-0.1278)) < 1e-4

    def test_read_daily_summary_returns_dataframe(self, garmin_db_dir):
        from polar.garmin.reader import read_daily_summary
        df = read_daily_summary(garmin_db_dir)
        assert isinstance(df, pd.DataFrame)
        assert 'day' in df.columns
        assert 'steps' in df.columns
        assert len(df) == 1

    def test_read_heart_rate_file_not_found(self, tmp_path):
        from polar.garmin.reader import read_heart_rate
        with pytest.raises(FileNotFoundError):
            read_heart_rate(tmp_path)  # empty dir, no DB files


# ---------------------------------------------------------------------------
# importer tests (DuckDB)
# ---------------------------------------------------------------------------

class TestImporter:
    def test_import_garmin_heart_rate(self, garmin_db_dir, duckdb_config):
        from polar.garmin.importer import import_garmin_heart_rate
        stats = import_garmin_heart_rate(garmin_db_dir, duckdb_config)
        assert stats['total'] == 3
        assert stats['inserted'] == 3
        assert stats['skipped'] == 0

    def test_import_garmin_heart_rate_idempotent(self, garmin_db_dir, duckdb_config):
        from polar.garmin.importer import import_garmin_heart_rate
        import_garmin_heart_rate(garmin_db_dir, duckdb_config)
        stats = import_garmin_heart_rate(garmin_db_dir, duckdb_config)
        assert stats['inserted'] == 0
        assert stats['skipped'] == 3

    def test_import_garmin_sleep(self, garmin_db_dir, duckdb_config):
        from polar.garmin.importer import import_garmin_sleep
        stats = import_garmin_sleep(garmin_db_dir, duckdb_config)
        assert stats['total'] == 2
        assert stats['inserted'] == 2

    def test_import_garmin_activities(self, garmin_db_dir, duckdb_config):
        from polar.garmin.importer import import_garmin_activities
        stats = import_garmin_activities(garmin_db_dir, duckdb_config)
        assert stats['total'] == 1
        assert stats['inserted'] == 1

    def test_import_all_garmin_data(self, garmin_db_dir, duckdb_config):
        from polar.garmin.importer import import_all_garmin_data
        results = import_all_garmin_data(garmin_db_dir, duckdb_config)
        assert 'heart_rate' in results
        assert 'sleep' in results
        assert 'activities' in results
        assert 'daily_summary' in results
        assert results['heart_rate']['inserted'] == 3
        assert results['sleep']['inserted'] == 2
        assert results['activities']['inserted'] == 1

    def test_import_config_required(self, garmin_db_dir):
        from polar.garmin.importer import import_garmin_heart_rate
        with pytest.raises(ValueError, match="config parameter is required"):
            import_garmin_heart_rate(garmin_db_dir, None)

    def test_import_all_handles_missing_db(self, tmp_path, duckdb_config):
        """import_all_garmin_data should not raise when DBs are missing."""
        from polar.garmin.importer import import_all_garmin_data
        results = import_all_garmin_data(tmp_path, duckdb_config)
        # All imports should report an error key but not raise
        for key in ('heart_rate', 'sleep', 'activities', 'daily_summary'):
            assert 'error' in results[key]


# ---------------------------------------------------------------------------
# Module export tests
# ---------------------------------------------------------------------------

class TestModuleExports:
    def test_garmin_package_exports(self):
        from polar import garmin
        for name in ('read_heart_rate', 'read_sleep', 'read_activities',
                     'read_daily_summary', 'import_garmin_heart_rate',
                     'import_garmin_sleep', 'import_garmin_activities',
                     'import_garmin_daily_summary', 'import_all_garmin_data'):
            assert hasattr(garmin, name), f"polar.garmin missing export: {name}"

    def test_reader_all_exports(self):
        from polar.garmin import reader
        for name in ('read_heart_rate', 'read_sleep', 'read_activities', 'read_daily_summary'):
            assert name in reader.__all__

    def test_importer_all_exports(self):
        from polar.garmin import importer
        for name in ('import_garmin_heart_rate', 'import_garmin_sleep',
                     'import_garmin_activities', 'import_garmin_daily_summary',
                     'import_all_garmin_data'):
            assert name in importer.__all__


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
