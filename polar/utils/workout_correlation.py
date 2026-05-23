"""Utilities for correlating Polar and Garmin workouts by time overlap."""
from __future__ import annotations

from typing import Iterable

import pandas as pd


def _find_column(columns: Iterable[str], candidates: list[str]) -> str | None:
    by_lower = {str(col).lower(): str(col) for col in columns}
    for candidate in candidates:
        match = by_lower.get(candidate.lower())
        if match:
            return match
    return None


def parse_duration_to_seconds(values: pd.Series) -> pd.Series:
    """Parse mixed duration formats into seconds."""
    if values.empty:
        return pd.Series(dtype="float64")

    text_values = values.astype("string").str.strip()
    numeric_values = pd.to_numeric(text_values, errors="coerce")
    timedelta_values = pd.to_timedelta(text_values, errors="coerce").dt.total_seconds()
    return numeric_values.fillna(timedelta_values)


def _to_utc(values: pd.Series, local_timezone: str) -> pd.Series:
    timestamps = pd.to_datetime(values, errors="coerce")
    if timestamps.dt.tz is None:
        return timestamps.dt.tz_localize(local_timezone, ambiguous="NaT", nonexistent="shift_forward").dt.tz_convert("UTC")
    return timestamps.dt.tz_convert("UTC")


def normalize_polar_workout_windows(workout_metadata_df: pd.DataFrame, local_timezone: str = "UTC") -> pd.DataFrame:
    """Normalize Polar workout_metadata rows into UTC start/end windows."""
    if workout_metadata_df.empty:
        return pd.DataFrame(columns=["workoutId", "start_time_utc", "end_time_utc", "duration_seconds"])

    workout_id_col = _find_column(workout_metadata_df.columns, ["workoutId", "workout_id"])
    if not workout_id_col:
        raise ValueError("workout_metadata data must include a workoutId column")

    date_col = _find_column(workout_metadata_df.columns, ["Date"])
    time_col = _find_column(workout_metadata_df.columns, ["Start time", "start_time", "startTime"])
    start_col = _find_column(workout_metadata_df.columns, ["start_datetime", "start_time_utc", "local_start_time"])
    end_col = _find_column(workout_metadata_df.columns, ["End time", "end_time", "end_datetime"])
    duration_col = _find_column(
        workout_metadata_df.columns,
        ["Duration", "Duration (s)", "Duration [s]", "duration", "duration_seconds"],
    )

    result = pd.DataFrame()
    result["workoutId"] = workout_metadata_df[workout_id_col].astype("string")

    if date_col and time_col:
        start_local = pd.to_datetime(
            workout_metadata_df[date_col].astype("string").str.strip()
            + " "
            + workout_metadata_df[time_col].astype("string").str.strip(),
            dayfirst=True,
            errors="coerce",
        )
    elif start_col:
        start_local = pd.to_datetime(workout_metadata_df[start_col], errors="coerce")
    else:
        raise ValueError("Could not find start time columns in workout_metadata")

    duration_seconds = (
        parse_duration_to_seconds(workout_metadata_df[duration_col]) if duration_col else pd.Series(index=workout_metadata_df.index, dtype="float64")
    )
    result["duration_seconds"] = duration_seconds
    result["start_time_utc"] = _to_utc(start_local, local_timezone)

    if end_col:
        result["end_time_utc"] = _to_utc(workout_metadata_df[end_col], local_timezone)
    else:
        result["end_time_utc"] = result["start_time_utc"] + pd.to_timedelta(result["duration_seconds"], unit="s")

    return result.dropna(subset=["workoutId", "start_time_utc", "end_time_utc"]).reset_index(drop=True)


def build_garmin_activity_windows(garmin_metadata_df: pd.DataFrame, local_timezone: str = "UTC") -> pd.DataFrame:
    """Normalize Garmin metadata rows into UTC start/end windows."""
    if garmin_metadata_df.empty:
        return pd.DataFrame(columns=["activity_id", "metadata_file", "start_time_utc", "end_time_utc", "duration_seconds"])

    activity_id_col = _find_column(garmin_metadata_df.columns, ["activity_id", "activityId", "activityid", "id"])
    if not activity_id_col:
        raise ValueError("Garmin metadata must include activity_id/activityId column")

    start_utc_col = _find_column(garmin_metadata_df.columns, ["start_time_utc", "startTimeGMT", "startTimeGmt"])
    start_local_col = _find_column(garmin_metadata_df.columns, ["start_time_local", "startTimeLocal"])
    duration_col = _find_column(
        garmin_metadata_df.columns,
        ["duration_seconds", "duration", "movingDuration", "elapsedDuration"],
    )
    file_col = _find_column(garmin_metadata_df.columns, ["metadata_file", "file_path", "file"])

    result = pd.DataFrame()
    result["activity_id"] = pd.to_numeric(garmin_metadata_df[activity_id_col], errors="coerce").astype("Int64")
    result["metadata_file"] = garmin_metadata_df[file_col].astype("string") if file_col else pd.Series(index=garmin_metadata_df.index, dtype="string")
    result["duration_seconds"] = (
        parse_duration_to_seconds(garmin_metadata_df[duration_col]) if duration_col else pd.Series(index=garmin_metadata_df.index, dtype="float64")
    )

    if start_utc_col:
        result["start_time_utc"] = _to_utc(garmin_metadata_df[start_utc_col], local_timezone)
    elif start_local_col:
        result["start_time_utc"] = _to_utc(garmin_metadata_df[start_local_col], local_timezone)
    else:
        raise ValueError("Could not find Garmin start time columns")

    result["end_time_utc"] = result["start_time_utc"] + pd.to_timedelta(result["duration_seconds"], unit="s")
    return result.dropna(subset=["activity_id", "start_time_utc", "end_time_utc"]).reset_index(drop=True)


def correlate_workouts_by_overlap(
    polar_windows_df: pd.DataFrame,
    garmin_windows_df: pd.DataFrame,
    tolerance_seconds: int = 300,
) -> pd.DataFrame:
    """Match Polar workouts to Garmin activity files based on window overlap."""
    columns = [
        "workoutId",
        "activity_id",
        "metadata_file",
        "overlap_seconds",
        "start_delta_seconds",
        "polar_start_utc",
        "polar_end_utc",
        "garmin_start_utc",
        "garmin_end_utc",
    ]
    if polar_windows_df.empty or garmin_windows_df.empty:
        return pd.DataFrame(columns=columns)

    matches: list[dict[str, object]] = []
    tolerance = float(tolerance_seconds)

    for _, polar_row in polar_windows_df.iterrows():
        p_start = polar_row["start_time_utc"]
        p_end = polar_row["end_time_utc"]
        if pd.isna(p_start) or pd.isna(p_end):
            continue

        for _, garmin_row in garmin_windows_df.iterrows():
            g_start = garmin_row["start_time_utc"]
            g_end = garmin_row["end_time_utc"]
            if pd.isna(g_start) or pd.isna(g_end):
                continue

            overlap_seconds = float((min(p_end, g_end) - max(p_start, g_start)).total_seconds())
            if overlap_seconds < -tolerance:
                continue

            matches.append(
                {
                    "workoutId": polar_row["workoutId"],
                    "activity_id": garmin_row["activity_id"],
                    "metadata_file": garmin_row["metadata_file"],
                    "overlap_seconds": overlap_seconds,
                    "start_delta_seconds": abs(float((p_start - g_start).total_seconds())),
                    "polar_start_utc": p_start,
                    "polar_end_utc": p_end,
                    "garmin_start_utc": g_start,
                    "garmin_end_utc": g_end,
                }
            )

    if not matches:
        return pd.DataFrame(columns=columns)

    matches_df = pd.DataFrame(matches, columns=columns)
    ranked = (
        matches_df.sort_values(["workoutId", "overlap_seconds", "start_delta_seconds"], ascending=[True, False, True])
        .drop_duplicates(subset=["workoutId"], keep="first")
        .reset_index(drop=True)
    )
    return ranked


__all__ = [
    "parse_duration_to_seconds",
    "normalize_polar_workout_windows",
    "build_garmin_activity_windows",
    "correlate_workouts_by_overlap",
]
