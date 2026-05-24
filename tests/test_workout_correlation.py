"""Focused tests for Polar/Garmin time-overlap correlation helpers."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from polar.utils.workout_correlation import (
    build_garmin_activity_windows,
    correlate_workouts_by_overlap,
    normalize_polar_workout_windows,
    parse_duration_to_seconds,
)


def test_parse_duration_to_seconds() -> None:
    values = pd.Series(["3600", "00:15:30", None, "bad"])
    parsed = parse_duration_to_seconds(values)
    assert parsed.iloc[0] == 3600
    assert parsed.iloc[1] == 930
    assert pd.isna(parsed.iloc[2])
    assert pd.isna(parsed.iloc[3])


def test_overlap_correlation() -> None:
    polar_metadata_df = pd.DataFrame(
        {
            "workoutId": ["11-05-2025_100000", "11-05-2025_120000"],
            "Date": ["11-05-2025", "11-05-2025"],
            "Start time": ["10:00:00", "12:00:00"],
            "Duration": ["00:30:00", "00:20:00"],
        }
    )
    garmin_metadata_df = pd.DataFrame(
        {
            "activity_id": [101, 202],
            "start_time_utc": ["2025-05-11T10:05:00Z", "2025-05-11T13:00:00Z"],
            "duration_seconds": [1200, 1200],
            "metadata_file": ["/tmp/activity_101.json", "/tmp/activity_202.json"],
        }
    )

    polar_windows_df = normalize_polar_workout_windows(polar_metadata_df, local_timezone="UTC")
    garmin_windows_df = build_garmin_activity_windows(garmin_metadata_df, local_timezone="UTC")
    correlation_df = correlate_workouts_by_overlap(
        polar_windows_df,
        garmin_windows_df,
        tolerance_seconds=300,
    )

    assert len(correlation_df) == 1
    assert correlation_df.iloc[0]["workoutId"] == "11-05-2025_100000"
    assert int(correlation_df.iloc[0]["activity_id"]) == 101
    assert correlation_df.iloc[0]["metadata_file"] == "/tmp/activity_101.json"


if __name__ == "__main__":
    test_parse_duration_to_seconds()
    test_overlap_correlation()
    print("✅ workout correlation tests passed")
