"""Validate Garmin destination databases against the transformed SQLite source."""
from __future__ import annotations

import argparse
import sys
from typing import Dict

import pandas as pd

from garmin_etl.config import load_garmin_configuration
from garmin_etl.transform import (
    SLEEP_COLUMNS,
    TIMESERIES_COLUMNS,
    WORKOUT_COLUMNS,
    transform_all,
)

_TABLES = {
    "workouts": ("garmin_workout_metadata", WORKOUT_COLUMNS, ["activity_id"]),
    "timeseries": (
        "garmin_timeseries",
        TIMESERIES_COLUMNS,
        ["activity_id", "record"],
    ),
    "sleep": ("garmin_sleep", SLEEP_COLUMNS, ["day"]),
}

_DATETIME_COLUMNS = {
    "workouts": ["start_time", "stop_time"],
    "timeseries": ["timestamp"],
    "sleep": ["day", "sleep_start", "sleep_end"],
}


def _load_duckdb(config: dict) -> Dict[str, pd.DataFrame]:
    import duckdb

    con = duckdb.connect(str(config["GARMIN_DUCKDB_PATH"]), read_only=True)
    try:
        return {
            name: con.execute(
                f"SELECT {', '.join(columns)} FROM {table}"
            ).fetchdf()
            for name, (table, columns, _) in _TABLES.items()
        }
    finally:
        con.close()


def _load_postgres(config: dict) -> Dict[str, pd.DataFrame]:
    from garmin_etl.storage.postgres import get_postgres_connection

    conn = get_postgres_connection(config)
    try:
        result: Dict[str, pd.DataFrame] = {}
        with conn.cursor() as cur:
            for name, (table, columns, _) in _TABLES.items():
                quoted = ", ".join(f'"{column}"' for column in columns)
                cur.execute(f"SELECT {quoted} FROM {table}")
                result[name] = pd.DataFrame(
                    cur.fetchall(),
                    columns=[column.name for column in cur.description],
                )
        return result
    finally:
        conn.close()


def _normalize(name: str, frame: pd.DataFrame) -> pd.DataFrame:
    _, columns, keys = _TABLES[name]
    normalized = frame[columns].copy()
    for column in _DATETIME_COLUMNS[name]:
        normalized[column] = pd.to_datetime(normalized[column], errors="coerce")
    normalized = normalized.where(normalized.notna(), pd.NA).convert_dtypes()
    return normalized.sort_values(keys).reset_index(drop=True)


def _assert_integrity(name: str, frame: pd.DataFrame) -> None:
    _, _, keys = _TABLES[name]
    duplicate_count = int(frame.duplicated(subset=keys).sum())
    if duplicate_count:
        raise AssertionError(f"{name}: found {duplicate_count} duplicate primary keys")

    if name == "workouts":
        if frame["workoutId"].isna().any():
            raise AssertionError("workouts: null workoutId values found")
        if frame["workoutId"].duplicated().any():
            raise AssertionError("workouts: duplicate workoutId values found")
        invalid_gps = ~frame["gps_source"].isin(["first_record", "activity_start", "none"])
        if invalid_gps.any():
            raise AssertionError("workouts: invalid gps_source values found")

    if name == "timeseries" and frame["activity_id"].isna().any():
        raise AssertionError("timeseries: null activity_id values found")

    if name == "sleep" and frame["day"].isna().any():
        raise AssertionError("sleep: null day values found")


def _assert_relationships(frames: Dict[str, pd.DataFrame]) -> None:
    workouts = frames["workouts"][["activity_id", "workoutId"]]
    timeseries = frames["timeseries"][["activity_id", "workoutId"]]
    linked = timeseries.merge(
        workouts,
        on="activity_id",
        how="left",
        suffixes=("_timeseries", "_workout"),
        indicator=True,
    )
    orphan_count = int((linked["_merge"] != "both").sum())
    if orphan_count:
        raise AssertionError(f"timeseries: found {orphan_count} orphan rows")

    mismatch_count = int(
        (
            linked["workoutId_timeseries"].fillna("")
            != linked["workoutId_workout"].fillna("")
        ).sum()
    )
    if mismatch_count:
        raise AssertionError(
            f"timeseries: found {mismatch_count} activity/workoutId mismatches"
        )


def _assert_matches_source(
    expected: Dict[str, pd.DataFrame],
    actual: Dict[str, pd.DataFrame],
    backend: str,
) -> None:
    for name in _TABLES:
        expected_frame = _normalize(name, expected[name])
        actual_frame = _normalize(name, actual[name])
        _assert_integrity(name, actual_frame)
        pd.testing.assert_frame_equal(
            actual_frame,
            expected_frame,
            check_dtype=False,
            check_exact=False,
            check_categorical=False,
            rtol=1e-12,
            atol=1e-12,
        )
        print(f"  - {name}: {len(actual_frame)} rows exactly match transformed source")

    _assert_relationships(actual)
    print(f"  - {backend}: primary keys and workout relationships are valid")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate Garmin databases against the transformed SQLite source."
    )
    parser.add_argument(
        "--backend",
        choices=("duckdb", "postgres", "both"),
        default="both",
    )
    args = parser.parse_args()

    try:
        config = load_garmin_configuration()
        print("Transforming the SQLite source for validation...")
        expected = transform_all(config)

        validated: Dict[str, Dict[str, pd.DataFrame]] = {}
        if args.backend in ("duckdb", "both"):
            print("Validating DuckDB...")
            validated["duckdb"] = _load_duckdb(config)
            _assert_matches_source(expected, validated["duckdb"], "DuckDB")

        if args.backend in ("postgres", "both"):
            print("Validating PostgreSQL...")
            validated["postgres"] = _load_postgres(config)
            _assert_matches_source(expected, validated["postgres"], "PostgreSQL")

        if args.backend == "both":
            print("Comparing DuckDB and PostgreSQL...")
            for name in _TABLES:
                pd.testing.assert_frame_equal(
                    _normalize(name, validated["duckdb"][name]),
                    _normalize(name, validated["postgres"][name]),
                    check_dtype=False,
                    check_exact=False,
                    check_categorical=False,
                    rtol=1e-12,
                    atol=1e-12,
                )
                print(f"  - {name}: backend rows match")

        print("Garmin database validation passed.")
    except Exception as exc:  # noqa: BLE001 - CLI boundary reports validation failure
        print(f"ERROR: Garmin database validation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
