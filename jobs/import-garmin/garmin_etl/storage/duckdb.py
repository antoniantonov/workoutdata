"""DuckDB storage backend for Garmin data.

Writes three ``garmin_``-prefixed tables into a dedicated DuckDB file (kept
separate from Polar's ``database_v2.duckdb``):

- ``garmin_workout_metadata`` (PK ``activity_id``; carries ``workoutId`` + GPS)
- ``garmin_timeseries``       (PK ``activity_id, record``; per-second HR/GPS)
- ``garmin_sleep``            (PK ``day``)

Tables use **explicit DDL**. Imports are idempotent via delete-by-id upsert.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

import duckdb  # type: ignore
import pandas as pd  # type: ignore

WORKOUT_METADATA_DDL = """
CREATE TABLE IF NOT EXISTS garmin_workout_metadata (
    activity_id      VARCHAR PRIMARY KEY,
    workoutId        VARCHAR NOT NULL,
    name             VARCHAR,
    sport            VARCHAR,
    sub_sport        VARCHAR,
    start_time       TIMESTAMP,
    stop_time        TIMESTAMP,
    elapsed_time_s   INTEGER,
    moving_time_s    INTEGER,
    distance         DOUBLE,
    calories         INTEGER,
    avg_hr           INTEGER,
    max_hr           INTEGER,
    avg_speed        DOUBLE,
    max_speed        DOUBLE,
    avg_cadence      INTEGER,
    ascent           DOUBLE,
    descent          DOUBLE,
    training_load    DOUBLE,
    training_effect  DOUBLE,
    gps_lat          DOUBLE,
    gps_long         DOUBLE,
    gps_source       VARCHAR
);
"""

TIMESERIES_DDL = """
CREATE TABLE IF NOT EXISTS garmin_timeseries (
    activity_id    VARCHAR NOT NULL,
    workoutId      VARCHAR,
    record         INTEGER NOT NULL,
    timestamp      TIMESTAMP,
    hr             INTEGER,
    position_lat   DOUBLE,
    position_long  DOUBLE,
    speed          DOUBLE,
    distance       DOUBLE,
    cadence        INTEGER,
    altitude       DOUBLE,
    temperature    DOUBLE,
    rr             DOUBLE,
    PRIMARY KEY (activity_id, record)
);
"""

SLEEP_DDL = """
CREATE TABLE IF NOT EXISTS garmin_sleep (
    day            DATE PRIMARY KEY,
    sleep_start    TIMESTAMP,
    sleep_end      TIMESTAMP,
    total_sleep_s  INTEGER,
    deep_sleep_s   INTEGER,
    light_sleep_s  INTEGER,
    rem_sleep_s    INTEGER,
    awake_s        INTEGER,
    avg_spo2       DOUBLE,
    avg_rr         DOUBLE,
    avg_stress     DOUBLE,
    score          INTEGER,
    qualifier      VARCHAR
);
"""


def _connect(config: dict) -> duckdb.DuckDBPyConnection:
    db_path = Path(config["GARMIN_DUCKDB_PATH"])
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(db_path))


def _connect_ro(config: dict) -> Optional[duckdb.DuckDBPyConnection]:
    """Open the DuckDB file **read-only** for destination discovery.

    Returns ``None`` when the file does not exist yet (a genuinely empty new
    destination). A file that exists but is locked, corrupt, or otherwise
    unreadable raises, so discovery aborts rather than silently reporting an
    empty inventory (which would trigger a mass re-download).
    """
    db_path = Path(config["GARMIN_DUCKDB_PATH"])
    if not db_path.exists():
        return None
    return duckdb.connect(str(db_path), read_only=True)


def _table_missing(con: duckdb.DuckDBPyConnection, table: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = ?", [table]
    ).fetchone()
    return row is None


def _chunked(values: list, size: int = 900):
    for start in range(0, len(values), size):
        yield values[start:start + size]


def ensure_tables(config: dict) -> None:
    """Create the Garmin tables if they do not already exist."""
    con = _connect(config)
    try:
        con.execute(WORKOUT_METADATA_DDL)
        con.execute(TIMESERIES_DDL)
        con.execute(SLEEP_DDL)
    finally:
        con.close()


def _assert_timeseries_within_metadata(workouts_df: pd.DataFrame, timeseries_df: pd.DataFrame) -> None:
    """Reject timeseries rows whose activity_id is absent from the metadata."""
    if timeseries_df.empty:
        return
    meta_ids = {str(a) for a in workouts_df["activity_id"].tolist()}
    ts_ids = {str(a) for a in timeseries_df["activity_id"].dropna().tolist()}
    orphans = ts_ids - meta_ids
    if orphans:
        raise ValueError(
            f"Refusing to write {len(orphans)} timeseries activity id(s) not "
            "present in the supplied workout metadata."
        )


def import_workouts(
    workouts_df: pd.DataFrame, timeseries_df: pd.DataFrame, config: dict
) -> dict:
    """Upsert selected workout metadata + timeseries (delta-safe, idempotent).

    Timeseries are replaced by the **selected workout-metadata ids**, so a
    selected zero-sample activity still clears its old records. An empty delta
    performs no DELETE/INSERT (the tables are still ensured for a new file).
    """
    _assert_timeseries_within_metadata(workouts_df, timeseries_df)
    con = _connect(config)
    try:
        con.execute(WORKOUT_METADATA_DDL)
        con.execute(TIMESERIES_DDL)

        if workouts_df.empty and timeseries_df.empty:
            meta_count = con.execute("SELECT COUNT(*) FROM garmin_workout_metadata").fetchone()[0]
            ts_count = con.execute("SELECT COUNT(*) FROM garmin_timeseries").fetchone()[0]
            print("  ℹ️  DuckDB workouts: empty delta — no rows changed")
            return {"workouts": meta_count, "timeseries": ts_count}

        con.register("workouts_view", workouts_df)
        con.register("timeseries_view", timeseries_df)

        con.execute("BEGIN TRANSACTION")
        try:
            # Delete timeseries by the SELECTED METADATA ids (not by ids present
            # in the incoming timeseries frame) so a zero-sample replacement
            # cannot leave old records behind.
            con.execute(
                "DELETE FROM garmin_timeseries WHERE activity_id IN "
                "(SELECT activity_id FROM workouts_view)"
            )
            con.execute(
                "DELETE FROM garmin_workout_metadata WHERE activity_id IN "
                "(SELECT activity_id FROM workouts_view)"
            )
            con.execute("INSERT INTO garmin_workout_metadata BY NAME SELECT * FROM workouts_view")
            con.execute("INSERT INTO garmin_timeseries BY NAME SELECT * FROM timeseries_view")
            con.execute("COMMIT")
        except Exception:
            try:
                con.execute("ROLLBACK")
            except Exception:
                pass  # Preserve the original exception
            raise

        meta_count = con.execute("SELECT COUNT(*) FROM garmin_workout_metadata").fetchone()[0]
        ts_count = con.execute("SELECT COUNT(*) FROM garmin_timeseries").fetchone()[0]
        print(
            f"  ✅ DuckDB workouts: upserted {len(workouts_df)} "
            f"(table total {meta_count}); timeseries rows {len(timeseries_df)} "
            f"(table total {ts_count})"
        )
        return {"workouts": meta_count, "timeseries": ts_count}
    finally:
        con.close()


def import_sleep(sleep_df: pd.DataFrame, config: dict) -> dict:
    """Upsert daily sleep rows (idempotent by day)."""
    con = _connect(config)
    try:
        con.execute(SLEEP_DDL)
        if sleep_df.empty:
            count = con.execute("SELECT COUNT(*) FROM garmin_sleep").fetchone()[0]
            print("  ℹ️  No sleep rows to import")
            return {"sleep": count}

        con.register("sleep_view", sleep_df)
        con.execute("BEGIN TRANSACTION")
        try:
            con.execute(
                "DELETE FROM garmin_sleep WHERE day IN "
                "(SELECT CAST(day AS DATE) FROM sleep_view)"
            )
            con.execute("INSERT INTO garmin_sleep BY NAME SELECT * FROM sleep_view")
            con.execute("COMMIT")
        except Exception:
            try:
                con.execute("ROLLBACK")
            except Exception:
                pass  # Preserve the original exception
            raise

        count = con.execute("SELECT COUNT(*) FROM garmin_sleep").fetchone()[0]
        print(f"  ✅ DuckDB sleep: upserted {len(sleep_df)} (table total {count})")
        return {"sleep": count}
    finally:
        con.close()


# =============================================================================
# Query helpers
# =============================================================================

def get_existing_activity_ids(config: dict, candidate_ids: Optional[Iterable[str]] = None) -> set:
    """Return committed ``activity_id`` values (read-only).

    ``candidate_ids=None`` returns every stored id (backwards-compatible "all").
    An **empty** collection returns an empty set — it means *none*, never "all".
    A non-empty collection is matched with chunked, parameterized ``IN`` queries.
    """
    if candidate_ids is not None:
        candidates = [str(c) for c in candidate_ids]
        if not candidates:
            return set()
    else:
        candidates = None

    con = _connect_ro(config)
    if con is None:
        return set()
    try:
        if _table_missing(con, "garmin_workout_metadata"):
            return set()
        if candidates is None:
            rows = con.execute(
                "SELECT activity_id FROM garmin_workout_metadata"
            ).fetchall()
            return {r[0] for r in rows}
        found: set = set()
        for chunk in _chunked(candidates):
            placeholders = ", ".join("?" for _ in chunk)
            rows = con.execute(
                "SELECT activity_id FROM garmin_workout_metadata "
                f"WHERE activity_id IN ({placeholders})",
                chunk,
            ).fetchall()
            found.update(r[0] for r in rows)
        return found
    finally:
        con.close()


def get_existing_sleep_days(config: dict, start_day, end_day) -> set:
    """Return committed sleep ``day`` values within ``[start_day, end_day]`` (read-only)."""
    con = _connect_ro(config)
    if con is None:
        return set()
    try:
        if _table_missing(con, "garmin_sleep"):
            return set()
        rows = con.execute(
            "SELECT day FROM garmin_sleep WHERE day BETWEEN ? AND ?",
            [start_day, end_day],
        ).fetchall()
        return {r[0] for r in rows}
    finally:
        con.close()


def get_workout_id_assignments(
    config: dict, activity_ids: Optional[Iterable[str]] = None
) -> dict:
    """Return the committed ``{activity_id: workoutId}`` mapping (read-only).

    ``activity_ids=None`` returns every assignment; an empty collection returns
    an empty mapping.
    """
    if activity_ids is not None:
        wanted = [str(a) for a in activity_ids]
        if not wanted:
            return {}
    else:
        wanted = None

    con = _connect_ro(config)
    if con is None:
        return {}
    try:
        if _table_missing(con, "garmin_workout_metadata"):
            return {}
        if wanted is None:
            rows = con.execute(
                "SELECT activity_id, workoutId FROM garmin_workout_metadata"
            ).fetchall()
            return {r[0]: r[1] for r in rows}
        assignments: dict = {}
        for chunk in _chunked(wanted):
            placeholders = ", ".join("?" for _ in chunk)
            rows = con.execute(
                "SELECT activity_id, workoutId FROM garmin_workout_metadata "
                f"WHERE activity_id IN ({placeholders})",
                chunk,
            ).fetchall()
            assignments.update({r[0]: r[1] for r in rows})
        return assignments
    finally:
        con.close()


def get_garmin_workout_metadata(config: dict, workout_ids: Optional[list] = None) -> pd.DataFrame:
    """Return workout metadata, optionally filtered by ``workoutId`` values."""
    con = _connect(config)
    try:
        if workout_ids:
            placeholders = ", ".join("?" for _ in workout_ids)
            return con.execute(
                f"SELECT * FROM garmin_workout_metadata WHERE workoutId IN ({placeholders}) "
                "ORDER BY start_time",
                workout_ids,
            ).fetchdf()
        return con.execute(
            "SELECT * FROM garmin_workout_metadata ORDER BY start_time"
        ).fetchdf()
    finally:
        con.close()


def get_garmin_timeseries(workout_ids: list, config: dict) -> pd.DataFrame:
    """Return per-second timeseries for the given ``workoutId`` values."""
    if not workout_ids:
        return pd.DataFrame()
    con = _connect(config)
    try:
        placeholders = ", ".join("?" for _ in workout_ids)
        return con.execute(
            f"SELECT * FROM garmin_timeseries WHERE workoutId IN ({placeholders}) "
            "ORDER BY workoutId, record",
            workout_ids,
        ).fetchdf()
    finally:
        con.close()


def get_garmin_sleep(config: dict) -> pd.DataFrame:
    """Return all daily sleep rows ordered by day."""
    con = _connect(config)
    try:
        return con.execute("SELECT * FROM garmin_sleep ORDER BY day").fetchdf()
    finally:
        con.close()


def delete_workout_by_id(activity_id: str, config: dict) -> None:
    """Delete a workout (metadata + timeseries) by ``activity_id``."""
    con = _connect(config)
    try:
        con.execute(
            "DELETE FROM garmin_timeseries WHERE activity_id = ?", (activity_id,)
        )
        con.execute(
            "DELETE FROM garmin_workout_metadata WHERE activity_id = ?", (activity_id,)
        )
        print(f"Deleted activity_id = '{activity_id}'")
    finally:
        con.close()


__all__ = [
    "ensure_tables",
    "import_workouts",
    "import_sleep",
    "get_existing_activity_ids",
    "get_existing_sleep_days",
    "get_workout_id_assignments",
    "get_garmin_workout_metadata",
    "get_garmin_timeseries",
    "get_garmin_sleep",
    "delete_workout_by_id",
]
