"""PostgreSQL storage backend for Garmin data.

Mirrors :mod:`garmin_etl.storage.duckdb` for PostgreSQL using ``psycopg`` (v3).
Creates three ``garmin_``-prefixed tables with explicit DDL and performs
idempotent delete-by-id upserts. Nullable integers are bound as ``None`` (never
``NaN``) so PostgreSQL integer columns accept them.
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np  # type: ignore
import pandas as pd  # type: ignore
import psycopg  # type: ignore

from garmin_etl.transform import (
    SLEEP_COLUMNS,
    TIMESERIES_COLUMNS,
    WORKOUT_COLUMNS,
)

WORKOUT_METADATA_DDL = """
CREATE TABLE IF NOT EXISTS garmin_workout_metadata (
    activity_id      TEXT PRIMARY KEY,
    "workoutId"      TEXT NOT NULL,
    name             TEXT,
    sport            TEXT,
    sub_sport        TEXT,
    start_time       TIMESTAMP,
    stop_time        TIMESTAMP,
    elapsed_time_s   INTEGER,
    moving_time_s    INTEGER,
    distance         DOUBLE PRECISION,
    calories         INTEGER,
    avg_hr           INTEGER,
    max_hr           INTEGER,
    avg_speed        DOUBLE PRECISION,
    max_speed        DOUBLE PRECISION,
    avg_cadence      INTEGER,
    ascent           DOUBLE PRECISION,
    descent          DOUBLE PRECISION,
    training_load    DOUBLE PRECISION,
    training_effect  DOUBLE PRECISION,
    gps_lat          DOUBLE PRECISION,
    gps_long         DOUBLE PRECISION,
    gps_source       TEXT
);
"""

TIMESERIES_DDL = """
CREATE TABLE IF NOT EXISTS garmin_timeseries (
    activity_id    TEXT NOT NULL,
    "workoutId"    TEXT,
    record         INTEGER NOT NULL,
    "timestamp"    TIMESTAMP,
    hr             INTEGER,
    position_lat   DOUBLE PRECISION,
    position_long  DOUBLE PRECISION,
    speed          DOUBLE PRECISION,
    distance       DOUBLE PRECISION,
    cadence        INTEGER,
    altitude       DOUBLE PRECISION,
    temperature    DOUBLE PRECISION,
    rr             DOUBLE PRECISION,
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
    avg_spo2       DOUBLE PRECISION,
    avg_rr         DOUBLE PRECISION,
    avg_stress     DOUBLE PRECISION,
    score          INTEGER,
    qualifier      TEXT
);
"""


def get_postgres_connection(config: dict):
    """Open a PostgreSQL connection from configuration."""
    if config is None:
        raise ValueError("config parameter is required and cannot be None")

    conn_string = config.get("POSTGRES_CONNECTION_STRING")
    if conn_string:
        return psycopg.connect(conn_string)

    host = config.get("POSTGRES_HOST")
    port = config.get("POSTGRES_PORT", 5432)
    database = config.get("POSTGRES_DATABASE")
    user = config.get("POSTGRES_USER")
    password = config.get("POSTGRES_PASSWORD")
    sslmode = config.get("POSTGRES_SSLMODE", "prefer")

    if not all([host, database, user, password]):
        raise ValueError(
            "Missing required PostgreSQL configuration. Provide POSTGRES_CONNECTION_STRING "
            "or all of: POSTGRES_HOST, POSTGRES_DATABASE, POSTGRES_USER, POSTGRES_PASSWORD"
        )

    return psycopg.connect(
        host=host, port=port, dbname=database, user=user, password=password, sslmode=sslmode
    )


def _to_py(value):
    """Convert a pandas/numpy scalar to a native Python value (NA/NaN/NaT → None)."""
    if value is None:
        return None
    if value is pd.NaT or value is pd.NA:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    if isinstance(value, np.datetime64):
        ts = pd.Timestamp(value)
        return None if ts is pd.NaT else ts.to_pydatetime()
    if isinstance(value, np.generic):
        return value.item()
    return value


def _prepare_rows(df: pd.DataFrame, columns: list) -> list:
    """Build a list of native-Python row tuples in ``columns`` order."""
    ordered = df[columns]
    return [tuple(_to_py(v) for v in row) for row in ordered.itertuples(index=False, name=None)]


def _quoted(columns: list) -> str:
    return ", ".join(f'"{c}"' for c in columns)


def _insert_sql(table: str, columns: list) -> str:
    cols = _quoted(columns)
    placeholders = ", ".join(["%s"] * len(columns))
    return f"INSERT INTO {table} ({cols}) VALUES ({placeholders})"


def ensure_tables(config: dict) -> None:
    """Create the Garmin tables if they do not already exist."""
    conn = get_postgres_connection(config)
    try:
        with conn.cursor() as cur:
            cur.execute(WORKOUT_METADATA_DDL)
            cur.execute(TIMESERIES_DDL)
            cur.execute(SLEEP_DDL)
        conn.commit()
    finally:
        conn.close()


def import_workouts(
    workouts_df: pd.DataFrame, timeseries_df: pd.DataFrame, config: dict
) -> dict:
    """Upsert workout metadata + per-second timeseries (idempotent by activity_id)."""
    conn = get_postgres_connection(config)
    try:
        with conn.cursor() as cur:
            cur.execute(WORKOUT_METADATA_DDL)
            cur.execute(TIMESERIES_DDL)

            activity_ids = [str(a) for a in workouts_df["activity_id"].tolist()]
            ts_activity_ids = [
                str(a) for a in timeseries_df["activity_id"].dropna().unique().tolist()
            ]

            if ts_activity_ids:
                cur.execute(
                    "DELETE FROM garmin_timeseries WHERE activity_id = ANY(%s)",
                    (ts_activity_ids,),
                )
            if activity_ids:
                cur.execute(
                    "DELETE FROM garmin_workout_metadata WHERE activity_id = ANY(%s)",
                    (activity_ids,),
                )

            cur.executemany(
                _insert_sql("garmin_workout_metadata", WORKOUT_COLUMNS),
                _prepare_rows(workouts_df, WORKOUT_COLUMNS),
            )
            cur.executemany(
                _insert_sql("garmin_timeseries", TIMESERIES_COLUMNS),
                _prepare_rows(timeseries_df, TIMESERIES_COLUMNS),
            )

            cur.execute("SELECT COUNT(*) FROM garmin_workout_metadata")
            meta_count = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM garmin_timeseries")
            ts_count = cur.fetchone()[0]
        conn.commit()
        print(
            f"  ✅ PostgreSQL workouts: upserted {len(workouts_df)} "
            f"(table total {meta_count}); timeseries rows {len(timeseries_df)} "
            f"(table total {ts_count})"
        )
        return {"workouts": meta_count, "timeseries": ts_count}
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass  # Preserve the original exception
        raise
    finally:
        conn.close()


def import_sleep(sleep_df: pd.DataFrame, config: dict) -> dict:
    """Upsert daily sleep rows (idempotent by day)."""
    conn = get_postgres_connection(config)
    try:
        with conn.cursor() as cur:
            cur.execute(SLEEP_DDL)
            if sleep_df.empty:
                cur.execute("SELECT COUNT(*) FROM garmin_sleep")
                count = cur.fetchone()[0]
                conn.commit()
                print("  ℹ️  No sleep rows to import")
                return {"sleep": count}

            days = [_to_py(d) for d in sleep_df["day"].tolist()]
            days = [d.date() if hasattr(d, "date") else d for d in days if d is not None]
            if days:
                cur.execute("DELETE FROM garmin_sleep WHERE day = ANY(%s)", (days,))

            cur.executemany(
                _insert_sql("garmin_sleep", SLEEP_COLUMNS),
                _prepare_rows(sleep_df, SLEEP_COLUMNS),
            )
            cur.execute("SELECT COUNT(*) FROM garmin_sleep")
            count = cur.fetchone()[0]
        conn.commit()
        print(f"  ✅ PostgreSQL sleep: upserted {len(sleep_df)} (table total {count})")
        return {"sleep": count}
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass  # Preserve the original exception
        raise
    finally:
        conn.close()


# =============================================================================
# Query helpers
# =============================================================================

def get_existing_activity_ids(config: dict) -> set:
    """Return the set of activity_ids already present in workout metadata."""
    conn = get_postgres_connection(config)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT EXISTS (SELECT FROM information_schema.tables "
                "WHERE table_name = 'garmin_workout_metadata')"
            )
            if not cur.fetchone()[0]:
                return set()
            cur.execute("SELECT activity_id FROM garmin_workout_metadata")
            return {r[0] for r in cur.fetchall()}
    finally:
        conn.close()


def get_garmin_workout_metadata(config: dict, workout_ids: Optional[list] = None) -> pd.DataFrame:
    """Return workout metadata, optionally filtered by ``workoutId`` values."""
    conn = get_postgres_connection(config)
    try:
        with conn.cursor() as cur:
            if workout_ids:
                cur.execute(
                    'SELECT * FROM garmin_workout_metadata WHERE "workoutId" = ANY(%s) '
                    "ORDER BY start_time",
                    (list(workout_ids),),
                )
            else:
                cur.execute("SELECT * FROM garmin_workout_metadata ORDER BY start_time")
            cols = [d[0] for d in cur.description]
            return pd.DataFrame(cur.fetchall(), columns=cols)
    finally:
        conn.close()


def get_garmin_timeseries(workout_ids: list, config: dict) -> pd.DataFrame:
    """Return per-second timeseries for the given ``workoutId`` values."""
    if not workout_ids:
        return pd.DataFrame()
    conn = get_postgres_connection(config)
    try:
        with conn.cursor() as cur:
            cur.execute(
                'SELECT * FROM garmin_timeseries WHERE "workoutId" = ANY(%s) '
                "ORDER BY \"workoutId\", record",
                (list(workout_ids),),
            )
            cols = [d[0] for d in cur.description]
            return pd.DataFrame(cur.fetchall(), columns=cols)
    finally:
        conn.close()


def get_garmin_sleep(config: dict) -> pd.DataFrame:
    """Return all daily sleep rows ordered by day."""
    conn = get_postgres_connection(config)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM garmin_sleep ORDER BY day")
            cols = [d[0] for d in cur.description]
            return pd.DataFrame(cur.fetchall(), columns=cols)
    finally:
        conn.close()


def delete_workout_by_id(activity_id: str, config: dict) -> None:
    """Delete a workout (metadata + timeseries) by ``activity_id``."""
    conn = get_postgres_connection(config)
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM garmin_timeseries WHERE activity_id = %s", (activity_id,))
            cur.execute("DELETE FROM garmin_workout_metadata WHERE activity_id = %s", (activity_id,))
        conn.commit()
        print(f"Deleted activity_id = '{activity_id}'")
    finally:
        conn.close()


__all__ = [
    "get_postgres_connection",
    "ensure_tables",
    "import_workouts",
    "import_sleep",
    "get_existing_activity_ids",
    "get_garmin_workout_metadata",
    "get_garmin_timeseries",
    "get_garmin_sleep",
    "delete_workout_by_id",
]
