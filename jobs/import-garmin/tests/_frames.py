"""Shared builders for minimal, schema-correct Garmin DataFrames in tests."""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd

from garmin_etl.transform import (
    SLEEP_COLUMNS,
    TIMESERIES_COLUMNS,
    WORKOUT_COLUMNS,
)

_BASE = datetime(2026, 9, 10, 8, 0, 0)


def make_workouts(specs) -> pd.DataFrame:
    """Build a workout-metadata frame from ``[(activity_id, workoutId), ...]``.

    Optional 3-tuples ``(activity_id, workoutId, start_time)`` set the start.
    """
    rows = []
    for index, spec in enumerate(specs):
        if len(spec) == 3:
            activity_id, workout_id, start_time = spec
        else:
            activity_id, workout_id = spec
            start_time = _BASE + timedelta(hours=index)
        row = {column: None for column in WORKOUT_COLUMNS}
        row.update(
            activity_id=str(activity_id),
            workoutId=workout_id,
            name=f"Activity {activity_id}",
            sport="running",
            sub_sport="generic",
            start_time=pd.Timestamp(start_time),
            stop_time=pd.Timestamp(start_time) + pd.Timedelta(minutes=30),
            elapsed_time_s=1800,
            moving_time_s=1700,
            distance=5000.0,
            calories=300,
            avg_hr=140,
            max_hr=170,
            gps_source="none",
        )
        rows.append(row)
    frame = pd.DataFrame(rows, columns=WORKOUT_COLUMNS)
    return frame


def make_timeseries(specs, records_per_activity=3) -> pd.DataFrame:
    """Build a timeseries frame for ``[(activity_id, workoutId), ...]``."""
    rows = []
    for activity_id, workout_id in specs:
        for record in range(records_per_activity):
            row = {column: None for column in TIMESERIES_COLUMNS}
            row.update(
                activity_id=str(activity_id),
                workoutId=workout_id,
                record=record,
                timestamp=pd.Timestamp(_BASE + timedelta(seconds=record)),
                hr=120 + record,
            )
            rows.append(row)
    return pd.DataFrame(rows, columns=TIMESERIES_COLUMNS)


def make_sleep(days) -> pd.DataFrame:
    """Build a sleep frame from an iterable of ``date`` values."""
    rows = []
    for day in days:
        row = {column: None for column in SLEEP_COLUMNS}
        start = datetime(day.year, day.month, day.day, 23, 0, 0)
        row.update(
            day=pd.Timestamp(day),
            sleep_start=pd.Timestamp(start),
            sleep_end=pd.Timestamp(start) + pd.Timedelta(hours=7),
            total_sleep_s=25200,
            deep_sleep_s=5400,
            light_sleep_s=14400,
            rem_sleep_s=5400,
            awake_s=0,
            score=80,
            qualifier="GOOD",
        )
        rows.append(row)
    return pd.DataFrame(rows, columns=SLEEP_COLUMNS)
