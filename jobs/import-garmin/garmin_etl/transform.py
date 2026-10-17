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


def _chunk(values: list, size: int = 900):
    for start in range(0, len(values), size):
        yield values[start:start + size]


def _read_filtered(
    conn: sqlite3.Connection,
    base_sql: str,
    column: str,
    values: list,
    order_by: str = "",
) -> pd.DataFrame:
    """Read rows whose ``column`` is in ``values`` via chunked ``IN`` queries."""
    frames = []
    for chunk in _chunk([str(v) for v in values]):
        placeholders = ", ".join("?" for _ in chunk)
        sql = f"{base_sql} WHERE {column} IN ({placeholders})"
        if order_by:
            sql += f" ORDER BY {order_by}"
        frames.append(pd.read_sql_query(sql, conn, params=chunk))
    if not frames:
        return pd.read_sql_query(f"{base_sql} WHERE 1=0", conn)
    return pd.concat(frames, ignore_index=True)


def _read_activities(db_path: Path, activity_ids: Optional[list] = None) -> pd.DataFrame:
    conn = _connect_ro(db_path)
    try:
        if not _table_exists(conn, "activities"):
            raise ValueError(f"Table 'activities' not found in {db_path}")
        if activity_ids is None:
            return pd.read_sql_query("SELECT * FROM activities", conn)
        return _read_filtered(conn, "SELECT * FROM activities", "activity_id", activity_ids)
    finally:
        conn.close()


def _read_activity_records(db_path: Path, activity_ids: Optional[list] = None) -> pd.DataFrame:
    conn = _connect_ro(db_path)
    try:
        if not _table_exists(conn, "activity_records"):
            raise ValueError(f"Table 'activity_records' not found in {db_path}")
        if activity_ids is None:
            return pd.read_sql_query(
                "SELECT * FROM activity_records ORDER BY activity_id, record", conn
            )
        return _read_filtered(
            conn,
            "SELECT * FROM activity_records",
            "activity_id",
            activity_ids,
            order_by="activity_id, record",
        )
    finally:
        conn.close()


def _read_sleep(db_path: Path, days: Optional[list] = None) -> pd.DataFrame:
    conn = _connect_ro(db_path)
    try:
        if not _table_exists(conn, "sleep"):
            raise ValueError(f"Table 'sleep' not found in {db_path}")
        if days is None:
            return pd.read_sql_query("SELECT * FROM sleep", conn)
        iso_days = [d.isoformat() if hasattr(d, "isoformat") else str(d) for d in days]
        frames = []
        for chunk in _chunk(iso_days):
            placeholders = ", ".join("?" for _ in chunk)
            frames.append(
                pd.read_sql_query(
                    f"SELECT * FROM sleep WHERE date(day) IN ({placeholders})",
                    conn,
                    params=chunk,
                )
            )
        if not frames:
            return pd.read_sql_query("SELECT * FROM sleep WHERE 1=0", conn)
        return pd.concat(frames, ignore_index=True)
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

def transform_all(
    config: dict, existing_assignments: Optional[dict] = None
) -> Dict[str, pd.DataFrame]:
    """Read GarminDB SQLite DBs and return clean ``workouts``/``timeseries``/``sleep``.

    Performs preflight validation: source DBs exist, required tables present,
    no duplicate generated ``workoutId``, and ``(activity_id, record)`` is unique.
    ``existing_assignments`` optionally seeds the shared canonical ``workoutId``
    policy from committed destination assignments (default: none — legacy
    full-cache behavior).
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
    # workoutId is a downstream join key, so it must stay unique. Use the shared
    # canonical assignment policy (optionally seeded from committed destination
    # assignments) instead of renumbering in isolation.
    workout_id_map = compute_workout_id_map(workouts, existing_assignments)
    workouts = workouts.copy()
    workouts["workoutId"] = workouts["activity_id"].map(workout_id_map)

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


def _empty_typed_frame(columns: list, int_cols: list) -> pd.DataFrame:
    """Return an empty frame with the canonical columns and integer dtypes."""
    df = pd.DataFrame({column: pd.Series([], dtype="object") for column in columns})
    return _cast_int_cols(df, int_cols)


def empty_frames() -> Dict[str, pd.DataFrame]:
    """Correctly-typed empty ``workouts``/``timeseries``/``sleep`` frames."""
    return {
        "workouts": _empty_typed_frame(WORKOUT_COLUMNS, _WORKOUT_INT_COLS),
        "timeseries": _empty_typed_frame(TIMESERIES_COLUMNS, _TIMESERIES_INT_COLS),
        "sleep": _empty_typed_frame(SLEEP_COLUMNS, _SLEEP_INT_COLS),
    }


def transform_selected(
    config: dict,
    activity_ids: Optional[list] = None,
    sleep_days: Optional[list] = None,
    *,
    assign_workout_ids=None,
) -> Dict[str, pd.DataFrame]:
    """Transform only the selected activities and sleep days (delta path).

    Predicates are pushed into the SQLite reads (including ``activity_records``),
    so unrelated rows are never materialized. An empty activity/sleep selection
    yields a correctly-typed empty frame *without* requiring the corresponding
    source database.

    ``workoutId`` is left as the bare ``DD-MM-YYYY_HHMMSS`` base id here; the
    shared assignment policy must never renumber ids on a subset in isolation.
    The caller passes ``assign_workout_ids(workouts, timeseries) -> (workouts,
    timeseries)`` to apply the shared canonical stable-id policy.
    """
    db_dir = Path(config["GARMIN_DB_DIR"])
    activities_db = db_dir / ACTIVITIES_DB
    garmin_db = db_dir / GARMIN_DB

    selected_ids = [str(a) for a in (activity_ids or [])]
    selected_days = list(sleep_days or [])
    empty = empty_frames()

    if selected_ids:
        if not activities_db.exists():
            raise FileNotFoundError(
                f"GarminDB activities database not found: {activities_db}"
            )
        activities_df = _read_activities(activities_db, selected_ids)
        records_df = _read_activity_records(activities_db, selected_ids)
        workouts = build_workout_metadata(activities_df, records_df)

        missing_ids = int(workouts["workoutId"].isna().sum())
        if missing_ids:
            raise ValueError(
                f"{missing_ids} selected activities have an unparseable start_time "
                "→ null workoutId."
            )

        workout_id_map = dict(zip(workouts["activity_id"], workouts["workoutId"]))
        timeseries = build_timeseries(records_df, workout_id_map)

        # Selected timeseries rows must belong to selected metadata.
        meta_ids = set(workouts["activity_id"])
        ts_ids = {a for a in timeseries["activity_id"].dropna().tolist()}
        orphans = ts_ids - meta_ids
        if orphans:
            raise ValueError(
                f"Selected timeseries reference {len(orphans)} activity id(s) "
                "absent from the selected metadata."
            )
        if timeseries.duplicated(subset=["activity_id", "record"]).any():
            raise ValueError("Duplicate (activity_id, record) rows in selection.")
    else:
        workouts = empty["workouts"]
        timeseries = empty["timeseries"]

    if selected_days:
        if not garmin_db.exists():
            raise FileNotFoundError(f"GarminDB main database not found: {garmin_db}")
        sleep = build_sleep(_read_sleep(garmin_db, selected_days))
    else:
        sleep = empty["sleep"]

    if assign_workout_ids is not None:
        workouts, timeseries = assign_workout_ids(workouts, timeseries)

    print(
        f"  Selective transform: {len(workouts)} workouts, "
        f"{len(timeseries)} records, {len(sleep)} sleep nights"
    )
    return {"workouts": workouts, "timeseries": timeseries, "sleep": sleep}


def merge_existing_assignments(*maps: dict) -> dict:
    """Merge per-target ``{activity_id: workoutId}`` maps, rejecting conflicts.

    A conflicting prior mapping (the same activity assigned different workoutIds
    across destinations) is an error rather than something to rewrite. A reverse
    conflict (two different activities mapped to the same ``workoutId``) is also
    rejected — that would corrupt the join key.
    """
    merged: Dict[str, str] = {}
    for mapping in maps:
        for activity_id, workout_id in (mapping or {}).items():
            key = str(activity_id)
            if key in merged and merged[key] != workout_id:
                raise ValueError(
                    f"Conflicting workoutId assignment for activity {key}: "
                    f"{merged[key]!r} vs {workout_id!r}."
                )
            merged[key] = workout_id
    owner: Dict[str, str] = {}
    for activity_id, workout_id in merged.items():
        if workout_id in owner and owner[workout_id] != activity_id:
            raise ValueError(
                f"workoutId {workout_id!r} is assigned to multiple activities: "
                f"{owner[workout_id]!r} and {activity_id!r}."
            )
        owner[workout_id] = activity_id
    return merged


def compute_workout_id_map(
    workouts: pd.DataFrame, existing_assignments: Optional[dict] = None
) -> Dict[str, str]:
    """Return a stable ``{activity_id: workoutId}`` map for ``workouts``.

    Committed assignments in ``existing_assignments`` are preserved verbatim
    (never renumbered) and their ids are reserved. New activities take their
    bare ``DD-MM-YYYY_HHMMSS`` base id, or the next unused ``-N`` suffix when a
    base/suffix is already reserved. Ordering by ``start_time`` then
    ``activity_id`` makes new suffix allocation deterministic.
    """
    existing = {str(k): v for k, v in (existing_assignments or {}).items()}
    reserved = set(existing.values())
    base_by_aid = {
        str(a): w for a, w in zip(workouts["activity_id"], workouts["workoutId"])
    }

    ordered = workouts.sort_values(["start_time", "activity_id"], kind="stable")
    final: Dict[str, str] = {}
    collisions = 0
    for activity_id in ordered["activity_id"].astype(str):
        if activity_id in existing:
            final[activity_id] = existing[activity_id]
            continue
        base = base_by_aid[activity_id]
        if base is None or (isinstance(base, float) and pd.isna(base)):
            raise ValueError(
                f"Activity {activity_id} has a null base workoutId (unparseable "
                "start_time)."
            )
        candidate = base
        if candidate in reserved:
            suffix = 2
            while f"{base}-{suffix}" in reserved:
                suffix += 1
            candidate = f"{base}-{suffix}"
            collisions += 1
        reserved.add(candidate)
        final[activity_id] = candidate

    if collisions:
        print(
            f"  ⚠️  Assigned {collisions} same-second workoutId suffix(es) "
            "without renumbering existing activities"
        )
    return final


def assign_workout_ids(
    workouts: pd.DataFrame,
    timeseries: pd.DataFrame,
    existing_assignments: Optional[dict] = None,
):
    """Apply the shared canonical ``workoutId`` policy to both frames.

    Suitable as the ``assign_workout_ids`` hook of :func:`transform_selected`
    (bind ``existing_assignments`` with ``functools.partial``).
    """
    final = compute_workout_id_map(workouts, existing_assignments)
    workouts = workouts.copy()
    workouts["workoutId"] = workouts["activity_id"].astype(str).map(final)
    if timeseries is not None and not timeseries.empty:
        timeseries = timeseries.copy()
        timeseries["workoutId"] = timeseries["activity_id"].astype(str).map(final)
    return workouts, timeseries


__all__ = [
    "transform_all",
    "transform_selected",
    "empty_frames",
    "compute_workout_id_map",
    "assign_workout_ids",
    "merge_existing_assignments",
    "build_workout_metadata",
    "build_timeseries",
    "build_sleep",
    "WORKOUT_COLUMNS",
    "TIMESERIES_COLUMNS",
    "SLEEP_COLUMNS",
]
