# Polar Import Job (Full Workflow)

Automated Docker job that replicates the complete `polar_accesslink_workflow_v0.2` notebook:

1. **OAuth & User Registration** — Validates tokens, registers with Polar API
2. **Exercise Discovery** — Lists all exercises, filters new ones by database
3. **Download & Convert** — Downloads TCX files, converts to Polar-compatible CSV
4. **Azure Upload** — Uploads TCX + CSV files to Azure Blob Storage
5. **Database Import** — Imports CSVs into DuckDB or PostgreSQL
6. **DuckDB Upload** — Uploads DuckDB database to Azure (DuckDB mode only)
7. **Cleanup** — Deletes processed TCX and CSV files

## Setup

### Prerequisites

- Docker and Docker Compose
- Polar AccessLink API credentials
- Azure CLI credentials (`az login`) for Azure Storage
- Valid `tokens_polar.json` (from a previous OAuth flow)

### Configuration

1. Copy and edit the `.env` file:
   ```bash
   cp .env.example .env
   # Edit .env with your credentials
   ```

2. Ensure `tokens_polar.json` exists (from a previous OAuth authorization).

3. Create the `local_data/` directory for DuckDB mode:
   ```bash
   mkdir -p local_data
   # Optionally copy an existing DuckDB database:
   # cp ../../hr_data/database_v2.duckdb local_data/
   ```

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `POLAR_CLIENT_ID` | Yes | Polar API client ID |
| `POLAR_CLIENT_SECRET` | Yes | Polar API client secret |
| `POLAR_REDIRECT_PORT` | No | OAuth callback port (default: 5000) |
| `POLAR_MEMBER_ID` | No | Polar member ID |
| `DATABASE_TYPE` | No | `duckdb` (default) or `postgres` |
| `POLAR_TOKENS_FILE` | No | Path to token file (default: jobs/import/tokens_polar.json) |
| `DUCKDB_PATH` | No | Path to DuckDB file (default: local_data/database_v2.duckdb) |
| `OUTPUT_DIR` | No | Output directory (default: local_data) |
| `AZURE_STORAGE_ENABLED` | No | Enable Azure upload (default: false) |
| `AZURE_STORAGE_ACCOUNT_NAME` | If Azure | Storage account name |
| `AZURE_STORAGE_CONTAINER_NAME` | No | Container name (default: workout-data) |
| `POSTGRES_HOST` | If postgres | PostgreSQL hostname |
| `POSTGRES_PORT` | No | PostgreSQL port (default: 5432) |
| `POSTGRES_DATABASE` | If postgres | Database name |
| `POSTGRES_USER` | If postgres | Username |
| `POSTGRES_PASSWORD` | If postgres | Password |

## Usage

### Run with Docker Compose

```bash
cd jobs/import

# Build and run (DuckDB mode — set DATABASE_TYPE=duckdb in .env)
docker compose build
docker compose up

# Run with PostgreSQL (set DATABASE_TYPE=postgres in .env)
docker compose up
```

### Test Modes

**DuckDB (local):**
- Set `DATABASE_TYPE=duckdb` in `.env`
- DuckDB file is stored in `local_data/` (volume-mounted into container)
- Downloaded files (TCX/CSV) are also saved to `local_data/`

**PostgreSQL (remote):**
- Set `DATABASE_TYPE=postgres` in `.env`
- Configure `POSTGRES_*` variables to point to your PostgreSQL server
- Downloaded files are still saved to `local_data/`

### Test Cleanup Script

For testing, use `test_cleanup.sh` to remove the last N workout entries so the job has new data to process:

```bash
# Remove last 2 entries from local DuckDB
./test_cleanup.sh --db duckdb

# Remove last 2 entries from PostgreSQL
./test_cleanup.sh --db postgres

# Remove last 3 entries
./test_cleanup.sh --db duckdb -n 3
```

**Note:** This script runs on the host (not in Docker). It requires `duckdb` CLI for DuckDB mode or `psql` for PostgreSQL mode.

## Files

| File | Description |
|------|-------------|
| `main.py` | Main entry point — full import workflow |
| `pyproject.toml` | Dependencies and project metadata |
| `Dockerfile` | Docker image definition |
| `docker-compose.yml` | Docker Compose configuration |
| `.env` | Environment variables (gitignored) |
| `.env.example` | Example environment configuration |
| `tokens_polar.json` | OAuth tokens (gitignored) |
| `test_cleanup.sh` | Test helper to remove DB entries (excluded from Docker) |
| `local_data/` | Volume mount for DuckDB + files (gitignored) |

## Troubleshooting

### Polar API 503 Errors
The job includes retry logic (3 attempts, 30s delay). If the API remains unavailable, wait and re-run.

### Azure Storage Authentication
Mount Azure CLI credentials into the container (done automatically in docker-compose.yml):
```yaml
volumes:
  - ~/.azure:/root/.azure:ro
```

### Path Resolution
The container runs with WORKDIR `/app` (repo root). All paths in `.env` are relative to this root. The `config.py` module resolves paths using `Path(__file__).parent.parent.parent` which also resolves to `/app`.

## Related

- Notebook: `../../notebooks/polar_accesslink_workflow_v0.2.md`
- Polar module: `../../polar/`
- Storage modules: `../../polar/storage/`
