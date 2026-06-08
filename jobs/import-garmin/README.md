# Garmin Import Job

Imports **Garmin per-workout heart rate** (with `workoutId` + GPS) and **sleep**
data into **DuckDB** or **PostgreSQL**, selectable via `DATABASE_TYPE`. It mirrors
the structure of the Polar job at `../import/`.

Data originates from the [GarminDB](https://github.com/tcgoetz/GarminDB) SQLite
databases. By default the job **transforms the already-downloaded** SQLite DBs;
optionally it can **download fresh data** from Garmin first.

## What gets imported

| Table | Source | Description |
|-------|--------|-------------|
| `garmin_workout_metadata` | `garmin_activities.db` → `activities` | One row per activity. PK `activity_id`; carries `workoutId` (`DD-MM-YYYY_HHMMSS` from `start_time`) and `gps_lat`/`gps_long` (first valid GPS fix, `gps_source` records origin). |
| `garmin_timeseries` | `garmin_activities.db` → `activity_records` | Per-second `hr`, `position_lat/long`, `speed`, `distance`, `cadence`, `altitude`. Linked by `activity_id` + `workoutId`. |
| `garmin_sleep` | `garmin.db` → `sleep` | Daily sleep summary. Durations stored as integer seconds; `start`/`end` renamed to `sleep_start`/`sleep_end`. |

Durations are converted to integer seconds; timestamps are stored as local-naive
wall-clock values. Imports are **idempotent** (delete-by-id upsert).

## Layout

```
jobs/import-garmin/
  main.py                 # orchestrator: (opt) download → transform → load → summary
  garmin_etl/
    config.py             # env-based config + DATABASE_TYPE / GARMIN_DOWNLOAD switches
    transform.py          # GarminDB SQLite → clean DataFrames (workoutId, first GPS)
    download.py           # optional GarminDB CLI download phase
    storage/duckdb.py     # DuckDB data layer
    storage/postgres.py   # PostgreSQL data layer
  notebooks/
    query_garmin_duckdb.md  # query notebook (EXCLUDED from the Docker image)
  scripts/
    renew_garmin_token.sh   # host-only token renewal wrapper (EXCLUDED from image)
    renew_garmin_token.py   # token renewal helper (EXCLUDED from image)
  Dockerfile, docker-compose.yml, pyproject.toml, .env(.example)
  local_data/             # mount: garmin_sqlite/DBs (input) + garmin.duckdb (output)
```

## Setup

1. Copy and edit the environment file:
   ```bash
   cp .env.example .env
   ```
2. Provide the GarminDB SQLite databases under `local_data/garmin_sqlite/DBs/`:
   ```bash
   mkdir -p local_data/garmin_sqlite/DBs
   cp ../../data/sqlite/DBs/garmin_activities.db local_data/garmin_sqlite/DBs/
   cp ../../data/sqlite/DBs/garmin.db            local_data/garmin_sqlite/DBs/
   ```

## Usage

```bash
cd jobs/import-garmin

# DuckDB (default)
docker compose build
docker compose up

# PostgreSQL: set DATABASE_TYPE=postgres + POSTGRES_* in .env
```

Output (DuckDB) lands at `local_data/garmin.duckdb`.

### Run without Docker

```bash
uv run python main.py          # or: pip install -e . && python main.py
```

## Optional: live download from Garmin

Set `GARMIN_DOWNLOAD=true` in `.env`. This runs the GarminDB CLI
(`garmindb_cli.py --all --download --import [--latest]`) before transforming, using
a job-generated `GarminConnectConfig.json` (pinned to `local_data/garmin_sqlite`,
`metric=true`, activities + sleep enabled). Requirements:

- Install the download extra: build with `uv sync --no-dev --extra download` (the
  `Dockerfile` already does this), or `pip install 'garmindb==3.7.0'` locally.
  The job pins **`garmindb==3.7.0`** (garth-based, single-file `garth_session`) to
  match the token format already in use; newer 3.8.x uses a different token store.
- A valid **garth session token**. The job reuses `~/.GarminDb/garth_session`
  (mounted into the container by `docker-compose.yml`). It performs a preflight
  check and **aborts with a clear message if the token is expired** rather than
  silently importing stale data.

### Regenerating the garth token

garth refresh tokens expire (~1 year). If expired, run the helper script (it
prompts for email, password, and MFA, then writes the exact single-file format
this job expects to `~/.GarminDb/garth_session`):

```bash
cd jobs/import-garmin
./scripts/renew_garmin_token.sh
```

The `scripts/renew_garmin_token.sh` wrapper ensures `garth` is installed and then
runs `scripts/renew_garmin_token.py` (which performs the OAuth/MFA login — that
part needs the `garth` Python library and can't be pure shell). Both files are
host-only and excluded from the Docker image. Equivalent one-liner:

```bash
uv run python -c "import garth, getpass, os; \
garth.login(input('Garmin email: '), getpass.getpass('Garmin password: ')); \
open(os.path.expanduser('~/.GarminDb/garth_session'),'w').write(garth.client.dumps()); \
print('Fresh token saved')"
```

No token is required for the default transform-only path.

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_TYPE` | `duckdb` | `duckdb` or `postgres` |
| `GARMIN_DOWNLOAD` | `false` | Run the live download phase first |
| `GARMIN_DOWNLOAD_LATEST` | `true` | Incremental (`--latest`) vs. full download |
| `GARMIN_DOWNLOAD_REQUIRED` | `true` | Abort if a required download fails (vs. fall back to existing DBs) |
| `GARMIN_DB_DIR` | `local_data/garmin_sqlite/DBs` | GarminDB SQLite directory |
| `GARMIN_BASE_DIR` | `local_data/garmin_sqlite` | GarminDB base dir (download phase) |
| `GARMIN_DUCKDB_PATH` | `local_data/garmin.duckdb` | DuckDB output file |
| `GARMIN_CONFIG_DIR` | `local_data/.GarminDb` | Download-phase config + token dir |
| `POSTGRES_*` | — | PostgreSQL connection (when `DATABASE_TYPE=postgres`) |

## Related

- GarminDB upstream: `../../garmin/GarminDB-notebooks/`
- Polar import job (sibling pattern): `../import/`
- Query notebook: `notebooks/query_garmin_duckdb.md`
