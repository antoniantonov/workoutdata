# Polar Workout Import Job

Automated job for downloading and importing workout data from Polar AccessLink API.

## Overview

This job executes the complete Polar AccessLink workflow:
1. Downloads new exercises from Polar API via OAuth
2. Converts TCX files to Polar-compatible CSV format
3. Imports CSVs into DuckDB database

## Setup

### Prerequisites

- [uv](https://github.com/astral-sh/uv) package manager installed
- Polar AccessLink API credentials (see Configuration below)

### Configuration

Create a `.env` file in this directory with the following variables:

```env
POLAR_CLIENT_ID=your_client_id_here
POLAR_CLIENT_SECRET=your_client_secret_here
POLAR_REDIRECT_PORT=5000
POLAR_MEMBER_ID=your_member_id_here
DUCKDB_PATH=../../hr_data/database_v2.duckdb
OUTPUT_DIR=../../hr_data
```

### First Run

On the first run, the job will:
1. Open your browser for OAuth authorization
2. Create `tokens_polar.json` to store access/refresh tokens
3. Register with Polar API (idempotent operation)

Subsequent runs will use the stored tokens (auto-refresh if expired).

## Usage

### Run with uv

```bash
# Run the import job
uv run main.py
```

The `uv run` command will:
- Create a virtual environment (if needed)
- Install all dependencies from pyproject.toml
- Execute main.py

### Manual Python execution

```bash
# Install dependencies
uv sync

# Run with Python
uv run python main.py
```

## Files

- `main.py` - Main entry point for the import job
- `pyproject.toml` - Project dependencies and metadata
- `.env` - Environment variables (gitignored)
- `tokens_polar.json` - OAuth tokens (gitignored, created on first run)
- `README.md` - This file

## Output

The job will:
- Download new exercises as TCX files
- Convert TCX to CSV (saved to `OUTPUT_DIR` from config)
- Import CSVs into DuckDB database
- Display summary statistics

## Troubleshooting

### OAuth Authorization Failed

- Ensure `POLAR_CLIENT_ID` and `POLAR_CLIENT_SECRET` are correct
- Check that redirect URI in Polar admin console matches `http://localhost:{REDIRECT_PORT}/callback`
- Delete `tokens_polar.json` to force re-authorization

### Import Errors

- Verify `DUCKDB_PATH` points to correct database file
- Check that database tables exist (run populate_duckdb.ipynb first if needed)
- Ensure CSV files have correct format (metadata rows + time-series)

### Missing Dependencies

```bash
# Reinstall dependencies
uv sync --reinstall
```

## Related

- Source code: `../../src/`
- Notebooks: `../../notebooks/`
- Database: `../../hr_data/database_v2.duckdb`
