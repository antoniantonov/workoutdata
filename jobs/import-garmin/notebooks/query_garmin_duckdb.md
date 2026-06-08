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

# Query Garmin DuckDB tables

Explore the Garmin data imported by the `import-garmin` job:

- `garmin_workout_metadata` — one row per workout (`workoutId`, GPS, HR summary)
- `garmin_timeseries` — per-second heart rate + GPS track
- `garmin_sleep` — daily sleep summary

> This notebook is excluded from the Docker image. Open it as a notebook via the
> Jupytext extension, or run the cells in any environment with `duckdb` + `pandas`.

```{code-cell} ipython3
import os
from pathlib import Path

import duckdb
import pandas as pd

pd.set_option("display.max_columns", None)

# Resolve the Garmin DuckDB file. Defaults to the job's local_data mount; this
# notebook lives in jobs/import-garmin/notebooks/, so we search upward from the
# working directory for `local_data/garmin.duckdb` to stay robust to where the
# notebook is launched from.
def _resolve_duckdb_path() -> Path:
    env_path = os.getenv("GARMIN_DUCKDB_PATH")
    if env_path:
        p = Path(env_path)
        return p if p.is_absolute() else (Path.cwd() / p)
    for base in [Path.cwd(), *Path.cwd().parents]:
        candidate = base / "local_data" / "garmin.duckdb"
        if candidate.exists():
            return candidate
        # Also handle running from inside the job dir's parent levels.
        candidate = base / "jobs" / "import-garmin" / "local_data" / "garmin.duckdb"
        if candidate.exists():
            return candidate
    return Path.cwd() / "local_data" / "garmin.duckdb"


GARMIN_DUCKDB_PATH = _resolve_duckdb_path()

print(f"DuckDB: {GARMIN_DUCKDB_PATH}  (exists={GARMIN_DUCKDB_PATH.exists()})")
con = duckdb.connect(str(GARMIN_DUCKDB_PATH), read_only=True)
```

## Table overview

```{code-cell} ipython3
con.execute(
    """
    SELECT 'garmin_workout_metadata' AS table_name, COUNT(*) AS rows FROM garmin_workout_metadata
    UNION ALL SELECT 'garmin_timeseries', COUNT(*) FROM garmin_timeseries
    UNION ALL SELECT 'garmin_sleep', COUNT(*) FROM garmin_sleep
    """
).fetchdf()
```

## Recent workouts (with workoutId + GPS)

```{code-cell} ipython3
con.execute(
    """
    SELECT workoutId, activity_id, sport, start_time, stop_time,
           ROUND(distance, 1) AS distance_m, calories, avg_hr, max_hr,
           gps_lat, gps_long, gps_source
    FROM garmin_workout_metadata
    ORDER BY start_time DESC
    LIMIT 15
    """
).fetchdf()
```

## GPS coverage of workouts

```{code-cell} ipython3
con.execute(
    """
    SELECT gps_source, COUNT(*) AS workouts
    FROM garmin_workout_metadata
    GROUP BY gps_source
    ORDER BY workouts DESC
    """
).fetchdf()
```

## Heart-rate timeseries for the most recent workout

```{code-cell} ipython3
latest_workout = con.execute(
    "SELECT workoutId FROM garmin_workout_metadata ORDER BY start_time DESC LIMIT 1"
).fetchone()[0]
print(f"workoutId: {latest_workout}")

con.execute(
    """
    SELECT record, timestamp, hr, position_lat, position_long, speed
    FROM garmin_timeseries
    WHERE workoutId = ?
    ORDER BY record
    LIMIT 20
    """,
    [latest_workout],
).fetchdf()
```

## HR summary per workout (computed from timeseries)

```{code-cell} ipython3
con.execute(
    """
    SELECT m.workoutId, m.sport, m.start_time,
           COUNT(t.hr) AS hr_samples,
           MIN(t.hr) AS min_hr, AVG(t.hr)::INT AS mean_hr, MAX(t.hr) AS max_hr
    FROM garmin_workout_metadata m
    JOIN garmin_timeseries t USING (workoutId)
    GROUP BY m.workoutId, m.sport, m.start_time
    ORDER BY m.start_time DESC
    LIMIT 15
    """
).fetchdf()
```

## Sleep trend

```{code-cell} ipython3
con.execute(
    """
    SELECT day,
           ROUND(total_sleep_s / 3600.0, 2) AS total_sleep_h,
           ROUND(deep_sleep_s / 3600.0, 2)  AS deep_sleep_h,
           ROUND(rem_sleep_s / 3600.0, 2)   AS rem_sleep_h,
           score, qualifier
    FROM garmin_sleep
    ORDER BY day DESC
    LIMIT 21
    """
).fetchdf()
```

```{code-cell} ipython3
con.close()
```
