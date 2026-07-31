"""Polar AccessLink Import Job — Full Workflow.

Replicates the complete workflow from notebooks/polar_accesslink_workflow_v0.2.md:
1. OAuth token validation / authorization
2. Restore the DuckDB database from Azure Blob Storage (DuckDB mode only)
3. Register user with Polar API
4. List exercises, filter new ones (database-based deduplication)
5. Download new exercises as TCX, convert to CSV, upload to Azure
6. Import CSVs into the configured database (DuckDB or PostgreSQL)
7. Upload DuckDB database to Azure (DuckDB mode only)
8. Cleanup processed TCX and CSV files

Configuration is loaded from environment variables (via .env file).
OAuth tokens are read from tokens_polar.json or environment variables.
"""
import sys
import time
from pathlib import Path

# Add repository root to path
# __file__ is jobs/import/main.py → parent.parent.parent = repo root
repo_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(repo_root))

from polar.utils.config import load_configuration
from polar.workflow import run_polar_workflow
from polar.storage import duckdb as duckdb_storage
from polar.storage import postgres as postgres_storage
from polar.ingest import workouts as import_tools

MAX_RETRIES = 3
RETRY_DELAY = 30  # seconds


def main():
    """Execute the full Polar AccessLink import workflow."""
    print("=" * 80)
    print("POLAR IMPORT JOB — Full Workflow (Docker)")
    print("=" * 80)
    print()

    # ── Step 1: Load configuration ──────────────────────────────────────
    print("Step 1: Loading configuration...")
    try:
        config = load_configuration()
    except (ValueError, FileNotFoundError) as e:
        print(f"❌ ERROR: Configuration failed: {e}")
        sys.exit(1)
    print()

    db_type = config.get('DATABASE_TYPE', 'duckdb')

    # ── Step 2: Restore the DuckDB database from Azure ──────────────────
    # The container is stateless, so without this the database would be empty
    # and every exercise would look new.
    if db_type == 'duckdb':
        print("Step 2: Restoring DuckDB database from Azure...")
        try:
            duckdb_storage.download_database_from_azure(config)
        except Exception as e:
            print(f"⚠️  WARNING: DuckDB restore from Azure failed: {e}")
        print()

    # ── Step 3: Run the Polar workflow (with retries for transient errors) ─
    print("Step 3: Running Polar AccessLink workflow...")
    result = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            result = run_polar_workflow(config=config, timeout=300)
            break
        except Exception as e:
            error_msg = str(e)
            if ("503" in error_msg or "Service" in error_msg) and attempt < MAX_RETRIES:
                print(f"⚠️  Attempt {attempt}/{MAX_RETRIES} failed (API unavailable). Retrying in {RETRY_DELAY}s...")
                time.sleep(RETRY_DELAY)
            else:
                print(f"❌ ERROR: Polar workflow failed: {e}")
                sys.exit(1)

    processed_csv_files = result.get('processed_csv_files', [])

    if not processed_csv_files:
        print("\nℹ️  No new CSV files to import. Workflow complete.")
        print("=" * 80)
        sys.exit(0)

    # ── Step 4: Import CSVs into the database ───────────────────────────
    glob_patterns = ["Anton_Antonov*.CSV"]

    print(f"\nStep 4: Importing CSVs into {db_type.upper()} database...")
    print("-" * 60)

    try:
        if db_type == 'postgres':
            summary = postgres_storage.import_workout_from_directory(
                glob_patterns, config
            )
        else:
            summary = duckdb_storage.import_workout_from_directory(
                glob_patterns, config
            )
        print(summary)
    except Exception as e:
        print(f"❌ ERROR: Database import failed: {e}")
        sys.exit(1)

    # ── Step 5: Upload DuckDB to Azure (DuckDB mode only) ──────────────
    if db_type == 'duckdb':
        print("\nStep 5: Uploading DuckDB database to Azure...")
        try:
            duckdb_storage.upload_database_to_azure(config)
        except Exception as e:
            print(f"⚠️  WARNING: DuckDB upload to Azure failed: {e}")

    # ── Step 6: Cleanup processed files ─────────────────────────────────
    print("\nStep 6: Cleaning up processed files...")
    print("-" * 60)

    deletion_patterns = ["*.CSV", "*.tcx"]
    try:
        deletion_summary = import_tools.delete_files_from_directory(
            deletion_patterns, config
        )
        print(deletion_summary)
    except Exception as e:
        print(f"⚠️  WARNING: File cleanup failed: {e}")

    # ── Done ────────────────────────────────────────────────────────────
    print()
    print("=" * 80)
    print("✅ POLAR IMPORT JOB COMPLETE")
    print("=" * 80)
    print(f"  - Database type: {db_type.upper()}")
    print(f"  - New CSVs processed: {len(processed_csv_files)}")
    if db_type == 'duckdb':
        print(f"  - DuckDB restored from Azure: yes (if a snapshot existed)")
        print(f"  - DuckDB uploaded to Azure: yes (if enabled)")
    print(f"  - Files cleaned up: yes")
    print("=" * 80)


if __name__ == "__main__":
    main()
