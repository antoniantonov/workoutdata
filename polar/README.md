# Polar Workout Data

A comprehensive Python toolkit for working with Polar AccessLink workout data. This package provides:

- **OAuth authentication** with Polar AccessLink API
- **User and exercise management** via Polar API
- **TCX to CSV conversion** with proper metadata handling
- **Database imports** to DuckDB and PostgreSQL
- **Workout data visualization** with Plotly
- **Cloud storage integration** with Azure Blob Storage

## Installation

### From GitHub (recommended)

```bash
# Using uv
uv pip install git+https://github.com/tonkata/workoutdata.git#subdirectory=polar

# Using pip
pip install git+https://github.com/tonkata/workoutdata.git#subdirectory=polar
```

### With optional dependencies

```bash
# Azure Storage support
uv pip install "polar-workout-data[azure] @ git+https://github.com/tonkata/workoutdata.git#subdirectory=polar"

# Notebook support (IPython display)
uv pip install "polar-workout-data[notebook] @ git+https://github.com/tonkata/workoutdata.git#subdirectory=polar"

# All optional dependencies
uv pip install "polar-workout-data[all] @ git+https://github.com/tonkata/workoutdata.git#subdirectory=polar"
```

## Quick Start

### Configuration

Set up environment variables or create a `.env` file:

```bash
# Polar API credentials
CLIENT_ID=your_polar_client_id
CLIENT_SECRET=your_polar_client_secret

# Database (choose one)
DATABASE_TYPE=duckdb  # or 'postgres'
DUCKDB_PATH=./hr_data/database.duckdb

# PostgreSQL (if using postgres)
POSTGRES_CONNECTION_STRING=postgresql://user:pass@host:5432/dbname

# Optional: Azure Storage
AZURE_STORAGE_ENABLED=true
AZURE_STORAGE_ACCOUNT_NAME=your_storage_account
```

### Basic Usage

```python
from polar import load_configuration, run_polar_workflow

# Load configuration from environment
config = load_configuration()

# Run the complete OAuth + download workflow
result = run_polar_workflow(config)

print(f"Downloaded {len(result['new_exercises'])} new exercises")
```

### Database Operations

```python
from polar.storage import duckdb as storage
from polar import load_configuration

config = load_configuration()

# Import a workout CSV
result = storage.import_workout_csv("path/to/workout.csv", config)

# Query timeseries data
df = storage.get_timeseries_data(['11-05-2025_105946'], config)

# Get HR zones
zones = storage.get_hr_zones(config)
```

### Visualization

```python
from polar.utils.rendering import plot_hr_with_zones, piechart_hr_with_zones
from polar import load_configuration

config = load_configuration()

# Plot HR with zones overlay
plot_hr_with_zones(['11-05-2025_105946', '12-05-2025_093022'], config)

# Pie chart of time in zones
piechart_hr_with_zones('11-05-2025_105946', config)
```

## Package Structure

```
polar/
├── __init__.py          # Main exports and version
├── workflow.py          # High-level workflow orchestration
├── api/                 # Polar AccessLink API
│   ├── oauth.py         # OAuth callback server
│   ├── tokens.py        # Token management
│   ├── users.py         # User registration/info
│   └── exercises.py     # Exercise listing/download
├── storage/             # Database backends
│   ├── duckdb.py        # DuckDB operations
│   └── postgres.py      # PostgreSQL operations
├── converters/          # Data format conversion
│   └── tcx.py           # TCX to CSV conversion
├── ingest/              # Data processing
│   └── workouts.py      # HR interpolation, data cleaning
├── cloud/               # Cloud integrations
│   └── azure.py         # Azure Blob Storage
└── utils/               # Shared utilities
    ├── config.py        # Configuration loading
    ├── common.py        # Common helpers
    └── rendering.py     # Plotly visualizations
```

## License

MIT License - see [LICENSE](LICENSE) for details.
