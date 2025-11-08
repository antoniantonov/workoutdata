# Copilot Project Instructions (workoutdata)

Concise operational context for AI assistants modifying this repo. Focus on heart rate (HR) workout ingestion, cleaning, storage (DuckDB), and visualization.

## 1. Domain & Data Flow
- Source: Polar-exported CSV workout files in `hr_data/` named `Anton_Antonov+_YYYY-MM-DD_HH-MM-SS.CSV` (ingestion glob: `Anton_Antonov*.CSV`).
- Metadata: First row of each CSV (Date, Start time, etc.). Time‑series rows begin after 2 header rows (`skiprows=2`).
- Derived key `workoutId`: `"DD-MM-YYYY_HHMMSS"` built from Date + Start time with colons removed. Serves as primary key / foreign key.
- Storage: DuckDB file `hr_data/database_v2.duckdb` with tables:
  - `workout_metadata` – one row per workout (schema inferred from first-row metadata).
  - `timeseries` – per-second HR samples plus `workoutId`.
- Zone definitions (for plotting) live in `hr_data/zones.csv` (sorted by `HR`).
- Visualization: Plotly-based plotting functions in `hr-plotting-v0.2.ipynb` for line plots and pie charts.

## 2. Ingestion Patterns
- **Primary ingestion module**: `notebooks/import_tools.py` — properly refactored Python module with functions:
  - `import_workout_csv(csv_path, con, approved_columns)` – imports single workout with HR interpolation and column filtering.
  - `import_workout_from_directory(data_dir, glob_patterns)` – batch imports all matching CSVs with statistics reporting.
  - `fix_missing_hr(df)` – trims leading/trailing nulls and interpolates internal HR gaps.
  - `delete_workout_by_id(db_path, workout_id)` – removes workout from both tables.
- **Column filtering**: `approved_columns` parameter allows whitelisting columns (e.g., `["Sample rate", "Time", "HR (bpm)"]`). Non-approved columns are set to NULL to prevent schema drift.
- **Duplicate guard**: Skip import if `workout_metadata.workoutId` already exists.
- **Idempotent cleanup**: On failure, exception handler deletes any inserted rows for that `workoutId` from both tables.
- **Success/status messages**: "✅ Imported." (green check), "❌ Error", and summary statistics for batch imports.
- **Usage notebook**: `populate_duckdb.ipynb` demonstrates usage of import_tools.py functions.

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
| Add new metric column | Compute in `import_workout_csv()` function; include in `ts_view` before insert; recreate table schema if new columns introduced (migration note). |
| Re-run ingestion for a bad file | Call `delete_workout_by_id(db_path, workoutId)` then re-import with `import_workout_csv()`. |
| Batch import workouts | Use `import_workout_from_directory(data_dir, glob_patterns)` with approved_columns list. |
| Plot multiple workouts | Query each `workoutId`, add separate Plotly traces; use hovertemplate with `<extra></extra>` to suppress default (see `hr-plotting-v0.2.ipynb`). |
| Case-insensitive selection | Use `ILIKE '{prefix}%'` instead of `starts_with` if needed. |

## 6. DuckDB Usage
- Connect: `duckdb.connect("hr_data/database_v2.duckdb")`.
- Batch inserts leverage registered pandas DataFrames (`con.register("ts_view", df)`).
- Prefer incremental schema evolution via explicit `ALTER TABLE` if changing structure (avoid silent recreation losing data).

## 7. Safety / Sensitive Data
- Workout CSVs may contain personal metadata: Do NOT print or commit sensitive personal identifiers beyond `workoutId`.
- Before committing new notebooks, clear large or sensitive outputs if not essential.
- If a sensitive file was committed: Use history rewrite (e.g., `git filter-repo`) and add path to `.gitignore`.

## 8. Repository Structure & Tools
- **Notebooks**:
  - `populate_duckdb.ipynb` – demonstrates importing workouts using import_tools.py.
  - `hr-plotting-v0.2.ipynb` – Plotly visualization functions (line plots with zones, pie charts).
  - `hr-plotting-v0.1.ipynb` – **DEPRECATED**: Do not use or modify. Kept only for educational purposes for human developers.
  - `calories_calculator.ipynb` – calorie-related analysis.
  - `polar_accesslink_workflow.ipynb` – Polar AccessLink API integration.
- **Scripts**:
  - `scripts/run_duckdb_local.sh` – copies database to temp folder and launches DuckDB web UI (`duckdb -ui`).
- **Data directories**:
  - `hr_data/` – workout CSVs, database files, zones.csv.
  - `data/` – VO2max data and other reference files.
- Keep this doc concise; replace outdated sections instead of appending noise.

## 9. Quick Reference Snippets
```python
# Import single workout
from notebooks.import_tools import import_workout_csv
import duckdb
con = duckdb.connect("hr_data/database_v2.duckdb")
result = import_workout_csv("path/to/workout.CSV", con, 
                            approved_columns=["Sample rate", "Time", "HR (bpm)"])
# Returns: 'imported', 'skipped', or 'error'

# Batch import from directory
from notebooks.import_tools import import_workout_from_directory
stats = import_workout_from_directory("hr_data", "Anton_Antonov*.CSV")
# Returns: {"total": int, "processed": int, "skipped": int, "errors": int}

# Delete workout by ID
from notebooks.import_tools import delete_workout_by_id
delete_workout_by_id("hr_data/database_v2.duckdb", "11-05-2025_105946")

# Case-insensitive prefix match
con.execute("SELECT * FROM timeseries WHERE workoutId ILIKE ?", (f"{prefix}%",))
```

## 10. When Unsure
- The `notebooks/import_tools.py` module is canonical for ingestion logic. Notebooks demonstrate usage.
- Ask for clarification before altering schema or deleting data-wide.
- For visualization, reference `hr-plotting-v0.2.ipynb` as the current plotting implementation.

_End of instructions. Ask the user if anything is missing when expanding scope._
