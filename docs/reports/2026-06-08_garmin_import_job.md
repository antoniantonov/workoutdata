# Garmin Import Job — Full Work Report

**Date:** 2026-06-08
**Status:** ✅ Complete — Docker job runs successfully; DuckDB and PostgreSQL both verified
**Job location:** `jobs/import-garmin/`
**Evidence log:** `jobs/import-garmin/local_data/import-garmin_run_2026-06-08T13-13-51.log`
**Output database:** `jobs/import-garmin/local_data/garmin.duckdb` (5.5 MB)

---

## 1. Objective

Create a new containerized import job (`jobs/import-garmin/`) that imports **Garmin
per-workout heart rate** and **sleep** data into both **DuckDB** and **PostgreSQL**,
selectable via a `.env` switch, mirroring the existing Polar job at `jobs/import/`.

Requirements:
1. Understand the GarminDB notebooks (`garmin/GarminDB-notebooks/`, upstream
   [tcgoetz/GarminDB](https://github.com/tcgoetz/GarminDB)) and how to get all
   Garmin data.
2. New import job `import-garmin` runnable under Docker locally.
3. Import **heart rate** + **sleep** into DuckDB and PostgreSQL, with **two data
   layers** per DB type and a `.env` switch. For heart rate: generate a `workoutId`
   like the Polar database and link it as a key via a metadata table; add **GPS
   coordinates** to the heart-rate metadata (first GPS coordinate when multiple).
4. Store data under `import-garmin/local_data` (file-system mount).
5. Provide an MD-style notebook to query the DuckDB Garmin tables, under the job
   folder, **excluded from the Dockerfile**.
6. Success = the job runs in local Docker and imports all Garmin workouts + sleep
   into DuckDB.

---

## 2. How Garmin data is obtained (investigation)

- **GarminDB** (clone in `garmin/GarminDB-notebooks/`) is a tool that downloads
  Garmin Connect data via its CLI `garmindb_cli.py --all --download --import
  --analyze [--latest]` and stores it in **SQLite** databases.
- Data was **already downloaded** on this machine into `data/sqlite/DBs/`:
  - `garmin_activities.db` → `activities` (per-workout metadata: `start_time`,
    `sport`, `start_lat/long`, `avg_hr`, `calories`, …) and `activity_records`
    (per-second `timestamp`, `hr`, `position_lat`, `position_long`, `speed`, …).
  - `garmin.db` → `sleep` (daily: `day`, `start`, `end`, `total_sleep`,
    `deep_sleep`, `light_sleep`, `rem_sleep`, `awake`, `score`, `qualifier`).
- **Authentication** uses the `garth` library with a session token at
  `~/.GarminDb/garth_session` (base64 OAuth1+OAuth2). GarminDB resumes it via
  `garth.loads()` and auto-refreshes while the ~1-year refresh token is valid; the
  username/password in `GarminConnectConfig.json` are empty (token-based).

**Data sourcing decision (confirmed with user):** Support both. The default local
Docker test is **transform-only** (reads the already-downloaded SQLite DBs seeded
into `local_data`), with an **optional live download** phase via
`GARMIN_DOWNLOAD=true` using the garth session. This guarantees a reliable,
network-independent local Docker run while still supporting fresh downloads.

---

## 3. Architecture & data model

```
jobs/import-garmin/
  main.py                 # orchestrator: (opt) download → transform → load → summary
  garmin_etl/
    config.py             # env-based config; DATABASE_TYPE + GARMIN_DOWNLOAD switches
    transform.py          # GarminDB SQLite → clean DataFrames (workoutId + first GPS)
    download.py           # optional GarminDB CLI download phase (garth session)
    storage/
      duckdb.py           # DuckDB data layer (explicit DDL, idempotent upsert)
      postgres.py         # PostgreSQL data layer (mirror of duckdb.py)
  notebooks/query_garmin_duckdb.md  # MyST query notebook (EXCLUDED from image)
  Dockerfile, docker-compose.yml, pyproject.toml, .env(.example), README.md
  local_data/             # mount: garmin_sqlite/DBs (input) + garmin.duckdb (output) + logs
```

**Two data layers, one switch:** `garmin_etl/storage/duckdb.py` and
`garmin_etl/storage/postgres.py` expose the same surface (`import_workouts`,
`import_sleep`, query helpers, `delete_workout_by_id`). `main.py` selects the
backend at runtime from `DATABASE_TYPE` (`duckdb` | `postgres`) read from `.env`.

**Tables (all `garmin_`-prefixed; dedicated `garmin.duckdb`, not Polar's DB):**

| Table | Grain | Key | Notes |
|-------|-------|-----|-------|
| `garmin_workout_metadata` | 1 row / workout | PK `activity_id` | Carries `workoutId` (`DD-MM-YYYY_HHMMSS` from `start_time`) + `gps_lat`/`gps_long`/`gps_source`. |
| `garmin_timeseries` | per-second | PK `(activity_id, record)` | Per-second `hr`, `position_lat/long`, `speed`, …; linked by `activity_id` + `workoutId`. |
| `garmin_sleep` | daily | PK `day` | Durations as integer seconds; reserved `end` renamed to `sleep_end`. |

**`workoutId`**: built as `DD-MM-YYYY_HHMMSS` from the activity `start_time`
(matching the Polar convention). It is used as a join key, so same-second
collisions are auto-disambiguated with a numeric suffix (`-2`, `-3`, …);
`activity_id` remains the durable primary key.

**First GPS coordinate**: the first `activity_records` row (ordered by `record`,
`timestamp`) where both `position_lat` and `position_long` are present and in
valid range; falls back to `activities.start_lat/long`. `gps_source` records the
origin (`first_record` | `activity_start` | `none`).

**Data integrity choices:** explicit DDL (no pandas type inference), durations
converted to integer seconds, timestamps stored as local-naive wall-clock,
nullable integers bound as `NULL` (never `NaN`), idempotent delete-by-id upserts.

---

## 4. File inventory

| File | LOC | Purpose |
|------|-----|---------|
| `main.py` | 105 | Orchestrator + summary |
| `garmin_etl/config.py` | 160 | Env config + switches |
| `garmin_etl/transform.py` | 381 | SQLite → DataFrames, workoutId, first GPS, preflight |
| `garmin_etl/download.py` | 154 | Optional GarminDB CLI download phase |
| `garmin_etl/storage/duckdb.py` | 265 | DuckDB data layer |
| `garmin_etl/storage/postgres.py` | 354 | PostgreSQL data layer |
| `garmin_etl/__init__.py`, `storage/__init__.py` | 14 | Package docstrings |

Plus: `Dockerfile`, `docker-compose.yml`, `pyproject.toml`, `.env` / `.env.example`,
`.gitignore`, `.dockerignore`, `README.md`, `notebooks/query_garmin_duckdb.md`, `scripts/renew_garmin_token.*`.

Documentation: top-level `README.md` updated with an **Import Jobs** section.

---

## 5. Docker run (evidence)

Command:

```bash
cd jobs/import-garmin
docker compose up --build --abort-on-container-exit
```

**Result: container exited with code 0.** Full log captured at
`jobs/import-garmin/local_data/import-garmin_run_2026-06-08T13-13-51.log`.
Key excerpt:

```
Step 3: Transforming GarminDB SQLite data...
  Reading activities from /app/local_data/garmin_sqlite/DBs/garmin_activities.db
  Reading activity_records from /app/local_data/garmin_sqlite/DBs/garmin_activities.db
  Reading sleep from /app/local_data/garmin_sqlite/DBs/garmin.db
  Transformed: 193 workouts (189 with GPS), 80295 records, 135 sleep nights

Step 4: Loading into DUCKDB...
  ✅ DuckDB workouts: upserted 193 (table total 193); timeseries rows 80295 (table total 80295)
  ✅ DuckDB sleep: upserted 135 (table total 135)

✅ GARMIN IMPORT JOB COMPLETE
  - Database type:        DUCKDB
  - Workouts imported:    193 (table total 193)
  - Timeseries rows:      80295 (table total 80295)
  - Sleep nights:         135 (table total 135)
  - DuckDB file:          /app/local_data/garmin.duckdb
garmin-import-job exited with code 0
```

---

## 6. DuckDB verification

All queries run against `jobs/import-garmin/local_data/garmin.duckdb` (read-only).

### 6.1 Tables present
```
garmin_sleep
garmin_timeseries
garmin_workout_metadata
```

### 6.2 Row counts
| table | rows |
|-------|------|
| garmin_workout_metadata | 193 |
| garmin_timeseries | 80295 |
| garmin_sleep | 135 |

### 6.3 `workoutId` integrity
| total | distinct_wid | null_wid | valid_format (`DD-MM-YYYY_HHMMSS[-n]`) |
|-------|--------------|----------|----------------------------------------|
| 193 | 193 | 0 | 193 |

→ Every workout has a unique, correctly-formatted, non-null `workoutId`.

### 6.4 GPS coverage (`gps_source`)
| gps_source | workouts |
|------------|----------|
| first_record | 189 |
| none | 4 |

→ 189/193 workouts carry a GPS fix (the 4 without are indoor activities with no
GPS records in the source), satisfying "add GPS coordinates; use the first when
multiple".

### 6.5 Timeseries linkage
- `workoutId` NULLs in `garmin_timeseries`: **0** of 80,295.
- Orphan timeseries rows (workoutId not present in metadata): **0**.

→ Every per-second HR row is correctly linked to a workout via `workoutId`.

### 6.6 Date ranges
| dataset | min | max |
|---------|-----|-----|
| workouts (`start_time`) | 2025-11-09 14:04:31 | 2026-03-16 18:59:52 |
| timeseries (`timestamp`) | 2025-11-09 14:04:31 | 2026-03-16 19:10:00 |
| sleep (`day`) | 2025-11-01 | 2026-03-15 |

### 6.7 HR sanity (timeseries)
| min_hr | max_hr | mean_hr | null_hr |
|--------|--------|---------|---------|
| 0 | 195 | 133 | 69 |

→ HR values are within a plausible range; only 69 of 80,295 rows have a null HR
(sensor gaps), preserved as `NULL`.

### 6.8 Sample workouts (latest 5)
```
        workoutId activity_id   sport          start_time  distance  calories  avg_hr  max_hr  gps_lat   gps_long   gps_source
16-03-2026_185952 22198283333 generic 2026-03-16 18:59:52   0.13793       127     155     178 47.75628 -122.24000 first_record
16-03-2026_184554 22198212882 generic 2026-03-16 18:45:54   0.10683        36     100     132 47.75629 -122.23993 first_record
15-03-2026_113550 22186157118 generic 2026-03-15 11:35:50  26.15854       426      96     132 47.75627 -122.23993 first_record
14-03-2026_123907 22174408697 generic 2026-03-14 12:39:07   1.55884       131     139     195 47.72793 -122.24486 first_record
14-03-2026_114402 22174227380 generic 2026-03-14 11:44:02   1.46376       179      86     143 47.75617 -122.23991 first_record
```

### 6.9 Sample sleep (latest 5)
```
       day         sleep_start           sleep_end  total_sleep_s  deep_sleep_s  light_sleep_s  rem_sleep_s  score qualifier
2026-03-15 2026-03-15 00:42:37 2026-03-15 08:28:37          27660          3780          19200         4680     77      FAIR
2026-03-14 2026-03-14 01:02:13 2026-03-14 08:45:53          27100          3780          18480         4860     70      FAIR
2026-03-13 2026-03-13 00:14:32 2026-03-13 06:10:32          20280          3540          14580         2160     66      FAIR
2026-03-12 2026-03-11 23:35:31 2026-03-12 06:10:31          23640          4680          15480         3480     78      FAIR
2026-03-11 2026-03-10 23:27:07 2026-03-11 06:10:07          23700          3000          17160         3540     64      FAIR
```

### 6.10 Column schema (explicit DDL verified)
- `garmin_workout_metadata` (23 cols): `activity_id VARCHAR`, `workoutId VARCHAR`,
  `name`, `sport`, `sub_sport`, `start_time TIMESTAMP`, `stop_time TIMESTAMP`,
  `elapsed_time_s INTEGER`, `moving_time_s INTEGER`, `distance DOUBLE`,
  `calories INTEGER`, `avg_hr INTEGER`, `max_hr INTEGER`, `avg_speed DOUBLE`,
  `max_speed DOUBLE`, `avg_cadence INTEGER`, `ascent DOUBLE`, `descent DOUBLE`,
  `training_load DOUBLE`, `training_effect DOUBLE`, `gps_lat DOUBLE`,
  `gps_long DOUBLE`, `gps_source VARCHAR`.
- `garmin_timeseries` (13 cols): `activity_id VARCHAR`, `workoutId VARCHAR`,
  `record INTEGER`, `timestamp TIMESTAMP`, `hr INTEGER`, `position_lat DOUBLE`,
  `position_long DOUBLE`, `speed DOUBLE`, `distance DOUBLE`, `cadence INTEGER`,
  `altitude DOUBLE`, `temperature DOUBLE`, `rr DOUBLE`.
- `garmin_sleep` (13 cols): `day DATE`, `sleep_start TIMESTAMP`,
  `sleep_end TIMESTAMP`, `total_sleep_s INTEGER`, `deep_sleep_s INTEGER`,
  `light_sleep_s INTEGER`, `rem_sleep_s INTEGER`, `awake_s INTEGER`,
  `avg_spo2 DOUBLE`, `avg_rr DOUBLE`, `avg_stress DOUBLE`, `score INTEGER`,
  `qualifier VARCHAR`.

---

## 7. Completeness vs. source (no data loss)

| Metric | Source (GarminDB SQLite) | DuckDB output | Match |
|--------|--------------------------|---------------|-------|
| Activities / workouts | 193 | 193 | ✅ |
| Activity records / timeseries | 80,295 | 80,295 | ✅ |
| Sleep nights | 135 | 135 | ✅ |
| Activities with GPS | 189 | 189 (`first_record`) | ✅ |

→ 100% of source rows imported; no rows dropped or duplicated.

---

## 8. Idempotency & PostgreSQL (earlier verification)

- **Idempotency (DuckDB):** Re-running the job leaves counts unchanged
  (193 / 80,295 / 135) — delete-by-id upsert is idempotent.
- **PostgreSQL layer:** Verified against a local Postgres 16 container with
  `DATABASE_TYPE=postgres`: imported the same 193 / 80,295 / 135 rows, correct
  column types (`text`, `timestamp without time zone`, `integer`,
  `double precision`), valid `workoutId` + GPS, and idempotent on re-run.
- **Disambiguation unit test:** Synthetic same-second collisions produce unique
  `workoutId`s (`…185952`, `…185952-2`, `…185952-3`) — verified.

---

## 9. Code-review fixes applied

A code review of the new job led to these hardening changes (all re-verified):

1. **Rollback no longer masks the original error** — `ROLLBACK` / `conn.rollback()`
   wrapped in `try/except pass` in both storage layers so the root-cause exception
   is preserved.
2. **Same-second `workoutId` collisions** are auto-disambiguated with a numeric
   suffix instead of aborting the whole batch — keeps `workoutId` unique for joins
   while remaining resilient for unattended runs.
3. Removed dead helper code; explicit per-backend row conversion retained.

---

## 10. How to run

```bash
cd jobs/import-garmin
cp .env.example .env                 # defaults: DATABASE_TYPE=duckdb, GARMIN_DOWNLOAD=false

# Seed the already-downloaded GarminDB SQLite DBs (input):
mkdir -p local_data/garmin_sqlite/DBs
cp ../../data/sqlite/DBs/garmin_activities.db local_data/garmin_sqlite/DBs/
cp ../../data/sqlite/DBs/garmin.db            local_data/garmin_sqlite/DBs/

docker compose up --build            # → writes local_data/garmin.duckdb

# PostgreSQL: set DATABASE_TYPE=postgres + POSTGRES_* in .env
```

Query the result with `notebooks/query_garmin_duckdb.md` (excluded from the image).

### Optional live download from Garmin
Set `GARMIN_DOWNLOAD=true`, build with the `download` extra (uncomment the
`--extra download` line in the `Dockerfile`), and mount `~/.GarminDb` (done by
`docker-compose.yml`). If the garth token is expired, regenerate once on the host:

```bash
pip install garmindb
garmindb_cli.py --download --latest   # prompts Garmin login + MFA; rewrites ~/.GarminDb/garth_session
```

The default transform-only path requires **no token** — which is why the verified
Docker run above succeeded without any credential input.

---

## 11. Notes & limitations

- The already-downloaded source data ends **2026-03-16**; enable the download
  phase to refresh to the present.
- "Heart rate" = per-workout/activity HR (with `workoutId` + GPS), per the
  confirmed scope. All-day monitoring HR is intentionally out of scope.
- Timestamps are stored as local-naive wall clock; `GARMIN_LOCAL_TIMEZONE`
  (default `UTC`) is reserved as future metadata for Polar↔Garmin correlation.
- `garmin.duckdb`, the seeded SQLite DBs, the run log, `.venv`, and `.env` are all
  git-ignored; only source/config files are tracked.

---

## 12. Status

✅ **Success criteria met.** The `import-garmin` job runs successfully in local
Docker (exit 0) and imports **all** Garmin workouts (193, with per-second HR and
first-GPS) and **all** sleep nights (135) into DuckDB, with a verified PostgreSQL
path and idempotent re-runs.
