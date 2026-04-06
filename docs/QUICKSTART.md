# Quick Start Guide - Polar Package Structure

## What Changed?

✅ **Python modules reorganized into `polar/` package**
✅ **Modules organized by functionality into subpackages**
✅ **Notebooks updated with new import paths**
✅ **New automated job created in `jobs/import/`**
✅ **No logic changes - only imports and organization updated**

## Package Structure

The `polar/` package is organized by functionality:

```
polar/
├── __init__.py          # Main package with re-exports
├── workflow.py          # Main workflow orchestration
├── api/                 # Polar AccessLink API interactions
│   ├── users.py         # User registration and info
│   ├── exercises.py     # Exercise listing and management
│   ├── oauth.py         # OAuth authentication flow
│   └── tokens.py        # Token management
├── storage/             # Database operations
│   ├── duckdb.py        # DuckDB operations
│   └── postgres.py      # PostgreSQL operations
├── converters/          # Data format conversion
│   └── tcx.py           # TCX to CSV conversion
├── ingest/              # Workout data ingestion
│   └── workouts.py      # CSV import and processing
├── cloud/               # Cloud storage integrations
│   └── azure.py         # Azure Blob Storage
└── utils/               # Shared utilities
    ├── common.py        # Common helper functions
    ├── config.py        # Configuration loading
    ├── validations.py   # Validation checks
    └── rendering.py     # Data visualization
```

## Using Notebooks

All notebooks work the same way, just with updated imports at the top of cells:

```python
import sys
from pathlib import Path

# Add repository root to path
repo_root = Path.cwd().parent if 'notebooks' in str(Path.cwd()) else Path.cwd()
sys.path.insert(0, str(repo_root))

# Now import from polar package
from polar import workflow
from polar.storage import duckdb, postgres
from polar.ingest import workouts as import_tools
```

### Available Notebooks

- **`polar_accesslink_workflow_v0.2.md`** - Download workouts from Polar API
- **`populate_duckdb.md`** - Import CSV files to database
- **`hr_plotting_v0.2.md`** - Visualize workout data
- **`calories_calculator_hr_zones.md`** - Calculate calorie burn rates

## Using the Automated Job

### Setup (One-time)

1. **Install uv** (if not already installed):
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

2. **Configure environment** (already done if `.env` exists):
   ```bash
   cd jobs/import
   # Edit .env file with your Polar API credentials
   ```

### Running the Job

```bash
cd jobs/import
uv run main.py
```

This will:
1. Create virtual environment (first run only)
2. Install dependencies (first run only)
3. Download new workouts from Polar API
4. Convert TCX to CSV
5. Import to DuckDB database

### What `uv run` Does

- **First run:** Creates `.venv/` and installs all dependencies
- **Subsequent runs:** Uses existing virtual environment
- **Updates:** Automatically syncs dependencies if `pyproject.toml` changes

## File Locations

```
workoutdata/
├── polar/                  # ← All Python modules organized by functionality
│   ├── api/                # ← Polar API interactions
│   ├── storage/            # ← Database operations
│   ├── converters/         # ← Data conversions
│   ├── ingest/             # ← Workout ingestion
│   ├── cloud/              # ← Cloud storage
│   └── utils/              # ← Utilities
├── jobs/import/            # ← Automated job with uv
│   ├── main.py
│   ├── pyproject.toml
│   └── .env
├── notebooks/              # ← MyST Markdown notebooks (via Jupytext)
│   ├── *.md
│   └── .env
└── hr_data/                # ← Database and CSV files
    └── database_v2.duckdb
```

## Troubleshooting

### Notebooks: ModuleNotFoundError

**Problem:** `ModuleNotFoundError: No module named 'polar'`

**Solution:** Make sure the first cell includes:
```python
import sys
from pathlib import Path
repo_root = Path.cwd().parent if 'notebooks' in str(Path.cwd()) else Path.cwd()
sys.path.insert(0, str(repo_root))
```

### Jobs: Import errors

**Problem:** Can't import from polar

**Solution:** Check that main.py has:
```python
repo_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(repo_root))
```

### UV: Command not found

**Problem:** `uv: command not found`

**Solution:** Install uv:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
# Or use homebrew: brew install uv
```

### UV: Dependency errors

**Problem:** Dependencies not installing

**Solution:** Reinstall:
```bash
cd jobs/import
rm -rf .venv
uv sync
```

## Testing

### Test notebooks can import from polar:
```bash
cd notebooks
python3 -c "
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd().parent))
from polar import workflow
from polar.storage import duckdb
print('✅ Imports working!')
"
```

### Test jobs/import can run:
```bash
cd jobs/import
python3 -c "
import sys
from pathlib import Path
repo_root = Path('.').resolve().parent.parent
sys.path.insert(0, str(repo_root))
from polar.workflow import run_polar_workflow
print('✅ Ready to run!')
"
```

## Summary

- **Python files:** All in `polar/` organized by functionality
- **Notebooks:** MyST Markdown `.md` files in `notebooks/` (open as notebooks via Jupytext)
- **Automated job:** Use `uv run main.py` in `jobs/import/`
- **No logic changes:** Only import paths and organization updated

Everything should work exactly as before! 🎉
