---
jupytext:
  text_representation:
    extension: .md
    format_name: myst
    format_version: 0.13
    jupytext_version: 1.16.6
kernelspec:
  display_name: Python 3
  language: python
  name: python3
---

# Correlate Polar PostgreSQL workouts with Garmin metadata

This notebook:

1. Reads Polar `workout_metadata` from PostgreSQL.
2. Downloads Garmin activity metadata and stores each activity metadata file locally.
3. Writes Garmin metadata into DuckDB table `garmin_metadata`.
4. Correlates Polar `workoutId` values to Garmin metadata files by overlapping activity windows.

> Secrets are read from environment variables only. Do not hardcode credentials.

## Expected environment variables

- PostgreSQL: `POSTGRES_CONNECTION_STRING` **or** `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DATABASE`, `POSTGRES_USER`, `POSTGRES_PASSWORD`
- DuckDB path: `DUCKDB_PATH` (optional; default: `database_v2.duckdb`)
- Garmin auth: `GARMIN_EMAIL` / `GARMIN_PASSWORD` **or** `GARMIN_SESSION_PATH`
- Optional:
  - `GARMIN_ACTIVITY_LIMIT` (default: `200`)
  - `GARMIN_DOWNLOAD_DIR` (default: `/tmp/garmin_metadata`)
  - `GARMIN_TIME_TOLERANCE_SECONDS` (default: `300`)
  - `POLAR_LOCAL_TIMEZONE` (default: `UTC`)

## Optional Docker test environment for PostgreSQL

```bash
docker compose up -d postgres
docker compose ps
```

```{code-cell} ipython3
import json
import os
import sys
from pathlib import Path

import duckdb
import importlib
import pandas as pd

repo_root = Path.cwd().parent.parent if "garmin/notebooks" in str(Path.cwd()) else Path.cwd()
sys.path.insert(0, str(repo_root))

from polar.storage import postgres as postgres_storage
from polar.utils.workout_correlation import (
    build_garmin_activity_windows,
    correlate_workouts_by_overlap,
    normalize_polar_workout_windows,
)

importlib.reload(postgres_storage)
```

```{code-cell} ipython3
POSTGRES_CONFIG = {
    "POSTGRES_CONNECTION_STRING": os.getenv("POSTGRES_CONNECTION_STRING"),
    "POSTGRES_HOST": os.getenv("POSTGRES_HOST"),
    "POSTGRES_PORT": os.getenv("POSTGRES_PORT", "5432"),
    "POSTGRES_DATABASE": os.getenv("POSTGRES_DATABASE", "workout_data"),
    "POSTGRES_USER": os.getenv("POSTGRES_USER"),
    "POSTGRES_PASSWORD": os.getenv("POSTGRES_PASSWORD"),
}

DUCKDB_PATH = Path(os.getenv("DUCKDB_PATH", str(repo_root / "database_v2.duckdb")))
GARMIN_ACTIVITY_LIMIT = int(os.getenv("GARMIN_ACTIVITY_LIMIT", "200"))
GARMIN_DOWNLOAD_DIR = Path(os.getenv("GARMIN_DOWNLOAD_DIR", "/tmp/garmin_metadata"))
GARMIN_TIME_TOLERANCE_SECONDS = int(os.getenv("GARMIN_TIME_TOLERANCE_SECONDS", "300"))
POLAR_LOCAL_TIMEZONE = os.getenv("POLAR_LOCAL_TIMEZONE", "UTC")

GARMIN_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
(GARMIN_DOWNLOAD_DIR / "activities").mkdir(parents=True, exist_ok=True)

print(f"DuckDB: {DUCKDB_PATH}")
print(f"Garmin download dir: {GARMIN_DOWNLOAD_DIR}")
print(f"Time overlap tolerance: {GARMIN_TIME_TOLERANCE_SECONDS} seconds")
```

## Load Polar workout metadata from PostgreSQL

```{code-cell} ipython3
def load_polar_workout_metadata(config: dict) -> pd.DataFrame:
    conn = postgres_storage.get_postgres_connection(config)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_name = 'workout_metadata'
                )
            """)
            if not cur.fetchone()[0]:
                raise RuntimeError("PostgreSQL table workout_metadata does not exist.")

            cur.execute('SELECT * FROM workout_metadata ORDER BY "workoutId"')
            rows = cur.fetchall()
            columns = [desc[0] for desc in cur.description]
            return pd.DataFrame(rows, columns=columns)
    finally:
        conn.close()


polar_metadata_df = load_polar_workout_metadata(POSTGRES_CONFIG)
polar_windows_df = normalize_polar_workout_windows(
    polar_metadata_df,
    local_timezone=POLAR_LOCAL_TIMEZONE,
)

print(f"Loaded {len(polar_metadata_df)} Polar metadata rows")
print(f"Usable Polar workout windows: {len(polar_windows_df)}")
polar_windows_df.head()
```

## Download Garmin metadata

```{code-cell} ipython3
def download_garmin_activity_metadata(limit: int, output_dir: Path) -> pd.DataFrame:
    try:
        import garth
    except ImportError as exc:
        raise ImportError(
            "garth is required for Garmin metadata download. Install with: pip install garth"
        ) from exc

    session_path = Path(os.getenv("GARMIN_SESSION_PATH", str(output_dir / "garth_session")))
    if session_path.exists():
        garth.resume(str(session_path))
    else:
        email = os.getenv("GARMIN_EMAIL")
        password = os.getenv("GARMIN_PASSWORD")
        if not email or not password:
            raise ValueError("Set GARMIN_EMAIL/GARMIN_PASSWORD or provide GARMIN_SESSION_PATH")
        garth.login(email, password)
        garth.save(str(session_path))

    activities = garth.connectapi(
        "/activitylist-service/activities/search/activities",
        params={"start": 0, "limit": limit},
    )

    rows = []
    for activity in activities:
        activity_id = activity.get("activityId")
        if activity_id is None:
            continue

        metadata_file = output_dir / "activities" / f"activity_{activity_id}.json"
        metadata_file.write_text(json.dumps(activity, ensure_ascii=False), encoding="utf-8")

        rows.append(
            {
                "activity_id": activity_id,
                "start_time_local": activity.get("startTimeLocal"),
                "start_time_utc": activity.get("startTimeGMT"),
                "duration_seconds": activity.get("duration"),
                "metadata_file": str(metadata_file),
                "raw_json": json.dumps(activity, ensure_ascii=False),
            }
        )

    return pd.DataFrame(rows)


garmin_metadata_df = download_garmin_activity_metadata(
    limit=GARMIN_ACTIVITY_LIMIT,
    output_dir=GARMIN_DOWNLOAD_DIR,
)

print(f"Downloaded {len(garmin_metadata_df)} Garmin activity metadata rows")
garmin_metadata_df.head()
```

## Persist Garmin metadata to DuckDB

```{code-cell} ipython3
with duckdb.connect(str(DUCKDB_PATH)) as conn:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS garmin_metadata (
            activity_id BIGINT,
            start_time_local TIMESTAMP,
            start_time_utc TIMESTAMP,
            duration_seconds DOUBLE,
            metadata_file VARCHAR,
            raw_json VARCHAR
        )
        """
    )

    if not garmin_metadata_df.empty:
        conn.register("garmin_metadata_df", garmin_metadata_df)
        conn.execute("DELETE FROM garmin_metadata WHERE activity_id IN (SELECT activity_id FROM garmin_metadata_df)")
        conn.execute(
            """
            INSERT INTO garmin_metadata
            SELECT
                CAST(activity_id AS BIGINT),
                CAST(start_time_local AS TIMESTAMP),
                CAST(start_time_utc AS TIMESTAMP),
                CAST(duration_seconds AS DOUBLE),
                metadata_file,
                raw_json
            FROM garmin_metadata_df
            """
        )

    inserted = conn.execute("SELECT COUNT(*) FROM garmin_metadata").fetchone()[0]
    print(f"garmin_metadata rows in DuckDB: {inserted}")
```

## Correlate Polar workouts to Garmin metadata files by overlapping time

```{code-cell} ipython3
garmin_windows_df = build_garmin_activity_windows(
    garmin_metadata_df,
    local_timezone=POLAR_LOCAL_TIMEZONE,
)

correlation_df = correlate_workouts_by_overlap(
    polar_windows_df,
    garmin_windows_df,
    tolerance_seconds=GARMIN_TIME_TOLERANCE_SECONDS,
)

print(f"Correlated Polar workouts: {len(correlation_df)}")
correlation_df.head(50)
```

## (Optional) Persist correlation table to DuckDB

```{code-cell} ipython3
with duckdb.connect(str(DUCKDB_PATH)) as conn:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS polar_garmin_correlation (
            workoutId VARCHAR,
            activity_id BIGINT,
            metadata_file VARCHAR,
            overlap_seconds DOUBLE,
            start_delta_seconds DOUBLE,
            polar_start_utc TIMESTAMP,
            polar_end_utc TIMESTAMP,
            garmin_start_utc TIMESTAMP,
            garmin_end_utc TIMESTAMP
        )
        """
    )

    if not correlation_df.empty:
        conn.register("correlation_df", correlation_df)
        conn.execute("DELETE FROM polar_garmin_correlation")
        conn.execute(
            """
            INSERT INTO polar_garmin_correlation
            SELECT
                workoutId,
                CAST(activity_id AS BIGINT),
                metadata_file,
                CAST(overlap_seconds AS DOUBLE),
                CAST(start_delta_seconds AS DOUBLE),
                CAST(polar_start_utc AS TIMESTAMP),
                CAST(polar_end_utc AS TIMESTAMP),
                CAST(garmin_start_utc AS TIMESTAMP),
                CAST(garmin_end_utc AS TIMESTAMP)
            FROM correlation_df
            """
        )

    total = conn.execute("SELECT COUNT(*) FROM polar_garmin_correlation").fetchone()[0]
    print(f"polar_garmin_correlation rows: {total}")
```
