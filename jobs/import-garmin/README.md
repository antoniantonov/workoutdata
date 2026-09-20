# Garmin Import Job

This Docker job downloads Garmin Connect data through GarminDB, transforms the
GarminDB SQLite tables, and loads the result into DuckDB, PostgreSQL, or both.

## Where the data comes from and where it goes

Garmin is **not queried directly into DuckDB or PostgreSQL**.

```text
Garmin Connect
  -> garmindb 3.7.0 + garth session
  -> GarminDB SQLite files
     - local_data/garmin_sqlite/DBs/garmin_activities.db
     - local_data/garmin_sqlite/DBs/garmin.db
  -> garmin_etl transform
  -> DuckDB and/or PostgreSQL
```

The immediate ETL source is GarminDB SQLite:

| SQLite database | Source table | Imported data |
|---|---|---|
| `garmin_activities.db` | `activities` | One summary row per Garmin activity |
| `garmin_activities.db` | `activity_records` | Activity heart rate, track, and sensor records |
| `garmin.db` | `sleep` | Daily sleep summaries |

The destination is selected by the Docker service:

| Destination | Storage |
|---|---|
| DuckDB | `local_data/garmin.duckdb` |
| PostgreSQL | Compose-managed PostgreSQL 17 data under `local_data/postgres/` |

The live-download configuration enables GarminDB `activities` and `sleep`.
Monitoring, steps, weight, resting heart rate, HRV, and other GarminDB statistics
are not imported by this job.

## Imported tables

| Destination table | Key | Contents |
|---|---|---|
| `garmin_workout_metadata` | `activity_id` | Activity summary, derived `workoutId`, heart-rate summary, duration, distance, and first valid GPS coordinate |
| `garmin_timeseries` | `(activity_id, record)` | Per-record timestamp, heart rate, GPS, speed, distance, cadence, altitude, temperature, and GarminDB `rr` |
| `garmin_sleep` | `day` | Sleep window, stage durations in seconds, SpO2, respiration, stress, score, and qualifier |

`garmin_timeseries.activity_id` logically references
`garmin_workout_metadata.activity_id`. `workoutId` is also copied into both
workout tables for joins, but the database DDL does not declare foreign-key or
unique constraints for it.

## Docker-only quick start

The host only needs Docker and Docker Compose. Python, `uv`, GarminDB, DuckDB,
and PostgreSQL clients run inside containers.

### Run the complete workflow

From the repository root:

```bash
./jobs/import-garmin/scripts/run_import.sh --db postgres
```

The launcher builds `jobs/import-garmin/Dockerfile` once into the shared
`workoutdata/import-garmin:local` image, refreshes GarminDB SQLite, and imports
the selected destination(s). It creates `.env` from `.env.example` only if
missing; existing configuration is not overwritten. PostgreSQL is the default,
regardless of `DATABASE_TYPE` in `.env`, matching `scripts/run_database.sh`.
Docker must be running.

For first-time login or to renew authentication, add `--auth`. Credentials and
MFA are entered only at the interactive Docker prompt. Normal runs reuse the
saved session without prompting.

```bash
# Authenticate, download, import, and leave the DuckDB Web UI running.
./jobs/import-garmin/scripts/run_import.sh --db duckdb --auth

# Refresh once, load both databases, and compare both with the source.
./jobs/import-garmin/scripts/run_import.sh --db both --validate

# Request a full download rather than an incremental refresh.
./jobs/import-garmin/scripts/run_import.sh --db postgres --full-download --activity-limit 2000

# Re-import local SQLite data without Garmin access or an image build.
./jobs/import-garmin/scripts/run_import.sh --db duckdb --skip-download --no-build
```

| Option | Purpose |
|---|---|
| `--db postgres\|duckdb\|both` | Choose the import destination(s); default: `postgres` |
| `--auth` | Run interactive Garmin authentication before refreshing the source |
| `--skip-download` | Import existing `local_data/garmin_sqlite/DBs/` files without contacting Garmin |
| `--latest` / `--full-download` | Override `GARMIN_DOWNLOAD_LATEST` for this run |
| `--activity-limit N` | Set both incremental and full activity-download limits to a positive integer for this run |
| `--no-build` | Require and reuse the existing local Garmin image; PostgreSQL's official image is still pulled if missing |
| `--validate` | Compare selected destinations with the transformed source; with `both`, also check backend parity |
| `--help` | Show all options |

Without overrides, download settings come from `.env`: the sample uses an
incremental refresh limited to 25 latest activities, or a full-download limit
of 1000 activities. A full download is therefore not necessarily unlimited.
The activity limit controls downloading, not how many existing SQLite rows are
imported. `--skip-download` cannot be combined with `--auth` or download
overrides. Other settings, including `GARMIN_START_DATE`, remain configurable
in `.env`.

The launcher waits for PostgreSQL to be healthy before importing. For DuckDB,
it stops an existing `duckdb-ui` container immediately before database work to
release the file lock, then starts the Web UI and waits for it to be healthy
after import and optional validation. Close any other application holding a
DuckDB write connection before running it. PostgreSQL and/or the DuckDB Web UI
remain running afterward, with connection details printed by the existing
database launcher. Other database services are not stopped.

Failures abort with a nonzero exit status. A source-refresh failure never
falls back to stale data; an import or validation failure does not start the
DuckDB UI. PostgreSQL, if already started, is left running.

Set `GARMIN_POSTGRES_HOST_PORT` and `GARMIN_DUCKDB_UI_PORT` in `.env` or in the
launcher's environment to change the local query ports (defaults: 5433 and
4213). The script resolves the job directory itself and also works when
invoked by absolute path from another directory.

### Run individual steps manually

```bash
cd jobs/import-garmin
cp .env.example .env
docker compose build garmin-auth refresh-garmin-source import-garmin-duckdb import-garmin-postgres query-duckdb query-postgres validate-garmin
```

### 1. Create or renew Garmin authentication

```bash
docker compose run --rm garmin-auth
```

Enter the Garmin email, password, and MFA code only into the interactive
container prompt. The command writes:

```text
local_data/.GarminDb/garth_session
```

Do not put the Garmin password or MFA code in `.env`.

### 2. Download Garmin data into SQLite

```bash
docker compose run --rm refresh-garmin-source
```

This refreshes the mounted `local_data/garmin_sqlite/` directory. Run it once,
then load both destinations from the same SQLite snapshot.

### 3. Import into DuckDB

```bash
docker compose run --rm import-garmin-duckdb
```

### 4. Import into PostgreSQL

```bash
docker compose --profile postgres up -d --wait postgres
docker compose run --rm import-garmin-postgres
```

### 5. Verify source completeness and backend parity

```bash
docker compose run --rm validate-garmin
```

The validator:

- transforms the current SQLite source;
- compares every destination row and value with the transformed source;
- checks primary-key uniqueness;
- checks `activity_id` and `workoutId` relationships;
- compares DuckDB and PostgreSQL row-for-row.

To prove idempotency, rerun both import commands and then rerun the validator.
Table totals must remain stable.

### Transform existing SQLite files without contacting Garmin

If valid GarminDB SQLite files already exist under
`local_data/garmin_sqlite/DBs/`, skip the auth and refresh steps and run the
DuckDB/PostgreSQL import commands directly.

## Start a database for local clients

Use the launcher from any working directory. PostgreSQL is the default:

```bash
./jobs/import-garmin/scripts/run_database.sh
./jobs/import-garmin/scripts/run_database.sh --postgres
```

It starts the Compose PostgreSQL service and prints a local connection string,
including the configured database, username, password, and published port. Use
`127.0.0.1` or `localhost` as the client host; `0.0.0.0` is only a server
listen address and is not a valid DBeaver destination. For DBeaver, use the
printed `jdbc:postgresql://...` URL and enter the printed username/password in
their separate fields. Do not paste the `postgresql://...` libpq URI into
DBeaver's JDBC URL field.

Start the containerized DuckDB Web UI with:

```bash
./jobs/import-garmin/scripts/run_database.sh --duckdb
```

It prints the local Web UI URL and the mounted DuckDB file path for desktop
clients. Stop the DuckDB UI before running an import because DuckDB permits only
one process to hold a write connection to the database file. Add `--no-build`
to reuse an existing UI image; the complete-workflow launcher above handles
the image build and stop/restart ordering automatically.

## Garmin authentication

The download phase uses `garth`, through the pinned `garmindb==3.7.0`
dependency. Authentication is an interactive Garmin OAuth SSO flow:

1. Garmin email and password are entered at the Docker prompt.
2. Garmin requests an MFA code when MFA is enabled.
3. `garth` stores OAuth session data in a single `garth_session` file.
4. Later scheduled or local runs reuse that file without storing the password.

The session contains OAuth1 and OAuth2 credentials. With the pinned
`garth==0.6.3`, stale OAuth2 refresh metadata does not necessarily mean that
the whole session is unusable: `garth` can exchange the longer-lived OAuth1
token for new OAuth2 credentials. The source-refresh command warns and attempts
that renewal. If Garmin rejects the session, renew it with:

```bash
docker compose run --rm garmin-auth
```

The token is sensitive. `local_data/` is ignored by Git and excluded from the
Docker image. For cloud scheduling, inject the token from a cloud secret store
into the container at runtime; see [cloud-run-changes.md](cloud-run-changes.md).

No Garmin authentication is needed for transform-only imports from existing
SQLite files.

## Configuration

Copy `.env.example` to `.env`. Compose overrides backend and container-network
values for its individual services.

| Variable | Default | Purpose |
|---|---|---|
| `GARMIN_DOWNLOAD_LATEST` | `true` | Use GarminDB incremental activity download |
| `GARMIN_DOWNLOAD_REQUIRED` | `true` | Fail rather than use stale SQLite after a failed required refresh |
| `GARMIN_START_DATE` | `01/01/2020` | Earliest sleep date requested from GarminDB |
| `GARMIN_DOWNLOAD_ALL_ACTIVITIES` | `1000` | Full-download activity limit |
| `GARMIN_DOWNLOAD_LATEST_ACTIVITIES` | `25` | Incremental activity limit |
| `GARMIN_DOMAIN` | `garmin.com` | Garmin domain; use `garmin.cn` where applicable |
| `GARMIN_LOCAL_TIMEZONE` | `UTC` | Metadata only; timestamps remain local-naive |
| `POSTGRES_DATABASE` | `garmin` | Compose PostgreSQL database |
| `POSTGRES_USER` | `garmin` | Compose PostgreSQL user |
| `POSTGRES_PASSWORD` | `garmin-local-only` | Local Compose password; replace outside local development |
| `GARMIN_POSTGRES_HOST_PORT` | `5433` | Optional host port mapped to PostgreSQL container port 5432 |

Container paths are fixed by Compose:

| Variable | Container value |
|---|---|
| `GARMIN_CONFIG_DIR` | `/app/local_data/.GarminDb` |
| `GARMIN_BASE_DIR` | `/app/local_data/garmin_sqlite` |
| `GARMIN_DB_DIR` | `/app/local_data/garmin_sqlite/DBs` |
| `GARMIN_DUCKDB_PATH` | `/app/local_data/garmin.duckdb` |

For a remote PostgreSQL deployment, the Python storage layer also supports
`POSTGRES_CONNECTION_STRING` or `POSTGRES_HOST`, `POSTGRES_PORT`,
`POSTGRES_DATABASE`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, and
`POSTGRES_SSLMODE`.

## Transform rules

- `activity_id` remains the durable activity primary key.
- `workoutId` is derived from `start_time` as `DD-MM-YYYY_HHMMSS`.
- Same-second collisions are suffixed deterministically as `-2`, `-3`, and so
  on; `workoutId` therefore remains unique in transformed data.
- Activity and sleep duration strings are converted to integer seconds.
- Timestamps are stored as local-naive wall-clock values.
- The first valid activity-record coordinate becomes `gps_lat`/`gps_long` with
  `gps_source='first_record'`.
- A valid activity-level start coordinate is used as a fallback with
  `gps_source='activity_start'`.
- Activities without coordinates use `gps_source='none'`.
- GarminDB sleep placeholders are dropped when they have no positive sleep
  duration and no sleep start timestamp.
- Imports are transactional delete-by-key plus insert operations, so reruns are
  idempotent.

## DuckDB

### Persistence and connection

DuckDB is stored at:

```text
jobs/import-garmin/local_data/garmin.duckdb
```

Run a read-only query through the containerized query client:

```bash
docker compose run --rm query-duckdb --sql "SHOW TABLES"
```

### DuckDB schema

```sql
CREATE TABLE garmin_workout_metadata (
    activity_id VARCHAR PRIMARY KEY,
    workoutId VARCHAR NOT NULL,
    name VARCHAR,
    sport VARCHAR,
    sub_sport VARCHAR,
    start_time TIMESTAMP,
    stop_time TIMESTAMP,
    elapsed_time_s INTEGER,
    moving_time_s INTEGER,
    distance DOUBLE,
    calories INTEGER,
    avg_hr INTEGER,
    max_hr INTEGER,
    avg_speed DOUBLE,
    max_speed DOUBLE,
    avg_cadence INTEGER,
    ascent DOUBLE,
    descent DOUBLE,
    training_load DOUBLE,
    training_effect DOUBLE,
    gps_lat DOUBLE,
    gps_long DOUBLE,
    gps_source VARCHAR
);

CREATE TABLE garmin_timeseries (
    activity_id VARCHAR NOT NULL,
    workoutId VARCHAR,
    record INTEGER NOT NULL,
    timestamp TIMESTAMP,
    hr INTEGER,
    position_lat DOUBLE,
    position_long DOUBLE,
    speed DOUBLE,
    distance DOUBLE,
    cadence INTEGER,
    altitude DOUBLE,
    temperature DOUBLE,
    rr DOUBLE,
    PRIMARY KEY (activity_id, record)
);

CREATE TABLE garmin_sleep (
    day DATE PRIMARY KEY,
    sleep_start TIMESTAMP,
    sleep_end TIMESTAMP,
    total_sleep_s INTEGER,
    deep_sleep_s INTEGER,
    light_sleep_s INTEGER,
    rem_sleep_s INTEGER,
    awake_s INTEGER,
    avg_spo2 DOUBLE,
    avg_rr DOUBLE,
    avg_stress DOUBLE,
    score INTEGER,
    qualifier VARCHAR
);
```

Inspect the live DuckDB catalog:

```bash
docker compose run --rm query-duckdb --sql "SELECT table_name, column_name, data_type, is_nullable FROM information_schema.columns WHERE table_schema = 'main' AND table_name LIKE 'garmin_%' ORDER BY table_name, ordinal_position"
```

### DuckDB sample queries

Table counts:

```bash
docker compose run --rm query-duckdb --sql "SELECT 'garmin_workout_metadata' AS table_name, COUNT(*) AS row_count FROM garmin_workout_metadata UNION ALL SELECT 'garmin_timeseries', COUNT(*) FROM garmin_timeseries UNION ALL SELECT 'garmin_sleep', COUNT(*) FROM garmin_sleep"
```

Recent workout summaries:

```bash
docker compose run --rm query-duckdb --sql "SELECT workoutId, activity_id, sport, start_time, elapsed_time_s, ROUND(distance, 1) AS distance_m, calories, avg_hr, max_hr, gps_source FROM garmin_workout_metadata ORDER BY start_time DESC LIMIT 10"
```

Heart-rate statistics computed from activity records:

```bash
docker compose run --rm query-duckdb --sql "SELECT m.workoutId, m.sport, m.start_time, COUNT(t.hr) AS hr_samples, MIN(t.hr) AS min_hr, CAST(AVG(t.hr) AS INTEGER) AS mean_hr, MAX(t.hr) AS max_hr FROM garmin_workout_metadata m JOIN garmin_timeseries t ON m.workoutId = t.workoutId WHERE t.hr IS NOT NULL GROUP BY m.workoutId, m.sport, m.start_time ORDER BY m.start_time DESC LIMIT 10"
```

GPS coverage:

```bash
docker compose run --rm query-duckdb --sql "SELECT gps_source, COUNT(*) AS workouts, COUNT(CASE WHEN gps_lat IS NOT NULL AND gps_long IS NOT NULL THEN 1 END) AS workouts_with_coordinates FROM garmin_workout_metadata GROUP BY gps_source ORDER BY workouts DESC"
```

Recent sleep summaries:

```bash
docker compose run --rm query-duckdb --sql "SELECT day, ROUND(total_sleep_s / 3600.0, 2) AS total_sleep_h, ROUND(deep_sleep_s / 3600.0, 2) AS deep_sleep_h, ROUND(rem_sleep_s / 3600.0, 2) AS rem_sleep_h, score, qualifier FROM garmin_sleep ORDER BY day DESC LIMIT 10"
```

Relationship checks; every result must be zero:

```bash
docker compose run --rm query-duckdb --sql "SELECT COUNT(CASE WHEN m.activity_id IS NULL THEN 1 END) AS orphan_timeseries, COUNT(CASE WHEN t.workoutId IS NULL THEN 1 END) AS null_timeseries_workout_ids, COUNT(CASE WHEN m.workoutId IS DISTINCT FROM t.workoutId THEN 1 END) AS mismatched_workout_ids FROM garmin_timeseries t LEFT JOIN garmin_workout_metadata m ON t.activity_id = m.activity_id"
```

## PostgreSQL

### Persistence and connection

Start the database:

```bash
docker compose --profile postgres up -d --wait postgres
```

PostgreSQL data is persisted under `local_data/postgres/`. The optional host
mapping defaults to `localhost:5433`; containers connect to `postgres:5432`.

Open an interactive `psql` session inside the PostgreSQL container:

```bash
docker compose exec postgres sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

Run a one-off read-only query through the import image:

```bash
docker compose run --rm query-postgres --sql "SELECT current_database(), current_user"
```

### PostgreSQL schema

PostgreSQL folds unquoted identifiers to lowercase. Because the DDL preserves
camel-case `"workoutId"` and quotes `"timestamp"`, PostgreSQL queries must use
those double-quoted names.

```sql
CREATE TABLE garmin_workout_metadata (
    activity_id TEXT PRIMARY KEY,
    "workoutId" TEXT NOT NULL,
    name TEXT,
    sport TEXT,
    sub_sport TEXT,
    start_time TIMESTAMP,
    stop_time TIMESTAMP,
    elapsed_time_s INTEGER,
    moving_time_s INTEGER,
    distance DOUBLE PRECISION,
    calories INTEGER,
    avg_hr INTEGER,
    max_hr INTEGER,
    avg_speed DOUBLE PRECISION,
    max_speed DOUBLE PRECISION,
    avg_cadence INTEGER,
    ascent DOUBLE PRECISION,
    descent DOUBLE PRECISION,
    training_load DOUBLE PRECISION,
    training_effect DOUBLE PRECISION,
    gps_lat DOUBLE PRECISION,
    gps_long DOUBLE PRECISION,
    gps_source TEXT
);

CREATE TABLE garmin_timeseries (
    activity_id TEXT NOT NULL,
    "workoutId" TEXT,
    record INTEGER NOT NULL,
    "timestamp" TIMESTAMP,
    hr INTEGER,
    position_lat DOUBLE PRECISION,
    position_long DOUBLE PRECISION,
    speed DOUBLE PRECISION,
    distance DOUBLE PRECISION,
    cadence INTEGER,
    altitude DOUBLE PRECISION,
    temperature DOUBLE PRECISION,
    rr DOUBLE PRECISION,
    PRIMARY KEY (activity_id, record)
);

CREATE TABLE garmin_sleep (
    day DATE PRIMARY KEY,
    sleep_start TIMESTAMP,
    sleep_end TIMESTAMP,
    total_sleep_s INTEGER,
    deep_sleep_s INTEGER,
    light_sleep_s INTEGER,
    rem_sleep_s INTEGER,
    awake_s INTEGER,
    avg_spo2 DOUBLE PRECISION,
    avg_rr DOUBLE PRECISION,
    avg_stress DOUBLE PRECISION,
    score INTEGER,
    qualifier TEXT
);
```

Inspect the live PostgreSQL catalog:

```bash
docker compose run --rm query-postgres --sql "SELECT table_name, column_name, data_type, is_nullable FROM information_schema.columns WHERE table_schema = 'public' AND table_name LIKE 'garmin_%' ORDER BY table_name, ordinal_position"
```

### PostgreSQL sample queries

Table counts:

```bash
docker compose run --rm query-postgres --sql "SELECT 'garmin_workout_metadata' AS table_name, COUNT(*) AS row_count FROM garmin_workout_metadata UNION ALL SELECT 'garmin_timeseries', COUNT(*) FROM garmin_timeseries UNION ALL SELECT 'garmin_sleep', COUNT(*) FROM garmin_sleep"
```

Recent workout summaries:

```bash
docker compose run --rm query-postgres --sql 'SELECT "workoutId", activity_id, sport, start_time, elapsed_time_s, ROUND(distance::NUMERIC, 1) AS distance_m, calories, avg_hr, max_hr, gps_source FROM garmin_workout_metadata ORDER BY start_time DESC LIMIT 10'
```

Heart-rate statistics computed from activity records:

```bash
docker compose run --rm query-postgres --sql 'SELECT m."workoutId", m.sport, m.start_time, COUNT(t.hr) AS hr_samples, MIN(t.hr) AS min_hr, CAST(AVG(t.hr) AS INTEGER) AS mean_hr, MAX(t.hr) AS max_hr FROM garmin_workout_metadata m JOIN garmin_timeseries t ON m."workoutId" = t."workoutId" WHERE t.hr IS NOT NULL GROUP BY m."workoutId", m.sport, m.start_time ORDER BY m.start_time DESC LIMIT 10'
```

GPS coverage:

```bash
docker compose run --rm query-postgres --sql "SELECT gps_source, COUNT(*) AS workouts, COUNT(CASE WHEN gps_lat IS NOT NULL AND gps_long IS NOT NULL THEN 1 END) AS workouts_with_coordinates FROM garmin_workout_metadata GROUP BY gps_source ORDER BY workouts DESC"
```

Recent sleep summaries:

```bash
docker compose run --rm query-postgres --sql "SELECT day, ROUND((total_sleep_s / 3600.0)::NUMERIC, 2) AS total_sleep_h, ROUND((deep_sleep_s / 3600.0)::NUMERIC, 2) AS deep_sleep_h, ROUND((rem_sleep_s / 3600.0)::NUMERIC, 2) AS rem_sleep_h, score, qualifier FROM garmin_sleep ORDER BY day DESC LIMIT 10"
```

Relationship checks; every result must be zero:

```bash
docker compose run --rm query-postgres --sql 'SELECT COUNT(CASE WHEN m.activity_id IS NULL THEN 1 END) AS orphan_timeseries, COUNT(CASE WHEN t."workoutId" IS NULL THEN 1 END) AS null_timeseries_workout_ids, COUNT(CASE WHEN m."workoutId" IS DISTINCT FROM t."workoutId" THEN 1 END) AS mismatched_workout_ids FROM garmin_timeseries t LEFT JOIN garmin_workout_metadata m ON t.activity_id = m.activity_id'
```

Stop the PostgreSQL container without deleting its persisted data:

```bash
docker compose --profile postgres down
```

## Files

```text
jobs/import-garmin/
  main.py                       transform and load orchestrator
  scripts/run_import.sh         build, refresh, import, and leave databases running
  scripts/run_database.sh       start a queryable database without importing
  tests/test_run_import.py      isolated launcher and container-order tests
  garmin_etl/
    auth.py                     interactive Docker Garmin authentication
    config.py                   environment and path configuration
    download.py                 standalone GarminDB source refresh
    transform.py                SQLite to stable DataFrames
    query.py                    containerized DuckDB/PostgreSQL SQL client
    validate.py                 exact source/destination/backend validation
    storage/duckdb.py           DuckDB DDL, load, and query helpers
    storage/postgres.py         PostgreSQL DDL, load, and query helpers
  Dockerfile
  docker-compose.yml
  pyproject.toml
  .env.example
  local_data/                   ignored runtime data and credentials
```

Run the launcher regression tests from the repository root with
`python3 -m unittest discover -s jobs/import-garmin/tests`. They use a fake Docker
executable and temporary files, without contacting Garmin or opening real data.

## Troubleshooting

### Garmin rejects the saved session

Run:

```bash
docker compose run --rm garmin-auth
docker compose run --rm refresh-garmin-source
```

### Source refresh fails but exits without a useful GarminDB error

The wrapper scans GarminDB output for authentication-failure markers because
some GarminDB authentication failures return process exit code zero. The source
refresh returns failure when one of those markers is detected.

### SQLite source is missing

The import requires:

```text
local_data/garmin_sqlite/DBs/garmin_activities.db
local_data/garmin_sqlite/DBs/garmin.db
```

Create them with `refresh-garmin-source` or provide an existing GarminDB export
under that mounted path.

### PostgreSQL query says `workoutid` or `timestamp` does not exist

Use `"workoutId"` and `"timestamp"` with double quotes in PostgreSQL SQL.

### Validation fails

Rerun the relevant import, then:

```bash
docker compose run --rm validate-garmin
```

The validator reports which table differs from the transformed SQLite source.

## Related

- [Cloud scheduled-job implementation guide](cloud-run-changes.md)
- [DuckDB query notebook](notebooks/query_garmin_duckdb.md)
- [GarminDB source](../../garmin/GarminDB-notebooks/)
- [Polar import job](../import/)
