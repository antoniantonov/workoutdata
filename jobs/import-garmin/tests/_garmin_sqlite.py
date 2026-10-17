"""Build minimal GarminDB-shaped SQLite fixtures for transform tests.

Only the columns the transform layer actually reads are populated. Durations use
GarminDB's ``HH:MM:SS`` string form; timestamps use naive ``YYYY-MM-DD HH:MM:SS``.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

_ACTIVITY_COLUMNS = [
    "activity_id", "name", "sport", "sub_sport", "start_time", "stop_time",
    "elapsed_time", "moving_time", "distance", "calories", "avg_hr", "max_hr",
    "avg_speed", "max_speed", "avg_cadence", "ascent", "descent",
    "training_load", "training_effect", "start_lat", "start_long",
]
_RECORD_COLUMNS = [
    "activity_id", "record", "timestamp", "hr", "position_lat", "position_long",
    "speed", "distance", "cadence", "altitude", "temperature", "rr",
]
_SLEEP_COLUMNS = [
    "day", "start", "end", "total_sleep", "deep_sleep", "light_sleep",
    "rem_sleep", "awake", "avg_spo2", "avg_rr", "avg_stress", "score", "qualifier",
]


def _activity_row(activity_id, start_time, **overrides):
    row = {c: None for c in _ACTIVITY_COLUMNS}
    row.update(
        activity_id=str(activity_id),
        name=f"Activity {activity_id}",
        sport="running",
        sub_sport="generic",
        start_time=start_time,
        stop_time=start_time,
        elapsed_time="00:30:00",
        moving_time="00:28:00",
        distance=5000.0,
        calories=300,
        avg_hr=140,
        max_hr=170,
    )
    row.update(overrides)
    return row


def _record_row(activity_id, record, **overrides):
    row = {c: None for c in _RECORD_COLUMNS}
    row.update(
        activity_id=str(activity_id),
        record=record,
        timestamp=f"2026-09-10 08:00:{record:02d}",
        hr=120 + record,
    )
    row.update(overrides)
    return row


def _sleep_row(day, **overrides):
    row = {c: None for c in _SLEEP_COLUMNS}
    row.update(
        day=day,
        start=f"{day} 23:00:00",
        end=f"{day} 06:00:00",
        total_sleep="07:00:00",
        deep_sleep="01:30:00",
        light_sleep="04:00:00",
        rem_sleep="01:30:00",
        awake="00:00:00",
        score=80,
        qualifier="GOOD",
    )
    row.update(overrides)
    return row


def _create(conn, table, columns, rows):
    conn.execute(f"CREATE TABLE {table} ({', '.join(c + ' TEXT' for c in columns)})")
    if rows:
        placeholders = ", ".join("?" for _ in columns)
        conn.executemany(
            f"INSERT INTO {table} VALUES ({placeholders})",
            [tuple(row[c] for c in columns) for row in rows],
        )
    conn.commit()


def build_sqlite_cache(db_dir: Path, activities, records, sleeps) -> None:
    """Write ``garmin_activities.db`` and ``garmin.db`` under ``db_dir``.

    ``activities``: ``[(activity_id, start_time), ...]`` or dict rows.
    ``records``: ``[(activity_id, record), ...]``.
    ``sleeps``: ``['YYYY-MM-DD', ...]``.
    """
    db_dir.mkdir(parents=True, exist_ok=True)
    activity_rows = [
        a if isinstance(a, dict) else _activity_row(a[0], a[1]) for a in activities
    ]
    record_rows = [
        r if isinstance(r, dict) else _record_row(r[0], r[1]) for r in records
    ]
    sleep_rows = [s if isinstance(s, dict) else _sleep_row(s) for s in sleeps]

    activities_conn = sqlite3.connect(db_dir / "garmin_activities.db")
    try:
        _create(activities_conn, "activities", _ACTIVITY_COLUMNS, activity_rows)
        _create(activities_conn, "activity_records", _RECORD_COLUMNS, record_rows)
    finally:
        activities_conn.close()

    garmin_conn = sqlite3.connect(db_dir / "garmin.db")
    try:
        _create(garmin_conn, "sleep", _SLEEP_COLUMNS, sleep_rows)
    finally:
        garmin_conn.close()
