"""Functions for reading health data from GarminDB SQLite databases.

GarminDB (https://github.com/tcgoetz/GarminDB) organises Garmin Connect data
into three SQLite databases.  This module reads the three data types requested
by the user — heart rate, sleep, and GPS activity data — directly via the
``sqlite3`` standard-library module so that the GarminDB package itself is
not required at runtime.

Default database locations follow the GarminDB convention::

    ~/HealthData/DBs/garmin.db
    ~/HealthData/DBs/garmin_monitoring.db
    ~/HealthData/DBs/garmin_activities.db

All reader functions accept an optional *garmin_db_dir* argument that
overrides the default directory.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

import pandas as pd


# ---------------------------------------------------------------------------
# Default path helpers
# ---------------------------------------------------------------------------

_DEFAULT_GARMIN_DB_DIR = Path.home() / "HealthData" / "DBs"

_DB_FILES = {
    "garmin": "garmin.db",
    "monitoring": "garmin_monitoring.db",
    "activities": "garmin_activities.db",
}


def _resolve_db_path(db_key: str, garmin_db_dir: Optional[str | Path]) -> Path:
    """Return the resolved path for a GarminDB SQLite database file.

    Parameters
    ----------
    db_key:
        One of ``"garmin"``, ``"monitoring"``, or ``"activities"``.
    garmin_db_dir:
        Directory that contains the GarminDB SQLite files.  Defaults to
        ``~/HealthData/DBs`` when *None*.

    Raises
    ------
    FileNotFoundError
        If the resolved database file does not exist.
    """
    base = Path(garmin_db_dir) if garmin_db_dir is not None else _DEFAULT_GARMIN_DB_DIR
    path = base / _DB_FILES[db_key]
    if not path.exists():
        raise FileNotFoundError(
            f"GarminDB database not found: {path}\n"
            "Make sure GarminDB has been set up and the databases have been populated.\n"
            "See https://github.com/tcgoetz/GarminDB for setup instructions."
        )
    return path


# ---------------------------------------------------------------------------
# Public reader functions
# ---------------------------------------------------------------------------

def read_heart_rate(
    garmin_db_dir: Optional[str | Path] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> pd.DataFrame:
    """Read continuous (all-day) heart rate data from GarminDB.

    Reads the ``monitoring_hr`` table from ``garmin_monitoring.db``.

    Parameters
    ----------
    garmin_db_dir:
        Directory containing GarminDB SQLite files.  Defaults to
        ``~/HealthData/DBs``.
    start_date:
        Optional ISO-8601 date string (``"YYYY-MM-DD"``) to filter results
        from this date onward (inclusive).
    end_date:
        Optional ISO-8601 date string (``"YYYY-MM-DD"``) to filter results
        up to this date (inclusive).

    Returns
    -------
    pd.DataFrame
        DataFrame with columns:

        - ``timestamp`` – ``datetime`` of the HR measurement
        - ``heart_rate`` – heart rate in bpm (int)

    Raises
    ------
    FileNotFoundError
        If ``garmin_monitoring.db`` is not found.
    """
    db_path = _resolve_db_path("monitoring", garmin_db_dir)
    query = "SELECT timestamp, heart_rate FROM monitoring_hr"
    params: list = []

    conditions = []
    if start_date:
        conditions.append("timestamp >= ?")
        params.append(start_date)
    if end_date:
        conditions.append("timestamp <= ?")
        params.append(end_date + " 23:59:59")

    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY timestamp"

    with sqlite3.connect(str(db_path)) as con:
        df = pd.read_sql_query(query, con, params=params, parse_dates=["timestamp"])

    print(f"✅ Read {len(df):,} heart rate records from GarminDB")
    return df


def read_sleep(
    garmin_db_dir: Optional[str | Path] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> pd.DataFrame:
    """Read sleep session data from GarminDB.

    Reads the ``sleep`` table from ``garmin.db``.

    Parameters
    ----------
    garmin_db_dir:
        Directory containing GarminDB SQLite files.  Defaults to
        ``~/HealthData/DBs``.
    start_date:
        Optional ISO-8601 date string (``"YYYY-MM-DD"``) to filter results
        from this date onward (inclusive).
    end_date:
        Optional ISO-8601 date string (``"YYYY-MM-DD"``) to filter results
        up to this date (inclusive).

    Returns
    -------
    pd.DataFrame
        DataFrame with columns:

        - ``day`` – date of the sleep session
        - ``start`` – sleep start datetime
        - ``end_time`` – sleep end datetime
        - ``total_sleep`` – total sleep duration (``"HH:MM:SS"`` string)
        - ``deep_sleep`` – deep sleep duration (``"HH:MM:SS"`` string)
        - ``light_sleep`` – light sleep duration (``"HH:MM:SS"`` string)
        - ``rem_sleep`` – REM sleep duration (``"HH:MM:SS"`` string)
        - ``awake`` – awake time during sleep period (``"HH:MM:SS"`` string)
        - ``avg_spo2`` – average blood oxygen saturation (float or None)
        - ``avg_rr`` – average respiration rate (float or None)
        - ``avg_stress`` – average stress score (float or None)
        - ``score`` – overall sleep score (int or None)
        - ``qualifier`` – qualitative sleep qualifier (str or None)

    Raises
    ------
    FileNotFoundError
        If ``garmin.db`` is not found.
    """
    db_path = _resolve_db_path("garmin", garmin_db_dir)

    # GarminDB stores the end column as ``end`` but that is a reserved word in
    # some SQL dialects, so we alias it to ``end_time`` for portability.
    query = """
        SELECT
            day,
            start,
            "end" AS end_time,
            total_sleep,
            deep_sleep,
            light_sleep,
            rem_sleep,
            awake,
            avg_spo2,
            avg_rr,
            avg_stress,
            score,
            qualifier
        FROM sleep
    """
    params: list = []
    conditions = []
    if start_date:
        conditions.append("day >= ?")
        params.append(start_date)
    if end_date:
        conditions.append("day <= ?")
        params.append(end_date)

    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY day"

    with sqlite3.connect(str(db_path)) as con:
        df = pd.read_sql_query(query, con, params=params)

    print(f"✅ Read {len(df):,} sleep records from GarminDB")
    return df


def read_activities(
    garmin_db_dir: Optional[str | Path] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> pd.DataFrame:
    """Read recorded activity data (including GPS coordinates) from GarminDB.

    Reads the ``activities`` table from ``garmin_activities.db``.

    Parameters
    ----------
    garmin_db_dir:
        Directory containing GarminDB SQLite files.  Defaults to
        ``~/HealthData/DBs``.
    start_date:
        Optional ISO-8601 date string (``"YYYY-MM-DD"``) to filter results
        where ``start_time >= start_date`` (inclusive).
    end_date:
        Optional ISO-8601 date string (``"YYYY-MM-DD"``) to filter results
        where ``start_time <= end_date`` (inclusive).

    Returns
    -------
    pd.DataFrame
        DataFrame with columns:

        - ``activity_id`` – unique activity identifier (str)
        - ``name`` – activity name (str or None)
        - ``sport`` – sport type, e.g. ``"running"`` (str or None)
        - ``start_time`` – activity start datetime
        - ``stop_time`` – activity stop datetime
        - ``elapsed_time`` – total elapsed time (``"HH:MM:SS"`` string)
        - ``distance`` – distance in km or miles (float or None)
        - ``avg_hr`` – average heart rate in bpm (int or None)
        - ``max_hr`` – maximum heart rate in bpm (int or None)
        - ``calories`` – total calories burned (int or None)
        - ``avg_speed`` – average speed in km/h or mph (float or None)
        - ``max_speed`` – maximum speed in km/h or mph (float or None)
        - ``start_lat`` – GPS latitude at start (float or None)
        - ``start_long`` – GPS longitude at start (float or None)
        - ``stop_lat`` – GPS latitude at stop (float or None)
        - ``stop_long`` – GPS longitude at stop (float or None)

    Raises
    ------
    FileNotFoundError
        If ``garmin_activities.db`` is not found.
    """
    db_path = _resolve_db_path("activities", garmin_db_dir)
    query = """
        SELECT
            activity_id,
            name,
            sport,
            start_time,
            stop_time,
            elapsed_time,
            distance,
            avg_hr,
            max_hr,
            calories,
            avg_speed,
            max_speed,
            start_lat,
            start_long,
            stop_lat,
            stop_long
        FROM activities
    """
    params: list = []
    conditions = []
    if start_date:
        conditions.append("start_time >= ?")
        params.append(start_date)
    if end_date:
        conditions.append("start_time <= ?")
        params.append(end_date + " 23:59:59")

    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY start_time"

    with sqlite3.connect(str(db_path)) as con:
        df = pd.read_sql_query(query, con, params=params, parse_dates=["start_time", "stop_time"])

    print(f"✅ Read {len(df):,} activity records from GarminDB")
    return df


def read_daily_summary(
    garmin_db_dir: Optional[str | Path] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> pd.DataFrame:
    """Read daily health summary data from GarminDB.

    Reads the ``daily_summary`` table from ``garmin.db``.

    Parameters
    ----------
    garmin_db_dir:
        Directory containing GarminDB SQLite files.  Defaults to
        ``~/HealthData/DBs``.
    start_date:
        Optional ISO-8601 date string (``"YYYY-MM-DD"``) to filter results
        from this date onward (inclusive).
    end_date:
        Optional ISO-8601 date string (``"YYYY-MM-DD"``) to filter results
        up to this date (inclusive).

    Returns
    -------
    pd.DataFrame
        DataFrame with columns matching the ``daily_summary`` table:

        - ``day`` – date of the summary
        - ``hr_min`` – minimum heart rate (int or None)
        - ``hr_max`` – maximum heart rate (int or None)
        - ``rhr`` – resting heart rate (int or None)
        - ``stress_avg`` – average stress score (int or None)
        - ``steps`` – total step count (int or None)
        - ``distance`` – total distance (float or None)
        - ``calories_total`` – total calories burned (int or None)
        - ``calories_active`` – active calories burned (int or None)
        - ``spo2_avg`` – average blood oxygen saturation (float or None)

    Raises
    ------
    FileNotFoundError
        If ``garmin.db`` is not found.
    """
    db_path = _resolve_db_path("garmin", garmin_db_dir)
    query = """
        SELECT
            day,
            hr_min,
            hr_max,
            rhr,
            stress_avg,
            steps,
            distance,
            calories_total,
            calories_active,
            spo2_avg
        FROM daily_summary
    """
    params: list = []
    conditions = []
    if start_date:
        conditions.append("day >= ?")
        params.append(start_date)
    if end_date:
        conditions.append("day <= ?")
        params.append(end_date)

    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY day"

    with sqlite3.connect(str(db_path)) as con:
        df = pd.read_sql_query(query, con, params=params)

    print(f"✅ Read {len(df):,} daily summary records from GarminDB")
    return df


__all__ = [
    'read_heart_rate',
    'read_sleep',
    'read_activities',
    'read_daily_summary',
]
