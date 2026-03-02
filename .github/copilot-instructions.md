# Copilot Project Instructions (workoutdata)

Operational context for AI assistants modifying this repo. This project provides a complete workflow for downloading, storing, and visualizing heart rate workout data from Polar devices.

## 1. Project Overview

**Purpose**: Download workout data from Polar AccessLink API, store in databases (DuckDB/PostgreSQL), and visualize heart rate analytics.

**Key Workflows**:
1. **OAuth Authentication** → Polar AccessLink API
2. **Exercise Download** → TCX files from Polar
3. **TCX to CSV Conversion** → Polar-compatible format
4. **Database Import** → DuckDB or PostgreSQL
5. **Visualization** → HR line plots with zones, pie charts
6. **Cloud Backup** → Azure Blob Storage (optional)

## 2. Package Structure (`polar/`)

The `polar` package (v0.2.0) is a publishable Python package. Install from GitHub:
```bash
uv pip install git+https://github.com/tonkata/workoutdata.git#subdirectory=polar
```

### Module Organization

| Module | Purpose |
|--------|---------|
| `polar/__init__.py` | Re-exports all public functions |
| `polar/workflow.py` | `run_polar_workflow()` - orchestrates complete OAuth → download → convert flow |
| `polar/api/oauth.py` | Local callback server, authorization flow with CSRF protection |
| `polar/api/tokens.py` | Token save/load/validate, OAuth token exchange |
| `polar/api/users.py` | User registration, user info, physical info from Polar API |
| `polar/api/exercises.py` | List exercises, download TCX, filter new exercises |
| `polar/storage/duckdb.py` | DuckDB import, query, HR zones, Azure upload |
| `polar/storage/postgres.py` | PostgreSQL import, query, HR zones |
| `polar/converters/tcx.py` | TCX XML → Polar CSV format conversion |
| `polar/ingest/workouts.py` | `fix_missing_hr()` interpolation, calorie expansion |
| `polar/cloud/azure.py` | Azure Blob Storage upload/list using DefaultAzureCredential |
| `polar/utils/config.py` | `load_configuration()` - loads from `.env` or environment |
| `polar/utils/rendering.py` | Plotly visualization: `plot_hr_with_zones()`, `piechart_hr_with_zones()` |
| `polar/utils/common.py` | `get_field()` helper, `process_vo2max_data_for_calories()` |

## 3. Data Model

### CSV Format (Polar-compatible)
- **Row 1**: Metadata column headers (28 columns)
- **Row 2**: Metadata values (Name, Sport, Date, Start time, Duration, HR stats, etc.)
- **Row 3+**: Time-series data (Sample rate, Time, HR (bpm), etc.)

### Database Tables
| Table | Description |
|-------|-------------|
| `workout_metadata` | One row per workout, schema from CSV row 1-2 |
| `timeseries` | Per-second HR samples with `workoutId` foreign key |
| `hr_zones` | HR zone definitions from `zones.csv` (Zone, HR columns) |
| `calories_per_hr` | Calories per HR from VO2max data (optional) |

### Primary Key: `workoutId`
- Format: `"DD-MM-YYYY_HHMMSS"` (e.g., `"11-05-2025_105946"`)
- Derived from: Date + Start time with colons removed
- Used as: Primary key in `workout_metadata`, foreign key in `timeseries`

## 4. Configuration

All configuration via environment variables (`.env` file or system env).

### Required Variables
```bash
POLAR_CLIENT_ID='your-client-id'
POLAR_CLIENT_SECRET='your-client-secret'
POLAR_TOKENS_FILE=notebooks/tokens_polar.json
```

### Database Configuration
```bash
DATABASE_TYPE=duckdb  # or 'postgres'

# DuckDB
DUCKDB_PATH=hr_data/database_v2.duckdb

# PostgreSQL (when DATABASE_TYPE=postgres)
POSTGRES_HOST='hostname'
POSTGRES_PORT=5432
POSTGRES_DATABASE='workoutdata'
POSTGRES_USER='username'
POSTGRES_PASSWORD='password'
# Or use connection string:
# POSTGRES_CONNECTION_STRING='postgresql://user:pass@host:5432/db'
```

### File Paths
```bash
VO2MAX_DATA_PATH=data/v02max_data.csv
ZONES_CSV_PATH=hr_data/zones.csv
OUTPUT_DIR=hr_data
```

### Azure Storage (Optional)
```bash
AZURE_STORAGE_ENABLED=true
AZURE_STORAGE_ACCOUNT_NAME='your-account'
AZURE_STORAGE_CONTAINER_NAME='workoutdata'
```

## 5. Key Functions

### Complete Workflow
```python
from polar import run_polar_workflow, load_configuration

config = load_configuration()
result = run_polar_workflow(config, timeout=300)
# Returns: polar_user_id, access_token, exercises, new_exercises, 
#          downloaded_tcx_files, processed_csv_files
```

### Database Operations
```python
from polar.storage import duckdb as storage  # or postgres

# Batch import from directory
stats = storage.import_workout_from_directory(["Anton_Antonov*.CSV"], config)

# Query data
timeseries_df = storage.get_timeseries_data(['workout_id'], config)
metadata_df = storage.get_workout_metadata(['workout_id'], config)
zones_df = storage.get_hr_zones(config)

# Delete and re-import
storage.delete_workout_by_id("11-05-2025_105946", config)
```

### Visualization
```python
from polar.utils.rendering import plot_hr_with_zones, piechart_hr_with_zones

# Line plot with HR zones overlay
plot_hr_with_zones(['workout_id_1', 'workout_id_2'], config)

# Pie chart of time in zones
piechart_hr_with_zones('workout_id', config)
```

## 6. HR Cleaning Logic (`fix_missing_hr`)
Located in `polar/ingest/workouts.py`:
1. Trims leading/trailing rows with null HR
2. Groups consecutive null values
3. Linearly interpolates between surrounding known values
4. Preserves sign of change (handles ascending/descending HR)
5. Falls back to forward/backward fill when only one bound exists

## 7. Repository Structure

```
workoutdata/
├── polar/                    # Main Python package (publishable)
│   ├── __init__.py          # Package exports
│   ├── workflow.py          # run_polar_workflow()
│   ├── pyproject.toml       # Package metadata
│   ├── api/                 # Polar AccessLink API
│   │   ├── oauth.py         # OAuth callback server
│   │   ├── tokens.py        # Token management
│   │   ├── users.py         # User registration/info
│   │   └── exercises.py     # Exercise listing/download
│   ├── storage/             # Database backends
│   │   ├── duckdb.py        # DuckDB operations
│   │   └── postgres.py      # PostgreSQL operations
│   ├── converters/          # Format conversion
│   │   └── tcx.py           # TCX → CSV conversion
│   ├── ingest/              # Data processing
│   │   └── workouts.py      # HR interpolation
│   ├── cloud/               # Cloud integrations
│   │   └── azure.py         # Azure Blob Storage
│   └── utils/               # Shared utilities
│       ├── config.py        # Configuration loading
│       ├── rendering.py     # Plotly visualization
│       └── common.py        # Helper functions
├── notebooks/               # Jupyter notebooks
│   ├── .env                 # Environment config (gitignored)
│   ├── .env.sample          # Example config
│   ├── tokens_polar.json    # OAuth tokens (gitignored)
│   ├── polar_accesslink_workflow_v0.2.ipynb  # Main workflow
│   ├── populate_duckdb.ipynb                 # Database import demo
│   ├── hr_plotting_v0.2.ipynb                # Visualization demo
│   ├── calories_calculator_hr_zones.ipynb    # Calorie analysis
│   └── upload_existing_workouts_to_azure.ipynb
├── hr_data/                 # Data files
│   ├── database_v2.duckdb   # Main DuckDB database
│   ├── zones.csv            # HR zone definitions
│   └── *.CSV                # Workout files (gitignored)
├── data/                    # Reference data
│   └── v02max_data.csv      # VO2max/calorie data
├── jobs/                    # Automation scripts
│   └── import/              # Raw import job
│       └── main.py          # Downloads TCX, checks Azure, uploads
├── scripts/
│   └── run_duckdb_local.sh  # Launch DuckDB web UI
├── docs/                    # Documentation
│   ├── QUICKSTART.md
│   ├── DOCKER.md
│   └── POSTGRES_SETUP.md
└── tests/                   # Test files
```

## 8. Notebooks

| Notebook | Purpose |
|----------|---------|
| `polar_accesslink_workflow_v0.2.ipynb` | Complete OAuth → download → import workflow |
| `populate_duckdb.ipynb` | Manual database import from CSV files |
| `hr_plotting_v0.2.ipynb` | HR visualization with zones |
| `calories_calculator_hr_zones.ipynb` | Calorie analysis from VO2max data |
| `upload_existing_workouts_to_azure.ipynb` | Bulk upload to Azure Storage |

## 9. Common Tasks

| Task | Approach |
|------|----------|
| Run full workflow | Execute `polar_accesslink_workflow_v0.2.ipynb` |
| Import existing CSVs | Use `storage.import_workout_from_directory()` |
| Re-import a workout | `storage.delete_workout_by_id()` then import |
| Add HR zones | Edit `hr_data/zones.csv`, run `storage.ensure_hr_zones_table()` |
| Query timeseries | `storage.get_timeseries_data(workout_ids, config)` |
| Plot workouts | `plot_hr_with_zones(workout_ids, config)` |
| Launch DuckDB UI | `./scripts/run_duckdb_local.sh` then `duckdb -ui` |

## 10. Safety Guidelines

- **Tokens**: Never commit `tokens_polar.json` (gitignored)
- **Credentials**: Use `.env` files (gitignored), never hardcode secrets
- **Personal data**: CSV files may contain personal metadata - avoid committing
- **Schema changes**: Ask before altering database schema
- **Deletions**: Confirm before bulk delete operations

## 11. Dependencies

Core: `requests`, `pandas`, `plotly`, `duckdb`, `psycopg[binary]`, `python-dotenv`

Optional:
- Azure: `azure-storage-blob`, `azure-identity`
- Notebook: `ipython`

Install all: `pip install polar-workout-data[all]`
