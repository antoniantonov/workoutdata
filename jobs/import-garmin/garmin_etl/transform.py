"""Transform layer: read GarminDB SQLite databases into clean DataFrames.

Reads the GarminDB-produced SQLite databases and returns three DataFrames with
**explicit, stable dtypes** (so downstream DDL never depends on pandas type
inference):

- ``workouts``  : one row per activity (metadata) incl. derived ``workoutId``
                  and the first valid GPS coordinate.
- ``timeseries``: per-second activity records (carries ``workoutId`` linkage).
- ``sleep``     : daily sleep summary.

Source tables (GarminDB):
- ``garmin_activities.db`` → ``activities``, ``activity_records``
- ``garmin.db``           → ``sleep``

Durations stored by GarminDB as ``HH:MM:SS[.ffffff]`` strings are converted to
integer seconds. Timestamps are kept as local-naive wall-clock values.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Dict, Optional

import pandas as pd  # type: ignore

ACTIVITIES_DB = "garmin_activities.db"
GARMIN_DB = "garmin.db"

# Canonical output columns (also the DDL contract for the storage layers).
WORKOUT_COLUMNS = [
    "activity_id", "workoutId", "name", "sport", "sub_sport",
    "start_time", "stop_time", "elapsed_time_s", "moving_time_s",
    "distance", "calories", "avg_hr", "max_hr", "avg_speed", "max_speed",
    "avg_cadence", "ascent", "descent", "training_load", "training_effect",
    "gps_lat", "gps_long", "gps_source",
]
TIMESERIES_COLUMNS = [
    "activity_id", "workoutId", "record", "timestamp", "hr",
    "position_lat", "position_long", "speed", "distance", "cadence",
    "altitude", "temperature", "rr",
]
SLEEP_COLUMNS = [
    "day", "sleep_start", "sleep_end", "total_sleep_s", "deep_sleep_s",
    "light_sleep_s", "rem_sleep_s", "awake_s", "avg_spo2", "avg_rr",
    "avg_stress", "score", "qualifier",
]

# Integer-typed columns per frame (loaded as pandas nullable Int64).
_WORKOUT_INT_COLS = [
    "elapsed_time_s", "moving_time_s", "calories", "avg_hr", "max_hr", "avg_cadence",
]
_TIMESERIES_INT_COLS = ["record", "hr", "cadence"]
_SLEEP_INT_COLS = [
    "total_sleep_s", "deep_sleep_s", "light_sleep_s", "rem_sleep_s", "awake_s", "score",
]


# =============================================================================
# Parsing helpers
# =============================================================================

def _duration_to_seconds(value) -> Optional[int]:
    """Convert a GarminDB ``HH:MM:SS[.ffffff]`` duration to integer seconds."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in ("none", "nan", "nat"):
        return None
    try:
        parts = text.split(":")
        if len(parts) == 3:
            hours, minutes, seconds = parts
        elif len(parts) == 2:
            hours, minutes, seconds = "0", parts[0], parts[1]
        else:
            return int(round(float(text)))
        total = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        return int(round(total))
    except (ValueError, TypeError):
        return None


def _to_datetime(series: pd.Series) -> pd.Series:
    """Parse a string column into local-naive datetimes (NaT on failure)."""
    return pd.to_datetime(series, errors="coerce")


def _build_workout_id(start_times: pd.Series) -> pd.Series:
    """Build ``DD-MM-YYYY_HHMMSS`` ids from activity start datetimes."""
    return start_times.dt.strftime("%d-%m-%Y_%H%M%S")


def _is_valid_coord(lat, lon) -> bool:
    """True only when both coordinates are present, in range, and not (0, 0)."""
    if lat is None or lon is None:
        return False
    if pd.isna(lat) or pd.isna(lon):
        return False
    if not (-90.0 <= float(lat) <= 90.0) or not (-180.0 <= float(lon) <= 180.0):
        return False
    if float(lat) == 0.0 and float(lon) == 0.0:
        return False
    return True


# =============================================================================
# SQLite readers
# =============================================================================

def _connect_ro(db_path: Path) -> sqlite3.Connection:
    """Open a SQLite database read-only with a busy timeout."""
    uri = f"file:{db_path}?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=30)


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _read_activities(db_path: Path) -> pd.DataFrame:
    conn = _connect_ro(db_path)
    try:
        if not _table_exists(conn, "activities"):
            raise ValueError(f"Table 'activities' not found in {db_path}")
        return pd.read_sql_query("SELECT * FROM activities", conn)
    finally:
        conn.close()


def _read_activity_records(db_path: Path) -> pd.DataFrame:
    conn = _connect_ro(db_path)
    try:
        if not _table_exists(conn, "activity_records"):
            raise ValueError(f"Table 'activity_records' not found in {db_path}")
        return pd.read_sql_query(
            "SELECT * FROM activity_records ORDER BY activity_id, record", conn
        )
    finally:
        conn.close()


def _read_sleep(db_path: Path) -> pd.DataFrame:
    conn = _connect_ro(db_path)
    try:
        if not _table_exists(conn, "sleep"):
            raise ValueError(f"Table 'sleep' not found in {db_path}")
        return pd.read_sql_query('SELECT * FROM sleep', conn)
    finally:
        conn.close()


# =============================================================================
# First-GPS computation
# =============================================================================

def _first_gps_by_activity(records_df: pd.DataFrame) -> Dict[str, tuple]:
    """Return {activity_id: (lat, long)} for the first valid GPS fix.

    "First" = lowest ``record`` (then ``timestamp``) where both coordinates are
    valid. Activities without a valid fix are absent from the mapping.
    """
    if records_df.empty:
        return {}

    gps = records_df[["activity_id", "record", "timestamp", "position_lat", "position_long"]].copy()
    gps = gps.sort_values(["activity_id", "record", "timestamp"])

    result: Dict[str, tuple] = {}
    for activity_id, group in gps.groupby("activity_id", sort=False):
        for _, row in group.iterrows():
            lat, lon = row["position_lat"], row["position_long"]
            if _is_valid_coord(lat, lon):
                result[str(activity_id)] = (float(lat), float(lon))
                break
    return result


# =============================================================================
# Frame builders
# =============================================================================

def _cast_int_cols(df: pd.DataFrame, columns: list) -> pd.DataFrame:
    for col in columns:
        if col in df.columns:
            df[col] = pd.array(
                pd.to_numeric(df[col], errors="coerce").round(), dtype="Int64"
            )
    return df


def build_workout_metadata(
    activities_df: pd.DataFrame, records_df: pd.DataFrame
) -> pd.DataFrame:
    """Build the ``garmin_workout_metadata`` frame (workoutId + first GPS)."""
    df = activities_df.copy()
    df["activity_id"] = df["activity_id"].astype(str)

    df["start_time"] = _to_datetime(df.get("start_time"))
    df["stop_time"] = _to_datetime(df.get("stop_time"))
    df["workoutId"] = _build_workout_id(df["start_time"])

    df["elapsed_time_s"] = df.get("elapsed_time").map(_duration_to_seconds)
    df["moving_time_s"] = df.get("moving_time").map(_duration_to_seconds)

    first_gps = _first_gps_by_activity(records_df)

    def _gps_for(row):
        aid = row["activity_id"]
        if aid in first_gps:
            lat, lon = first_gps[aid]
            return pd.Series([lat, lon, "first_record"])
        # Fallback to the activity-level start coordinate when valid.
        slat, slon = row.get("start_lat"), row.get("start_long")
        if _is_valid_coord(slat, slon):
            return pd.Series([float(slat), float(slon), "activity_start"])
        return pd.Series([None, None, "none"])

    df[["gps_lat", "gps_long", "gps_source"]] = df.apply(_gps_for, axis=1)

    # Ensure all output columns exist (some optional in older GarminDB schemas).
    for col in WORKOUT_COLUMNS:
        if col not in df.columns:
            df[col] = None

    df = df[WORKOUT_COLUMNS].copy()
    df = _cast_int_cols(df, _WORKOUT_INT_COLS)
    for col in ("distance", "avg_speed", "max_speed", "ascent", "descent",
                "training_load", "training_effect", "gps_lat", "gps_long"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ("activity_id", "workoutId", "name", "sport", "sub_sport", "gps_source"):
        df[col] = df[col].astype(object).where(df[col].notna(), None)
    return df.reset_index(drop=True)


def build_timeseries(records_df: pd.DataFrame, workout_id_map: Dict[str, str]) -> pd.DataFrame:
    """Build the ``garmin_timeseries`` frame, attaching ``workoutId``."""
    df = records_df.copy()
    df["activity_id"] = df["activity_id"].astype(str)
    df["workoutId"] = df["activity_id"].map(workout_id_map)
    df["timestamp"] = _to_datetime(df.get("timestamp"))

    for col in TIMESERIES_COLUMNS:
        if col not in df.columns:
            df[col] = None

    df = df[TIMESERIES_COLUMNS].copy()
    df = _cast_int_cols(df, _TIMESERIES_INT_COLS)
    for col in ("position_lat", "position_long", "speed", "distance",
                "altitude", "temperature", "rr"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ("activity_id", "workoutId"):
        df[col] = df[col].astype(object).where(df[col].notna(), None)
    return df.reset_index(drop=True)


def build_sleep(sleep_df: pd.DataFrame) -> pd.DataFrame:
    """Build the ``garmin_sleep`` frame (durations → seconds; ``end`` renamed)."""
    df = sleep_df.copy()
    df["day"] = _to_datetime(df.get("day")).dt.normalize()
    df["sleep_start"] = _to_datetime(df.get("start"))
    df["sleep_end"] = _to_datetime(df.get("end"))

    df["total_sleep_s"] = df.get("total_sleep").map(_duration_to_seconds)
    df["deep_sleep_s"] = df.get("deep_sleep").map(_duration_to_seconds)
    df["light_sleep_s"] = df.get("light_sleep").map(_duration_to_seconds)
    df["rem_sleep_s"] = df.get("rem_sleep").map(_duration_to_seconds)
    df["awake_s"] = df.get("awake").map(_duration_to_seconds)

    for col in SLEEP_COLUMNS:
        if col not in df.columns:
            df[col] = None

    df = df[SLEEP_COLUMNS].copy()
    df = _cast_int_cols(df, _SLEEP_INT_COLS)
    for col in ("avg_spo2", "avg_rr", "avg_stress"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["qualifier"] = df["qualifier"].astype(object).where(df["qualifier"].notna(), None)
    # Drop GarminDB placeholder rows that carry no usable sleep information.
    # GarminDB seeds empty rows (total_sleep = 0, null timestamps) for days with
    # no recorded sleep, so a row counts only when it has real sleep duration or a
    # sleep start timestamp.
    has_duration = pd.to_numeric(df["total_sleep_s"], errors="coerce").fillna(0) > 0
    has_start = df["sleep_start"].notna()
    df = df[has_duration | has_start]
    return df.reset_index(drop=True)


# =============================================================================
# Orchestration + preflight
# =============================================================================

def transform_all(config: dict) -> Dict[str, pd.DataFrame]:
    """Read GarminDB SQLite DBs and return clean ``workouts``/``timeseries``/``sleep``.

    Performs preflight validation: source DBs exist, required tables present,
    no duplicate generated ``workoutId``, and ``(activity_id, record)`` is unique.
    """
    db_dir = Path(config["GARMIN_DB_DIR"])
    activities_db = db_dir / ACTIVITIES_DB
    garmin_db = db_dir / GARMIN_DB

    if not activities_db.exists():
        raise FileNotFoundError(f"GarminDB activities database not found: {activities_db}")
    if not garmin_db.exists():
        raise FileNotFoundError(f"GarminDB main database not found: {garmin_db}")

    print(f"  Reading activities from {activities_db}")
    activities_df = _read_activities(activities_db)
    print(f"  Reading activity_records from {activities_db}")
    records_df = _read_activity_records(activities_db)
    print(f"  Reading sleep from {garmin_db}")
    sleep_df = _read_sleep(garmin_db)

    workouts = build_workout_metadata(activities_df, records_df)

    # ── workoutId integrity ─────────────────────────────────────────────────
    # A null workoutId means start_time was unparseable → no reliable linkage.
    missing_ids = int(workouts["workoutId"].isna().sum())
    if missing_ids:
        raise ValueError(
            f"{missing_ids} activities have an unparseable start_time → null workoutId."
        )
    # workoutId is used as a join key downstream, so it must stay unique. Rather
    # than fail the whole batch on a rare same-second collision, disambiguate the
    # later activities with a numeric suffix (activity_id remains the durable PK).
    workouts = _disambiguate_workout_ids(workouts)

    workout_id_map = dict(zip(workouts["activity_id"], workouts["workoutId"]))
    timeseries = build_timeseries(records_df, workout_id_map)
    sleep = build_sleep(sleep_df)

    if timeseries.duplicated(subset=["activity_id", "record"]).any():
        raise ValueError("Duplicate (activity_id, record) rows found in activity_records.")

    gps_count = int((workouts["gps_source"] != "none").sum())
    print(
        f"  Transformed: {len(workouts)} workouts ({gps_count} with GPS), "
        f"{len(timeseries)} records, {len(sleep)} sleep nights"
    )

    return {"workouts": workouts, "timeseries": timeseries, "sleep": sleep}


def _disambiguate_workout_ids(workouts: pd.DataFrame) -> pd.DataFrame:
    """Ensure ``workoutId`` is unique by suffixing same-second collisions.

    The first activity keeps the bare ``DD-MM-YYYY_HHMMSS`` id; subsequent
    activities sharing it (ordered by ``start_time``, ``activity_id``) get
    ``-2``, ``-3``, … so downstream joins on ``workoutId`` stay correct.
    """
    if not workouts["workoutId"].duplicated().any():
        return workouts

    df = workouts.sort_values(["workoutId", "start_time", "activity_id"]).copy()
    counts: Dict[str, int] = {}
    new_ids = []
    collisions = set()
    for wid in df["workoutId"]:
        n = counts.get(wid, 0) + 1
        counts[wid] = n
        if n == 1:
            new_ids.append(wid)
        else:
            collisions.add(wid)
            new_ids.append(f"{wid}-{n}")
    df["workoutId"] = new_ids
    print(
        f"  ⚠️  Disambiguated {len(collisions)} same-second workoutId collision(s) "
        f"with numeric suffixes: {sorted(collisions)}"
    )
    return df.reset_index(drop=True)


__all__ = [
    "transform_all",
    "build_workout_metadata",
    "build_timeseries",
    "build_sleep",
    "WORKOUT_COLUMNS",
    "TIMESERIES_COLUMNS",
    "SLEEP_COLUMNS",
]
