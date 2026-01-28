"""Raw Import Job for Polar AccessLink Workouts.

This script downloads TCX files from Polar API and converts them to CSV format.
It checks Azure Blob Storage to avoid re-downloading files that already exist.

Workflow:
1. List all exercises from Polar API
2. Check Azure Storage for existing CSV files (by workoutId)
3. Download only TCX files that are NOT already in Azure Storage
4. Convert TCX to Polar-compatible CSV format
5. Upload both TCX and CSV files to Azure Storage

Configuration is loaded from environment variables via .env file.
OAuth tokens are stored in tokens_polar.json.
"""
import sys
from pathlib import Path
from typing import Dict, List

# Add repository root to path
# __file__ is jobs/raw-import/main.py
# parent = jobs/raw-import
# parent.parent = jobs
# parent.parent.parent = repo root
repo_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(repo_root))

from polar.utils.config import load_configuration
from polar.api.users import register_user, get_user_info, get_physical_info
from polar.api.exercises import (
    list_exercises,
    display_exercises,
    download_tcx_and_convert_to_csv,
    generate_workout_id_from_start_time,
)
from polar.utils.common import get_field
from polar.cloud.azure import (
    is_azure_storage_enabled,
    list_azure_storage_blobs,
    upload_file_to_azure_storage,
)


def filter_exercises_not_in_azure(
    exercises: List[Dict[str, object]]
) -> List[Dict[str, object]]:
    """Filter exercises to only include those not already in Azure Storage.
    
    Args:
        exercises: List of exercise dictionaries from Polar API
    
    Returns:
        List of exercises that don't have corresponding CSV files in Azure Storage
    """
    if not is_azure_storage_enabled():
        print("⚠️  Azure Storage is disabled. Cannot check for existing files.")
        print("   All exercises will be downloaded.")
        return exercises
    
    print("\n🔍 Checking Azure Storage for existing workout files...")
    
    # List all CSV files in Azure Storage
    csv_blobs = list_azure_storage_blobs(prefix="polar_csv/")
    
    # Extract workout IDs from blob names (format: polar_csv/{workoutId}.csv)
    existing_workout_ids = set()
    for blob_name in csv_blobs:
        # Extract workoutId from blob name
        if blob_name.startswith("polar_csv/") and blob_name.endswith(".csv"):
            workout_id = blob_name.replace("polar_csv/", "").replace(".csv", "")
            existing_workout_ids.add(workout_id)
    
    print(f"✅ Found {len(existing_workout_ids)} existing workouts in Azure Storage")
    
    # Filter exercises to only those not in Azure
    new_exercises = []
    for ex in exercises:
        start_time = get_field(ex, 'start_time', 'start-time', 'local_start_time')
        if start_time:
            workout_id = generate_workout_id_from_start_time(start_time)
            if workout_id not in existing_workout_ids:
                new_exercises.append(ex)
            else:
                exercise_id = get_field(ex, 'id', 'exercise_id')
                print(f"  ⏭ Skipping exercise {exercise_id} (workoutId {workout_id} already in Azure)")
    
    print(f"\n✅ Found {len(new_exercises)} new exercise(s) to download (out of {len(exercises)} total)")
    return new_exercises


def main():
    """Execute raw import workflow: Download TCX, convert to CSV, upload to Azure."""
    print("=" * 80)
    print("RAW IMPORT JOB - Polar AccessLink TCX Download & Conversion")
    print("=" * 80)
    print()
    
    # Step 1: Load configuration (includes token validation)
    print("Step 1: Loading configuration...")
    try:
        config = load_configuration()
    except (ValueError, FileNotFoundError) as e:
        print(f"❌ ERROR: Configuration failed: {e}")
        sys.exit(1)
    
    output_dir = config['OUTPUT_DIR']
    access_token = config['ACCESS_TOKEN']
    print()
    
    # Verify Azure Storage is enabled
    if not is_azure_storage_enabled():
        print("❌ ERROR: Azure Storage is NOT enabled!")
        print("   This job requires Azure Storage to check for existing files.")
        print("   Please set AZURE_STORAGE_ENABLED=true and configure storage account.")
        return
    
    # Step 2: Register user
    print("Step 2: Registering user with Polar API...")
    polar_user_id = register_user(
        access_token=access_token,
        config=config,
        member_id=config['MEMBER_ID']
    )
    print()
    
    # Step 3: List exercises from Polar API
    print("Step 3: Listing exercises from Polar API...")
    exercises = list_exercises(
        access_token=access_token,
        api_base=config['API_BASE']
    )
    
    if not exercises:
        print("⚠️  No exercises available from Polar API.")
        print("=" * 80)
        return
    
    # Display all exercises
    display_exercises(exercises)
    
    # Step 4: Filter exercises - only download those NOT in Azure Storage
    print("\nStep 4: Filtering exercises based on Azure Storage...")
    new_exercises = filter_exercises_not_in_azure(exercises)
    
    if not new_exercises:
        print("\n⚠️  All exercises are already in Azure Storage. Nothing new to download.")
        print("=" * 80)
        return
    
    # Step 5: Fetch user info for CSV conversion
    print("\nStep 5: Fetching user info for CSV conversion parameters...")
    user_info = get_user_info(polar_user_id, access_token, config=config)
    
    # Extract user name with default
    name = "Anton Antonov "  # Default
    if user_info and 'first-name' in user_info and 'last-name' in user_info:
        name = f"{user_info.get('first-name', '')} {user_info.get('last-name', '')} "
        print(f"✅ User name: {name.strip()}")
    
    # Get physical information
    physical_info = get_physical_info(polar_user_id, access_token, config=config)
    
    # Extract parameters from physical_info
    weight = physical_info.get('weight', 0.0)
    height = physical_info.get('height', 0.0)
    hr_max = physical_info.get('maximum-heart-rate', 0)
    hr_sit = physical_info.get('resting-heart-rate', 0)
    vo2max = physical_info.get('vo2-max', 0)
    
    # Step 6: Download TCX files and convert to CSV
    print("\nStep 6: Downloading TCX files and converting to CSV...")
    print("=" * 80)
    
    downloaded_count = 0
    uploaded_count = 0
    
    for i, exercise in enumerate(new_exercises, 1):
        exercise_id = get_field(exercise, 'id', 'exercise_id')
        start_time = get_field(exercise, 'start_time', 'start-time', 'local_start_time')
        
        print(f"\n--- Processing exercise {i}/{len(new_exercises)} ---")
        print(f"Exercise ID: {exercise_id}")
        print(f"Start Time: {start_time}")
        
        # Download TCX and convert to CSV
        result = download_tcx_and_convert_to_csv(
            exercise_id=exercise_id,
            access_token=access_token,
            output_dir=output_dir,
            name=name,
            height=height,
            weight=weight,
            hr_max=hr_max,
            hr_sit=hr_sit,
            vo2max=vo2max,
            api_base=config['API_BASE'],
            start_time=start_time
        )
        
        if result is not None:
            csv_path, tcx_path = result
            downloaded_count += 1
            
            # Generate workout ID for Azure blob names
            workout_id = generate_workout_id_from_start_time(start_time)
            
            # Upload CSV to Azure Storage
            try:
                csv_blob_name = f"polar_csv/{workout_id}.csv"
                csv_blob_url = upload_file_to_azure_storage(csv_path, blob_name=csv_blob_name)
                if csv_blob_url:
                    uploaded_count += 1
                    print(f"✅ CSV uploaded to Azure: {csv_blob_name}")
            except Exception as e:
                print(f"❌ Failed to upload CSV to Azure: {e}")
            
            # Upload TCX to Azure Storage
            try:
                tcx_blob_name = f"polar_tcx/{workout_id}.tcx"
                tcx_blob_url = upload_file_to_azure_storage(tcx_path, blob_name=tcx_blob_name)
                if tcx_blob_url:
                    uploaded_count += 1
                    print(f"✅ TCX uploaded to Azure: {tcx_blob_name}")
            except Exception as e:
                print(f"❌ Failed to upload TCX to Azure: {e}")
    
    # Step 8: Display summary
    print()
    print("=" * 80)
    print("RAW IMPORT JOB COMPLETE")
    print("=" * 80)
    print(f"  - Total exercises available: {len(exercises)}")
    print(f"  - New exercises downloaded: {downloaded_count}")
    print(f"  - Files uploaded to Azure: {uploaded_count} (TCX + CSV)")
    print("=" * 80)


if __name__ == "__main__":
    main()
