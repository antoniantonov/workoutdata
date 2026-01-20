# Copilot Project Instructions (workoutdata)

Concise operational context for AI assistants modifying this repo. Focus on heart rate (HR) workout ingestion, cleaning, storage (DuckDB), and visualization.

## 1. Domain & Data Flow
- Source: Polar-exported CSV workout files in `hr_data/` named `Anton_Antonov+_YYYY-MM-DD_HH-MM-SS.CSV` (ingestion glob: `Anton_Antonov*.CSV`).
- Metadata: First row of each CSV (Date, Start time, etc.). Time‑series rows begin after 2 header rows (`skiprows=2`).
- Derived key `workoutId`: `"DD-MM-YYYY_HHMMSS"` built from Date + Start time with colons removed. Serves as primary key / foreign key.
- Storage: Configurable database backend (DuckDB or PostgreSQL) with tables:
  - `workout_metadata` – one row per workout (schema inferred from first-row metadata).
  - `timeseries` – per-second HR samples plus `workoutId`.
  - `hr_zones` – HR zone definitions populated from zones.csv.
- Zone definitions (for plotting) stored in database, sourced from `hr_data/zones.csv` (sorted by `HR`).
- Visualization: Plotly-based plotting functions in `polar/utils/rendering.py` for line plots and pie charts.

## 2. Ingestion Patterns
- **Primary ingestion modules**: Storage modules in `polar/storage/` — properly refactored Python modules:
  - `polar/storage/duckdb.py` – DuckDB-specific functions
  - `polar/storage/postgres.py` – PostgreSQL-specific functions
  - Both expose: `import_workout_csv()`, `import_workout_from_directory()`, `delete_workout_by_id()`, `ensure_hr_zones_table()`, query functions
- **Column filtering**: `approved_columns` parameter allows whitelisting columns (e.g., `["Sample rate", "Time", "HR (bpm)"]`). Non-approved columns are set to NULL to prevent schema drift.
- **Duplicate guard**: Skip import if `workout_metadata.workoutId` already exists.
- **Idempotent cleanup**: On failure, exception handler deletes any inserted rows for that `workoutId` from both tables.
- **Success/status messages**: "✅ Imported." (green check), "❌ Error", and summary statistics for batch imports.
- **Usage notebook**: `populate_duckdb.ipynb` demonstrates usage of storage module functions.

## 3. HR Cleaning Logic (`fix_missing_hr`)
- Trims leading/trailing rows with null HR.
- Detects consecutive null groups (internal gaps only) and linearly interpolates between surrounding known HR values.
- Interpolation keeps sign of change (handles descending HR) and rounds to whole numbers (`round(..., 0)`).
- Falls back to forward or backward fill when only one bound exists; warns if neither bound present.

## 4. Conventions & Naming
- Always add / propagate `workoutId` before inserting into `timeseries`.
- Never modify raw CSVs; transformations occur in-memory.
- Keep schema creation idempotent: `CREATE TABLE IF NOT EXISTS ... SELECT * FROM view LIMIT 0` pattern.
- Use parameterized SQL for lookups / deletes: `WHERE workoutId = ?`.

## 5. Typical Assistant Tasks & Examples
| Task | Approach |
|------|----------|
| Add new metric column | Compute in `import_workout_csv()` function in storage module; include in data before insert; recreate table schema if new columns introduced (migration note). |
| Re-run ingestion for a bad file | Call `delete_workout_by_id(workoutId, config)` from storage module then re-import with `import_workout_csv()`. |
| Batch import workouts | Use `import_workout_from_directory(glob_patterns, config)` with approved_columns list. |
| Plot multiple workouts | Use `plot_hr_with_zones(workoutIds, config)` from `polar.utils.rendering`. |
| Query workout data | Use storage module functions: `get_timeseries_data()`, `get_workout_metadata()`, `get_hr_zones()`, `get_calories_per_hr()`. |
| Case-insensitive selection | Use `ILIKE '{prefix}%'` for PostgreSQL or appropriate DuckDB equivalent. |

## 6. Database Usage
- **Configuration-based**: All database operations use config dictionary from `polar.utils.config.load_configuration()`.
- **DATABASE_TYPE**: Environment variable controls backend ('duckdb' or 'postgres').
- **DuckDB**: Connect via storage module functions, no direct connection needed.
- **PostgreSQL**: Automatically handles connection pooling via storage module.
- Batch inserts leverage registered pandas DataFrames (DuckDB) or executemany (PostgreSQL).
- Prefer incremental schema evolution via explicit `ALTER TABLE` if changing structure (avoid silent recreation losing data).

## 7. Safety / Sensitive Data
- Workout CSVs may contain personal metadata: Do NOT print or commit sensitive personal identifiers beyond `workoutId`.
- Before committing new notebooks, clear large or sensitive outputs if not essential.
- If a sensitive file was committed: Use history rewrite (e.g., `git filter-repo`) and add path to `.gitignore`.

## 8. Repository Structure & Tools
- **Storage Modules** (in `polar/storage/`):
  - `duckdb.py` – DuckDB storage backend with import, query, and HR zones functions
  - `postgres.py` – PostgreSQL storage backend with import, query, and HR zones functions
- **Rendering** (in `polar/utils/`):
  - `rendering.py` – Visualization functions using config-based database queries
  - Functions: `plot_hr_with_zones(workoutIds, config)`, `piechart_hr_with_zones(workoutId, config)`
- **Notebooks**:
  - `populate_duckdb.ipynb` – demonstrates importing workouts using storage modules.
  - `hr-plotting-v0.2.ipynb` – demonstrates visualization with rendering functions.
  - `hr-plotting-v0.1.ipynb` – **DEPRECATED**: Do not use or modify. Kept only for educational purposes for human developers.
  - `calories_calculator.ipynb` – calorie-related analysis.
  - `polar_accesslink_workflow.ipynb` – Polar AccessLink API integration.
- **Polar AccessLink Modules** (in `polar/` subdirectories):
  - `utils/config.py` – Configuration loading from environment variables (DATABASE_TYPE, paths, API credentials).
  - `api/` – API client modules for Polar AccessLink
  - `cloud/` – Azure Storage integration
  - `converters/` – TCX to CSV conversion
  - `ingest/` – Workout data ingestion and processing
- **Scripts**:
  - `scripts/run_duckdb_local.sh` – copies database to temp folder and launches DuckDB web UI (`duckdb -ui`).
- **Data directories**:
  - `hr_data/` – workout CSVs, database files, zones.csv.
  - `data/` – VO2max data and other reference files.
- Keep this doc concise; replace outdated sections instead of appending noise.

## 8.1 Azure Storage Configuration (Optional)
To enable automatic upload of workout CSVs to Azure Blob Storage:
1. Install Azure SDK: `pip install azure-storage-blob azure-identity`
2. Set environment variables:
   - `AZURE_STORAGE_ENABLED=true`
   - `AZURE_STORAGE_ACCOUNT_NAME=<your-storage-account>`
   - `AZURE_STORAGE_CONTAINER_NAME=workout-data` (optional, default: 'workout-data')
3. Authenticate via `az login` (local) or Managed Identity (Azure cloud)
4. The workflow will automatically upload CSVs after download.

## 9. Quick Reference Snippets
```python
# Load configuration
from polar.utils.config import load_configuration
config = load_configuration()

# Import single workout (DuckDB or PostgreSQL based on config)
from polar.storage import duckdb as storage  # or postgres as storage
result = storage.import_workout_csv("path/to/workout.CSV", conn, 
                                    approved_columns=["Sample rate", "Time", "HR (bpm)"])
# Returns: 'imported', 'skipped', or 'error'

# Batch import from directory
stats = storage.import_workout_from_directory("Anton_Antonov*.CSV", config)
# Returns: {"total": int, "processed": int, "skipped": int, "errors": int}

# Delete workout by ID
storage.delete_workout_by_id("11-05-2025_105946", config)

# Initialize HR zones table
storage.ensure_hr_zones_table(config)

# Query data
zones_df = storage.get_hr_zones(config)
timeseries_df = storage.get_timeseries_data(['workout_id_1', 'workout_id_2'], config)
metadata_df = storage.get_workout_metadata(['workout_id_1'], config)
calories_df = storage.get_calories_per_hr(config)

# Visualize workouts
from polar.utils.rendering import plot_hr_with_zones, piechart_hr_with_zones
plot_hr_with_zones(['workout_id_1', 'workout_id_2'], config)
piechart_hr_with_zones('workout_id_1', config)
```

## 10. When Unsure
- The `polar/storage/` modules are canonical for database operations. Use config-based functions.
- Ask for clarification before altering schema or deleting data-wide.
- For visualization, use functions from `polar.utils.rendering` with config parameter.

_End of instructions. Ask the user if anything is missing when expanding scope._
