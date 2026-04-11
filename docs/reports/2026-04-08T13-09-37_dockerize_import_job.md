# Dockerize Polar AccessLink Import Job — Full Report

**Date:** 2026-04-08  
**Branch:** `dev/anton/dockerize-import-job` (created from `master`)  
**Status:** ✅ Complete — Both DuckDB and PostgreSQL tests passing  
**Commits/Pushes:** None (kept local for review as requested)

---

## 1. Objective

Update `jobs/import/` to build a Docker image that replicates the **complete** notebook workflow (`notebooks/polar_accesslink_workflow_v0.2.md`), including database import, DuckDB Azure upload, and file cleanup — features missing from the original implementation.

The original `jobs/import/main.py` only performed:
- Download TCX from Polar API
- Convert TCX to CSV
- Upload to Azure Storage
- **Azure-based** deduplication (checking blob storage for existing files)

The notebook additionally does:
- **Database-based** deduplication (checking `workout_metadata` table)
- Import CSVs into DuckDB or PostgreSQL
- Upload DuckDB database to Azure
- Delete processed TCX/CSV files from local storage

---

## 2. Summary of Changes

### Modified Files (7)

| File | Description |
|------|-------------|
| `jobs/import/main.py` | **Complete rewrite**: from raw API-only import to full notebook workflow using `run_polar_workflow()`, DB import, Azure upload, and file cleanup. Added retry logic for API errors (503). |
| `jobs/import/Dockerfile` | Fixed `raw-import` → `import` path references, added Azure CLI installation, restructured WORKDIR strategy (`/app` as repo root), `uv run --project` for correct dependency resolution. |
| `jobs/import/docker-compose.yml` | Added `env_file: .env`, volume mounts for `local_data/`, `tokens_polar.json`, and `~/.azure` (rw, not ro). Removed old hardcoded environment variables. |
| `jobs/import/pyproject.toml` | Added `pandas`, `duckdb`, `psycopg[binary]`, `ipython` dependencies. Fixed hatchling build config (removed `[project.scripts]`, added `[tool.hatch.build.targets.wheel]`). Fixed uv deprecation warning (`tool.uv.dev-dependencies` → `[dependency-groups]`). |
| `jobs/import/.env.example` | Complete rewrite with all required environment variables organized by category. |
| `jobs/import/.dockerignore` | Updated to exclude `test_cleanup.sh`, `local_data/`, and other non-essential files. |
| `jobs/import/README.md` | Complete rewrite documenting full workflow, environment variables, test modes, and troubleshooting. |

### New Files (2)

| File | Description |
|------|-------------|
| `jobs/import/test_cleanup.sh` | Shell script to remove last N workout entries from DuckDB or PostgreSQL for testing. Excluded from Docker image. |
| `.dockerignore` (repo root) | Required because Docker build context is `../..` (repo root). Excludes `.git`, secrets, large files. |

### Supporting Files Created During Testing

| File | Description |
|------|-------------|
| `jobs/import/local_data/database_v2.duckdb` | DuckDB database copy for Docker testing (gitignored). |

---

## 3. Architecture & Design Decisions

### 3.1 Database-Based Deduplication (not Azure-based)

The original code used `filter_exercises_not_in_azure()` to check Azure Blob Storage for existing files. The notebook uses `filter_new_exercises()` which checks the `workout_metadata` database table. We aligned with the notebook approach because:
- It's the canonical workflow
- It's simpler and more reliable (no Azure API call needed for deduplication)
- It works for both DuckDB and PostgreSQL modes

### 3.2 Single `main.py` for Both Backends

The `DATABASE_TYPE` environment variable (`duckdb` or `postgres`) controls which storage module is used at runtime. This avoids duplicating the entry point.

### 3.3 Path Resolution Strategy

The most challenging part. Inside Docker:
- `polar/utils/config.py` line 124 uses `os.getcwd()` for token file path resolution
- `polar/utils/config.py` line 182 uses `Path(__file__).parent.parent.parent` for data file paths
- Both must resolve to `/app` (repo root)

**Solution:** `WORKDIR /app` in Dockerfile + `uv run --project /app/jobs/import python /app/jobs/import/main.py` as CMD. This ensures:
- `os.getcwd()` = `/app`
- `Path(__file__)` for config.py = `/app/polar/utils/config.py` → `.parent.parent.parent` = `/app`
- All `.env` paths are relative to `/app` (e.g., `DUCKDB_PATH=local_data/database_v2.duckdb`)

### 3.4 Volume Mounts

```yaml
volumes:
  - ./tokens_polar.json:/app/jobs/import/tokens_polar.json  # OAuth tokens
  - ./local_data:/app/local_data                            # DuckDB + working files
  - ~/.azure:/root/.azure                                   # Azure CLI creds (read-write)
```

The `~/.azure` mount **must** be read-write (not `:ro`) because the Azure CLI writes session files at runtime.

---

## 4. Build & Test Iterations

### Iteration 1: Initial Build
- **Error:** `uv: not found` in container
- **Root Cause:** The install script puts `uv` at `/root/.local/bin`, not `/root/.cargo/bin`
- **Fix:** Changed PATH to include `/root/.local/bin`

### Iteration 2: hatchling Build Error
- **Error:** `[project.scripts]` entry `import-job = "main:main"` caused hatchling to fail trying to build a package
- **Root Cause:** When doing `uv sync`, it tries to build the project as a wheel, and the scripts entry needs a proper Python package structure
- **Fix:** Removed `[project.scripts]`, added `[tool.hatch.build.targets.wheel] packages=["."]`

### Iteration 3: Token Path Doubling
- **Error:** Token file not found at `/app/jobs/import/jobs/import/tokens_polar.json`
- **Root Cause:** `WORKDIR` was set to `/app/jobs/import`, so `os.getcwd()` = `/app/jobs/import`. Config then joined this with `POLAR_TOKENS_FILE=jobs/import/tokens_polar.json` → doubled path
- **Fix:** Changed WORKDIR back to `/app`, used `--project` flag: `CMD ["uv", "run", "--project", "/app/jobs/import", "python", "/app/jobs/import/main.py"]`

### Iteration 4: Polar API 503
- **Error:** Polar API returning HTTP 503 (Service Unavailable)
- **Root Cause:** Scheduled maintenance (April 7-8, 2026)
- **Fix:** Added retry logic (3 attempts, 30s delay). Waited for API to come back online.

### Iteration 5: Azure CLI Read-Only Mount
- **Error:** `OSError: [Errno 30] Read-only file system: '/root/.azure/az.sess'`
- **Root Cause:** Azure CLI writes session files to `~/.azure/`, but volume was mounted `:ro`
- **Fix:** Changed to read-write mount (removed `:ro`)

### Iteration 6: Azure CLI Not in Container
- **Error:** `DefaultAzureCredential` failed — `AzureCliCredential` couldn't find `az` binary
- **Root Cause:** `python:3.11-slim` doesn't include Azure CLI
- **Fix:** Added Azure CLI installation to Dockerfile via `curl -sL https://aka.ms/InstallAzureCLIDeb | bash`

### Iteration 7: DuckDB Test — ✅ PASSED
- Full pipeline completed successfully with DuckDB backend
- 33 exercises downloaded, converted, uploaded, imported, database uploaded, files cleaned up

### Iteration 8: macOS Bash Compatibility
- **Error:** `test_cleanup.sh: line 64: ${DB_TYPE^^}: bad substitution`
- **Root Cause:** macOS uses older bash that doesn't support `${var^^}` (uppercase)
- **Fix:** Replaced with `echo "$DB_TYPE" | tr '[:lower:]' '[:upper:]'`

### Iteration 9: Date Sorting in Cleanup Script
- **Issue:** `ORDER BY Date DESC` sorts lexically because Date is stored as text (`DD-MM-YYYY`), causing `31-12-2025` to rank higher than `08-04-2026`
- **Fix:** Used `strptime(Date, '%d-%m-%Y')` for DuckDB and `to_date("Date", 'DD-MM-YYYY')` for PostgreSQL

### Iteration 10: PostgreSQL Test — ✅ PASSED
- Cleaned up 2 entries from PostgreSQL, then ran Docker container
- 2 exercises re-downloaded, converted, uploaded, imported, files cleaned up

---

## 5. Test Results

### DuckDB Docker Test ✅

```
Step 1: Loading configuration...
✅ Configuration loaded
  - Database Type: DUCKDB
  - DuckDB Path: /app/local_data/database_v2.duckdb

Step 2: Running Polar AccessLink workflow...
✅ Retrieved 33 exercise(s)
✅ 33 exercises are NEW (not in database)
  Downloaded and processed 33 new exercise(s)
  Uploaded 66 file(s) (tcx and csv) to Azure Storage

Step 3: Importing CSVs into DUCKDB database...
  Found 34 CSV file(s). Processing...
  Successfully processed: 33
  Skipped (duplicates):  1
  Errors encountered:    0

Step 4: Uploading DuckDB database to Azure...
✅ Uploaded DuckDB database to Azure (507.66 KB)

Step 5: Cleaning up processed files...
  Successfully deleted:  67 (34 CSV + 33 TCX)

✅ POLAR IMPORT JOB COMPLETE
  Container exited with code 0
```

### PostgreSQL Docker Test ✅

```
Step 1: Loading configuration...
✅ Configuration loaded
  - Database Type: POSTGRES

Step 2: Running Polar AccessLink workflow...
✅ Retrieved 33 exercise(s)
✅ 2 exercises are NEW (not in database)
  Downloaded and processed 2 new exercise(s)
  Uploaded 4 file(s) (tcx and csv) to Azure Storage

Step 3: Importing CSVs into POSTGRES database...
  Found 2 CSV file(s). Processing...
  Successfully processed: 2
  Skipped (duplicates):  0
  Errors encountered:    0

Step 5: Cleaning up processed files...
  Successfully deleted:  4 (2 CSV + 2 TCX)

✅ POLAR IMPORT JOB COMPLETE
  Container exited with code 0
```

---

## 6. Environment Variables Reference

All variables are sourced from `jobs/import/.env` via the `env_file` directive in `docker-compose.yml`:

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `POLAR_CLIENT_ID` | Yes | — | Polar AccessLink API client ID |
| `POLAR_CLIENT_SECRET` | Yes | — | Polar AccessLink API client secret |
| `POLAR_REDIRECT_PORT` | No | 5000 | OAuth callback port |
| `POLAR_MEMBER_ID` | No | — | Polar member ID for user registration |
| `ALLOW_PORT_FALLBACK` | No | false | Allow port fallback for OAuth |
| `DATABASE_TYPE` | No | duckdb | Database backend: `duckdb` or `postgres` |
| `POLAR_TOKENS_FILE` | No | jobs/import/tokens_polar.json | Path to OAuth tokens file |
| `DUCKDB_PATH` | No | local_data/database_v2.duckdb | DuckDB database file path |
| `VO2MAX_DATA_PATH` | No | data/v02max_data.csv | VO2max reference data |
| `ZONES_CSV_PATH` | No | hr_data/zones.csv | HR zones definition file |
| `OUTPUT_DIR` | No | local_data | Working directory for TCX/CSV files |
| `AZURE_STORAGE_ENABLED` | No | false | Enable Azure Blob Storage uploads |
| `AZURE_STORAGE_ACCOUNT_NAME` | If Azure | — | Azure Storage account |
| `AZURE_STORAGE_CONTAINER_NAME` | No | workout-data | Azure blob container |
| `POSTGRES_HOST` | If postgres | — | PostgreSQL hostname |
| `POSTGRES_PORT` | No | 5432 | PostgreSQL port |
| `POSTGRES_DATABASE` | If postgres | — | PostgreSQL database name |
| `POSTGRES_USER` | If postgres | — | PostgreSQL username |
| `POSTGRES_PASSWORD` | If postgres | — | PostgreSQL password |

---

## 7. Docker Commands Quick Reference

```bash
cd jobs/import

# Build the Docker image
docker compose build

# Run the import job
docker compose up

# View logs
docker compose logs

# Switch database backend
# Edit .env: DATABASE_TYPE=duckdb or DATABASE_TYPE=postgres

# Test cleanup (run on host, not in Docker)
./test_cleanup.sh --db duckdb           # Remove last 2 from DuckDB
./test_cleanup.sh --db postgres -n 3    # Remove last 3 from PostgreSQL
```

---

## 8. Files Not Committed (Sensitive/Generated)

These files exist locally but are gitignored:

| File | Reason |
|------|--------|
| `jobs/import/.env` | Contains real API credentials and database passwords |
| `jobs/import/tokens_polar.json` | Contains OAuth access/refresh tokens |
| `jobs/import/local_data/` | Contains DuckDB database and working files |

---

## 9. Key Learnings & Gotchas

1. **`config.py` has dual path resolution** — `os.getcwd()` for tokens (line 124) and `Path(__file__).parent.parent.parent` for data files (line 182). Both must resolve to the same repo root inside Docker.

2. **Azure CLI writes session files** — The `~/.azure` mount cannot be read-only. The `az` CLI creates `az.sess` and other files at runtime even for read operations like `AzureCliCredential`.

3. **Azure CLI must be installed in container** — `DefaultAzureCredential` tries multiple credential providers. Without the `az` binary, `AzureCliCredential` fails silently but all other providers may also fail if not configured.

4. **IPython is required** — The storage modules (`polar/storage/duckdb.py` and `postgres.py`) import `from IPython.display import display`. This works fine in notebooks but fails in plain Python. Adding `ipython>=8.0.0` to dependencies resolves this.

5. **macOS bash compatibility** — `${var^^}` (bash 4+) is not available in macOS's default `/bin/bash` (3.2). Use `tr '[:lower:]' '[:upper:]'` instead.

6. **DuckDB Date column is text** — The `Date` column in `workout_metadata` is stored as text in `DD-MM-YYYY` format. Lexical sorting gives wrong results. Use `strptime()` (DuckDB) or `to_date()` (PostgreSQL) for correct chronological ordering.

7. **Polar API maintenance windows** — The Polar AccessLink API has scheduled maintenance windows. The retry logic (3 attempts, 30s delay) handles transient 503 errors.

---

## 10. Workflow Diagram

```
┌─────────────────────────────────────────────────────────┐
│                   Docker Container                       │
│                                                         │
│  1. Load Config (.env → load_configuration())           │
│         │                                               │
│  2. run_polar_workflow(config)                           │
│         ├── Validate OAuth tokens                       │
│         ├── Register user with Polar API                │
│         ├── List all exercises                          │
│         ├── filter_new_exercises() (DB check)           │
│         ├── Download new TCX files                      │
│         ├── Convert TCX → CSV                           │
│         └── Upload TCX + CSV to Azure                   │
│         │                                               │
│  3. import_workout_from_directory()                      │
│         ├── Read CSV files from OUTPUT_DIR               │
│         ├── Parse metadata + timeseries                  │
│         ├── Fix missing HR (interpolation)               │
│         └── INSERT into workout_metadata + timeseries    │
│         │                                               │
│  4. upload_database_to_azure() [DuckDB only]            │
│         │                                               │
│  5. delete_files_from_directory(["*.CSV", "*.tcx"])      │
│                                                         │
└─────────────────────────────────────────────────────────┘

Volume Mounts:
  ./local_data    ↔  /app/local_data     (DuckDB + working files)
  ./tokens.json   →  /app/jobs/import/tokens_polar.json
  ~/.azure        ↔  /root/.azure        (Azure CLI credentials)
```
