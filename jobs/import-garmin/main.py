"""Garmin Import Job — entry point.

Workflow:
1. Load configuration (DATABASE_TYPE switch, paths, toggles).
2. (Optional) Download phase — refresh GarminDB SQLite via the GarminDB CLI
   (only when GARMIN_DOWNLOAD=true; best-effort, falls back to existing DBs).
3. Transform — read GarminDB SQLite into clean DataFrames (workout metadata with
   workoutId + first GPS, per-second timeseries, daily sleep).
4. Load — upsert into DuckDB or PostgreSQL based on DATABASE_TYPE.
5. Print a summary.

Configuration is loaded from environment variables (via a .env file).
"""
import sys
from pathlib import Path

# Make the job directory importable so `garmin_etl` resolves regardless of cwd.
job_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(job_dir))

from garmin_etl.config import load_garmin_configuration
from garmin_etl import transform as transform_layer


def main() -> None:
    print("=" * 80)
    print("GARMIN IMPORT JOB — Heart Rate (workouts) + Sleep")
    print("=" * 80)
    print()

    # ── Step 1: Configuration ───────────────────────────────────────────────
    print("Step 1: Loading configuration...")
    try:
        config = load_garmin_configuration()
    except (ValueError, FileNotFoundError) as exc:
        print(f"❌ ERROR: Configuration failed: {exc}")
        sys.exit(1)
    print()

    db_type = config["DATABASE_TYPE"]
    if db_type == "postgres":
        from garmin_etl.storage import postgres as storage
    else:
        from garmin_etl.storage import duckdb as storage

    # ── Step 2: Optional download ───────────────────────────────────────────
    if config["GARMIN_DOWNLOAD"]:
        print("Step 2: Downloading latest data from Garmin (GarminDB CLI)...")
        print("-" * 60)
        download_required = config.get("GARMIN_DOWNLOAD_REQUIRED", True)
        try:
            from garmin_etl import download as download_layer

            ok = download_layer.run_download(config)
            if not ok:
                if download_required:
                    print(
                        "❌ ERROR: Download phase failed and GARMIN_DOWNLOAD_REQUIRED is "
                        "true. Aborting so stale data is not mistaken for a fresh import."
                    )
                    sys.exit(1)
                print("⚠️  Download phase failed; continuing with existing SQLite DBs.")
        except (FileNotFoundError, RuntimeError) as exc:
            print(f"❌ ERROR: {exc}")
            sys.exit(1)
        except Exception as exc:  # noqa: BLE001 - best-effort phase
            if download_required:
                print(f"❌ ERROR: Download phase error: {exc}")
                sys.exit(1)
            print(f"⚠️  Download phase error: {exc}")
            print("   Continuing with existing SQLite DBs.")
        print()
    else:
        print("Step 2: Download phase disabled (transform-only). Skipping.")
        print()

    # ── Step 3: Transform ───────────────────────────────────────────────────
    print("Step 3: Transforming GarminDB SQLite data...")
    print("-" * 60)
    try:
        frames = transform_layer.transform_all(config)
    except (FileNotFoundError, ValueError) as exc:
        print(f"❌ ERROR: Transform failed: {exc}")
        sys.exit(1)
    workouts_df = frames["workouts"]
    timeseries_df = frames["timeseries"]
    sleep_df = frames["sleep"]
    print()

    # ── Step 4: Load ────────────────────────────────────────────────────────
    print(f"Step 4: Loading into {db_type.upper()}...")
    print("-" * 60)
    try:
        workout_counts = storage.import_workouts(workouts_df, timeseries_df, config)
        sleep_counts = storage.import_sleep(sleep_df, config)
    except Exception as exc:  # noqa: BLE001
        print(f"❌ ERROR: Database load failed: {exc}")
        sys.exit(1)
    print()

    # ── Done ────────────────────────────────────────────────────────────────
    print("=" * 80)
    print("✅ GARMIN IMPORT JOB COMPLETE")
    print("=" * 80)
    print(f"  - Database type:        {db_type.upper()}")
    print(f"  - Workouts imported:    {len(workouts_df)} (table total {workout_counts['workouts']})")
    print(f"  - Timeseries rows:      {len(timeseries_df)} (table total {workout_counts['timeseries']})")
    print(f"  - Sleep nights:         {len(sleep_df)} (table total {sleep_counts['sleep']})")
    if db_type == "duckdb":
        print(f"  - DuckDB file:          {config['GARMIN_DUCKDB_PATH']}")
    print("=" * 80)


if __name__ == "__main__":
    main()
