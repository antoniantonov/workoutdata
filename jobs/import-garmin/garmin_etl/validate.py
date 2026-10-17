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


# =============================================================================
# Delta-scope validation (a populated destination need NOT equal the whole cache)
# =============================================================================

def _storage_for(backend: str):
    if backend == "duckdb":
        from garmin_etl.storage import duckdb as storage
    else:
        from garmin_etl.storage import postgres as storage
    return storage


def _empty_selected() -> Dict[str, pd.DataFrame]:
    return {
        name: pd.DataFrame(columns=columns)
        for name, (_, columns, _) in _TABLES.items()
    }


def _select_duckdb(config: dict, activity_ids, sleep_days) -> Dict[str, pd.DataFrame]:
    import duckdb

    con = duckdb.connect(str(config["GARMIN_DUCKDB_PATH"]), read_only=True)
    try:
        frames = _empty_selected()
        aids = [str(a) for a in activity_ids]
        days = list(sleep_days)
        if aids:
            wp = ", ".join("?" for _ in aids)
            wcols = ", ".join(_TABLES["workouts"][1])
            frames["workouts"] = con.execute(
                f"SELECT {wcols} FROM garmin_workout_metadata WHERE activity_id IN ({wp})",
                aids,
            ).fetchdf()
            tcols = ", ".join(_TABLES["timeseries"][1])
            frames["timeseries"] = con.execute(
                f"SELECT {tcols} FROM garmin_timeseries WHERE activity_id IN ({wp})",
                aids,
            ).fetchdf()
        if days:
            dp = ", ".join("?" for _ in days)
            scols = ", ".join(_TABLES["sleep"][1])
            frames["sleep"] = con.execute(
                f"SELECT {scols} FROM garmin_sleep WHERE day IN ({dp})", days
            ).fetchdf()
        return frames
    finally:
        con.close()


def _select_postgres(config: dict, activity_ids, sleep_days) -> Dict[str, pd.DataFrame]:
    from garmin_etl.storage.postgres import get_postgres_connection

    conn = get_postgres_connection(config)
    try:
        conn.read_only = True
        frames = _empty_selected()
        aids = [str(a) for a in activity_ids]
        days = list(sleep_days)
        with conn.cursor() as cur:
            if aids:
                wcols = ", ".join(f'"{c}"' for c in _TABLES["workouts"][1])
                cur.execute(
                    f"SELECT {wcols} FROM garmin_workout_metadata "
                    "WHERE activity_id = ANY(%s)",
                    (aids,),
                )
                frames["workouts"] = pd.DataFrame(
                    cur.fetchall(), columns=[d.name for d in cur.description]
                )
                tcols = ", ".join(f'"{c}"' for c in _TABLES["timeseries"][1])
                cur.execute(
                    f"SELECT {tcols} FROM garmin_timeseries WHERE activity_id = ANY(%s)",
                    (aids,),
                )
                frames["timeseries"] = pd.DataFrame(
                    cur.fetchall(), columns=[d.name for d in cur.description]
                )
            if days:
                scols = ", ".join(f'"{c}"' for c in _TABLES["sleep"][1])
                cur.execute(
                    f"SELECT {scols} FROM garmin_sleep WHERE day = ANY(%s)", (days,)
                )
                frames["sleep"] = pd.DataFrame(
                    cur.fetchall(), columns=[d.name for d in cur.description]
                )
        return frames
    finally:
        conn.close()


def load_selected(config: dict, backend: str, activity_ids, sleep_days) -> Dict[str, pd.DataFrame]:
    """Read only the selected rows from ``backend`` (read-only)."""
    if backend == "duckdb":
        return _select_duckdb(config, activity_ids, sleep_days)
    return _select_postgres(config, activity_ids, sleep_days)


def assert_scope_present(
    config: dict, backend: str, activity_ids, sleep_days, storage=None
) -> None:
    """Fail unless every planned activity and valid sleep day is committed.

    This is the always-on completeness gate (independent of the deep ``--validate``
    comparison): a payload/key must not be treated as imported until it is
    actually present in its required destination. ``storage`` may be injected to
    target a specific backend module (used by the coordinator).
    """
    storage = storage or _storage_for(backend)
    wanted_ids = {str(a) for a in activity_ids}
    if wanted_ids:
        present = storage.get_existing_activity_ids(config, wanted_ids)
        missing = wanted_ids - present
        if missing:
            raise AssertionError(
                f"{backend}: {len(missing)} planned activity id(s) missing after import."
            )
    wanted_days = {d for d in sleep_days}
    if wanted_days:
        low, high = min(wanted_days), max(wanted_days)
        present_days = storage.get_existing_sleep_days(config, low, high)
        missing_days = wanted_days - set(present_days)
        if missing_days:
            raise AssertionError(
                f"{backend}: {len(missing_days)} valid sleep day(s) missing after import."
            )
    print(f"  - {backend}: all planned activity/sleep keys are present")


def assert_scope_matches_source(
    expected: Dict[str, pd.DataFrame],
    actual: Dict[str, pd.DataFrame],
    backend: str,
) -> None:
    """Deep-compare the selected rows to the transformed selected source.

    Uses the same normalization/tolerance/integrity checks as the full-cache
    validator, but only over the selected scope — a windowed destination is not
    required to equal the entire historical cache.
    """
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
        print(f"  - {backend} {name}: {len(actual_frame)} selected rows match source")
    _assert_relationships(actual)


def validate_delta(
    config: dict,
    backend: str,
    expected: Dict[str, pd.DataFrame],
    activity_ids,
    sleep_days,
    deep: bool = False,
) -> None:
    """Validate a single backend's delta: presence always, deep on request."""
    assert_scope_present(config, backend, activity_ids, sleep_days)
    if deep:
        actual = load_selected(config, backend, activity_ids, sleep_days)
        assert_scope_matches_source(expected, actual, backend)


def _parity_compare(name: str, left: pd.DataFrame, right: pd.DataFrame, first: str, second: str) -> int:
    """Compare two already-restricted, normalized frames; raise on any diff."""
    _, _, keys = _TABLES[name]
    left_sorted = left.sort_values(keys).reset_index(drop=True)
    right_sorted = right.sort_values(keys).reset_index(drop=True)
    try:
        pd.testing.assert_frame_equal(
            left_sorted, right_sorted,
            check_dtype=False, check_exact=False, check_categorical=False,
            rtol=1e-12, atol=1e-12,
        )
    except AssertionError as exc:
        raise AssertionError(
            f"{name}: {first}/{second} disagree on shared window data "
            f"(reconciliation error): {exc}"
        ) from exc
    return len(left_sorted)


def validate_window_parity(
    config: dict, backends, activity_ids, sleep_days
) -> None:
    """For ``both``: shared window data across backends must match exactly.

    Compares candidate activities/days present in *both* backends. For
    activities present in both, the **complete** timeseries record set is
    compared (record keys are not intersected), so a backend missing or having
    extra records for a shared activity is surfaced as a reconciliation error
    rather than silently hidden. This runs on ``--validate`` even for an
    otherwise no-op run, so a previously-committed mismatch cannot be masked on
    a later run merely because both targets now contain the key.
    """
    backends = list(backends)
    if len(backends) < 2:
        return
    loaded = {
        backend: load_selected(config, backend, activity_ids, sleep_days)
        for backend in backends
    }
    first, second = backends[0], backends[1]

    left_meta = _normalize("workouts", loaded[first]["workouts"])
    right_meta = _normalize("workouts", loaded[second]["workouts"])
    common_activities = set(left_meta["activity_id"]) & set(right_meta["activity_id"])
    if common_activities:
        n = _parity_compare(
            "workouts",
            left_meta[left_meta["activity_id"].isin(common_activities)],
            right_meta[right_meta["activity_id"].isin(common_activities)],
            first, second,
        )
        print(f"  - workouts: {n} shared activity/ies match across backends")

        # Compare the full record set for activities present in both backends.
        left_ts = _normalize("timeseries", loaded[first]["timeseries"])
        right_ts = _normalize("timeseries", loaded[second]["timeseries"])
        _parity_compare(
            "timeseries",
            left_ts[left_ts["activity_id"].isin(common_activities)],
            right_ts[right_ts["activity_id"].isin(common_activities)],
            first, second,
        )

    left_sleep = _normalize("sleep", loaded[first]["sleep"])
    right_sleep = _normalize("sleep", loaded[second]["sleep"])
    common_days = set(left_sleep["day"]) & set(right_sleep["day"])
    if common_days:
        n = _parity_compare(
            "sleep",
            left_sleep[left_sleep["day"].isin(common_days)],
            right_sleep[right_sleep["day"].isin(common_days)],
            first, second,
        )
        print(f"  - sleep: {n} shared day(s) match across backends")


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
