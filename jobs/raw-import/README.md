# Raw Import Job

Automated job for downloading TCX workout files from Polar AccessLink API and converting them to CSV format.

## Overview

This job executes the raw import workflow:
1. Lists exercises from Polar API via OAuth
2. Checks Azure Blob Storage for existing workouts (by workoutId)
3. Downloads only NEW TCX files not already in Azure Storage
4. Converts TCX files to Polar-compatible CSV format
5. Uploads both TCX and CSV files to Azure Blob Storage

## Setup

### Prerequisites

- [uv](https://github.com/astral-sh/uv) package manager installed
- Polar AccessLink API credentials (see Configuration below)

### Configuration

Create a `.env` file in this directory with the following variables:

```env
# Polar API Configuration
POLAR_CLIENT_ID=your_client_id_here
POLAR_CLIENT_SECRET=your_client_secret_here
POLAR_REDIRECT_PORT=5000
POLAR_MEMBER_ID=your_member_id_here

# File Paths
OUTPUT_DIR=../../hr_data

# Azure Storage Configuration (REQUIRED for this job)
AZURE_STORAGE_ENABLED=true
AZURE_STORAGE_ACCOUNT_NAME=your_storage_account
AZURE_STORAGE_CONTAINER_NAME=workout-data
```

**Note:** This job requires Azure Storage to be enabled to check for existing files and upload new ones.

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
- List all available exercises from Polar API
- Check Azure Storage for existing workouts (by CSV files in `polar_csv/` folder)
- Download only new exercises as TCX files (those not in Azure)
- Convert TCX to Polar-compatible CSV format
- Upload both TCX and CSV to Azure Blob Storage:
  - CSV files: `polar_csv/{workoutId}.csv`
  - TCX files: `polar_tcx/{workoutId}.tcx`
- Display summary statistics

Files are saved locally to `OUTPUT_DIR` and uploaded to Azure Storage.

## Troubleshooting

### OAuth Authorization Failed

- Ensure `POLAR_CLIENT_ID` and `POLAR_CLIENT_SECRET` are correct
- Check that redirect URI in Polar admin console matches `http://localhost:{REDIRECT_PORT}/callback`
- Delete `tokens_polar.json` to force re-authorization

### Azure Storage Errors

- Ensure `AZURE_STORAGE_ENABLED=true` in `.env` file
- Verify `AZURE_STORAGE_ACCOUNT_NAME` is correct
- Authenticate with Azure CLI: `az login`
- Check that you have permissions to access the storage account
- Verify the container exists (job will create it if missing)

### Missing Dependencies

```bash
# Reinstall dependencies
uv sync --reinstall
```

## Related

- Polar module: `../../polar/`
- Notebooks: `../../notebooks/`
- Output directory: `../../hr_data/`

## Docker Support

This job can be containerized using Docker. See `Dockerfile` for building a container image.
