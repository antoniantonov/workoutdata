"""Automated Polar AccessLink workout import job.

This script executes the complete Polar AccessLink workflow to:
1. Download new exercises from Polar API
2. Convert TCX to Polar-compatible CSV format
3. Import CSVs into DuckDB database

Configuration is loaded from environment variables via .env file.
OAuth tokens are stored in tokens_polar.json.
"""
import sys
from pathlib import Path

# Add repository root to path
# __file__ is jobs/import/main.py
# parent = jobs/import
# parent.parent = jobs
# parent.parent.parent = repo root
repo_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(repo_root))

from polar.workflow import run_polar_workflow, get_field
from polar.ingest import workouts as import_tools


def main():
    """Execute the complete Polar AccessLink import workflow."""
    print("=" * 60)
    print("Polar AccessLink Workout Import Job")
    print("=" * 60)
    
    # OAuth token storage file (in same directory as this script)
    tokens_file = Path(__file__).parent / "tokens_polar.json"
    
    # Execute the complete workflow
    # This function handles:
    # - Configuration loading from environment variables
    # - OAuth token validation and authorization (if needed)
    # - User registration with Polar API
    # - User info retrieval (weight, height, HR max for CSV conversion)
    # - Exercise listing
    # - Download ALL new exercises (not already in database)
    # - TCX to CSV conversion with proper metadata
    result = run_polar_workflow(
        tokens_file=tokens_file,
        timeout=300  # 5 minute timeout for authorization flow
    )
    
    # Access the workflow results
    config = result['config']
    polar_user_id = result['polar_user_id']
    access_token = result['access_token']
    exercises = result['exercises']
    new_exercises = result['new_exercises']
    tcx_dataframes = result['tcx_dataframes']
    
    # Display summary
    print(f"\n✅ Workflow completed successfully!")
    print(f"  - Polar User ID: {polar_user_id}")
    print(f"  - Total exercises available: {len(exercises)}")
    print(f"  - New exercises downloaded: {len(new_exercises)}")
    
    if new_exercises:
        print(f"\n  New exercises:")
        for ex in new_exercises:
            exercise_id = get_field(ex, 'id', 'exercise_id')
            start_time = get_field(ex, 'start_time', 'start-time', 'local_start_time')
            print(f"    - {exercise_id} ({start_time})")
    
    if tcx_dataframes:
        total_rows = sum(len(df) for df in tcx_dataframes)
        print(f"\n  - Total time-series rows: {total_rows}")
        print(f"\nNext step: Importing CSVs to DuckDB...")
        print(f"\n" + "-" * 60 + "\n")
        
        # Import all Anton_Antonov*.CSV files (includes TCX conversions)
        glob_patterns = ["Anton_Antonov*.CSV"]
        summary = import_tools.import_workout_from_directory(glob_patterns)
        
        print(f"\n" + "=" * 60)
        print("Import Summary")
        print("=" * 60)
        print(f"  - Total files found: {summary.get('total', 0)}")
        print(f"  - Successfully imported: {summary.get('processed', 0)}")
        print(f"  - Skipped (already exists): {summary.get('skipped', 0)}")
        print(f"  - Errors: {summary.get('errors', 0)}")
        print("=" * 60)
    else:
        print("\nNo new workouts to import.")
        print("=" * 60)


if __name__ == "__main__":
    main()
