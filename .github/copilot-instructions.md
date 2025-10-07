# Copilot Project Instructions (workoutdata)

Concise operational context for AI assistants modifying this repo. Focus on heart rate (HR) workout ingestion, cleaning, storage (DuckDB), and visualization.

## 1. Domain & Data Flow
- Source: Polar-exported CSV workout files in `hr_data/` named `Anton_Antonov+_YYYY-MM-DD_HH-MM-SS.CSV` (ingestion glob: `Anton_Antonov*.CSV`).
- Metadata: First row of each CSV (Date, Start time, etc.). Time‑series rows begin after 2 header rows (`skiprows=2`).
- Derived key `workoutId`: `"YYYY-MM-DD_HHMMSS"` built from Date + Start time with colons removed. Serves as primary key / foreign key.
- Storage: DuckDB file `hr_data/database_v2.duckdb` with tables:
  - `workout_metadata` – one row per workout (schema inferred from first-row metadata).
  - `timeseries` – per-second HR samples plus `workoutId`.
- Zone definitions (for plotting) live in `hr_data/zones.csv` (sorted by `HR`).

## 2. Ingestion Patterns
- Primary, richer ingestion logic currently lives in the notebook `populate_duckdb.ipynb` (enhanced version of `tools.py` flow):
  - Functions: `import_workout_csv`, `fix_missing_hr`, `delete_workout_by_id`.
  - Duplicate guard: Skip import if `workout_metadata.workoutId` already exists.
  - On partial failure: Exception handler deletes any inserted rows for that `workoutId` from `timeseries` and `workout_metadata` (idempotent cleanup).
  - Success message: prints with a green check "✅ Imported.".
- Legacy / simplified batch script version: `tools.py` (kept for quick bulk import; lacks interpolation & cleanup sophistication). Favor updating notebook logic for new ingestion features, then optionally refactor into a reusable module.

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
| Add new metric column | Compute in ingestion function; include in `ts_view` before insert; recreate table schema if new columns introduced (migration note). |
| Re-run ingestion for a bad file | Call `delete_workout_by_id(db_path, workoutId)` then re-import its CSV. |
| Plot multiple workouts | Query each `workoutId`, add separate Plotly traces; use hovertemplate with `<extra></extra>` to suppress default. |
| Case-insensitive selection | Use `ILIKE '{prefix}%'` instead of `starts_with` if needed. |

## 6. DuckDB Usage
- Connect: `duckdb.connect("hr_data/database_v2.duckdb")`.
- Batch inserts leverage registered pandas DataFrames (`con.register("ts_view", df)`).
- Prefer incremental schema evolution via explicit `ALTER TABLE` if changing structure (avoid silent recreation losing data).

## 7. Safety / Sensitive Data
- Workout CSVs may contain personal metadata: Do NOT print or commit sensitive personal identifiers beyond `workoutId`.
- Before committing new notebooks, clear large or sensitive outputs if not essential.
- If a sensitive file was committed: Use history rewrite (e.g., `git filter-repo`) and add path to `.gitignore`.

## 8. Adding New Assistant-Aware Context
- If you create reusable ingestion utilities, consolidate into a new module (e.g., `ingest.py`) and update this file with function signatures.
- Keep this doc under 60 lines; replace outdated sections instead of appending noise.

## 9. Quick Reference Snippets
```python
# Duplicate guard pattern
exists = con.execute("SELECT 1 FROM workout_metadata WHERE workoutId=? LIMIT 1", (workoutId,)).fetchone()
if exists: return 'skipped'

# Cleanup on failure
for tbl in ("timeseries","workout_metadata"):
    con.execute(f"DELETE FROM {tbl} WHERE workoutId=?", (workoutId,))

# Case-insensitive prefix match
con.execute("SELECT * FROM timeseries WHERE workoutId ILIKE ?", (f"{prefix}%",))
```

## 10. When Unsure
- Prefer mirroring notebook logic over `tools.py` (notebook is canonical).
- Ask for clarification before altering schema or deleting data-wide.

_End of instructions. Ask the user if anything is missing when expanding scope._
