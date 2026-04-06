---
jupytext:
  text_representation:
    extension: .md
    format_name: myst
    format_version: 0.13
    jupytext_version: 1.16.6
kernelspec:
  display_name: Python 3
  language: python
  name: python3
---

```{code-cell} ipython3
import sys
from pathlib import Path

# Add src to path
repo_root = Path.cwd().parent if 'notebooks' in str(Path.cwd()) else Path.cwd()
sys.path.insert(0, str(repo_root))

# Load environment variables from .env file
from dotenv import load_dotenv
import os

# Load .env from notebooks directory (where this notebook is located)
notebook_dir = Path.cwd() if 'notebooks' in str(Path.cwd()) else Path.cwd() / 'notebooks'
env_file = notebook_dir / '.env'

if env_file.exists():
    load_dotenv(env_file)
    print(f"✅ Loaded environment variables from: {env_file}")
else:
    print(f"⚠️ No .env file found at: {env_file}")
    print("Please ensure AZURE_STORAGE_ENABLED and AZURE_STORAGE_ACCOUNT_NAME are set")

# Check TEST_RUN mode
TEST_RUN = os.getenv('TEST_RUN', 'false').lower() == 'true'
if TEST_RUN:
    print("🧪 TEST_RUN mode is ENABLED - will process only 10 CSV and 10 TCX files")
    print("   Files will be uploaded to polar_csv_test/ and polar_tcx_test/ folders")
else:
    print("🚀 Production mode - will process ALL files")
    print("   Files will be uploaded to polar_csv/ and polar_tcx/ folders")

# Check overwrite mode
OVERWRITE_EXISTING_BLOBS = os.getenv('OVERWRITE_EXISTING_BLOBS', 'false').lower() == 'true'
if OVERWRITE_EXISTING_BLOBS:
    print("⚠️  OVERWRITE mode is ENABLED - existing blobs will be replaced")
else:
    print("🛡️  OVERWRITE mode is DISABLED - upload will fail if blob exists")

print()

# Import required modules from polar
import importlib
import azure_storage
from azure_storage import (
    upload_file_to_azure_storage,
    is_azure_storage_enabled,
    get_azure_storage_config
)
importlib.reload(azure_storage)

import csv
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import Optional, Tuple, List


# =============================================================================
# Helper Functions for Workout ID Generation
# =============================================================================

def extract_workout_id_from_csv(csv_path: Path) -> Optional[str]:
    """Extract workoutId from CSV file metadata.
    
    Reads the first two rows (metadata) and extracts Date and Start time fields
    to generate workoutId in format: DD-MM-YYYY_HHMMSS
    
    Args:
        csv_path: Path to CSV file
        
    Returns:
        Workout ID string or None if extraction fails
    """
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            
            # Row 1: Headers
            headers = next(reader)
            
            # Row 2: Values
            values = next(reader)
            
            # Find Date and Start time columns
            date_idx = headers.index('Date')
            time_idx = headers.index('Start time')
            
            date_str = values[date_idx]  # DD-MM-YYYY
            time_str = values[time_idx]  # HH:MM:SS
            
            # Parse date and time
            # Date format: DD-MM-YYYY
            # Time format: HH:MM:SS
            # Remove colons from time for workoutId: HHMMSS
            time_compact = time_str.replace(':', '')
            
            # Format: DD-MM-YYYY_HHMMSS
            day, month, year = date_str.split('-')
            workout_id = f"{day}-{month}-{year}_{time_compact}"
            
            return workout_id
            
    except Exception as e:
        print(f"❌ Error extracting workout ID from {csv_path.name}: {e}")
        return None


def extract_workout_id_from_tcx(tcx_path: Path) -> Optional[str]:
    """Extract workoutId from TCX file.
    
    Parses XML to extract the <Id> field which contains UTC datetime,
    converts to local machine timezone, and generates workoutId in format: DD-MM-YYYY_HHMMSS
    
    Args:
        tcx_path: Path to TCX file
        
    Returns:
        Workout ID string or None if extraction fails
    """
    try:
        tree = ET.parse(tcx_path)
        root = tree.getroot()
        
        # Define namespace
        ns = {'tcx': 'http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2'}
        
        # Extract <Id> field which is in UTC
        # Example: <Id>2025-10-19T00:47:34.000Z</Id>
        id_elem = root.find('.//tcx:Activity/tcx:Id', ns)
        
        if id_elem is None or not id_elem.text:
            print(f"❌ No <Id> element found in {tcx_path.name}")
            return None
        
        id_str = id_elem.text  # e.g., "2025-10-19T00:47:34.000Z"
        
        # Check if the ID is in UTC format (ends with 'Z')
        if not id_str.endswith('Z'):
            print(f"⚠️  WARNING: ID not in expected UTC format (doesn't end with 'Z'): {id_str}")
            print(f"   Using timestamp as-is without timezone conversion")
            
            # Parse without timezone conversion
            try:
                dt = datetime.fromisoformat(id_str.replace('Z', '').replace('+00:00', ''))
                workout_id = dt.strftime('%d-%m-%Y_%H%M%S')
                return workout_id
            except Exception as e:
                print(f"❌ Failed to parse non-UTC timestamp: {e}")
                return None
        
        # Parse UTC datetime
        # Remove 'Z' and parse as UTC
        utc_dt = datetime.fromisoformat(id_str.replace('Z', '+00:00'))
        
        # Convert to local machine timezone
        # Get local timezone from system
        local_tz = datetime.now().astimezone().tzinfo
        local_dt = utc_dt.astimezone(local_tz)
        
        # Calculate and display timezone offset (hours only)
        offset_hours = int(local_dt.utcoffset().total_seconds() // 3600)
        
        print(f"   UTC: {utc_dt.strftime('%Y-%m-%d %H:%M:%S')} → Local: {local_dt.strftime('%Y-%m-%d %H:%M:%S')} (UTC{offset_hours:+d})")
        
        # Format as DD-MM-YYYY_HHMMSS
        workout_id = local_dt.strftime('%d-%m-%Y_%H%M%S')
        
        return workout_id
        
    except Exception as e:
        print(f"❌ Error extracting workout ID from {tcx_path.name}: {e}")
        return None


# =============================================================================
# Main Upload Functions
# =============================================================================

def upload_csv_files(hr_data_dir: Path, test_run: bool = False, overwrite: bool = True) -> Tuple[int, int, List[str]]:
    """Upload all CSV files from hr_data directory to Azure Blob Storage.
    
    Args:
        hr_data_dir: Path to hr_data directory
        test_run: If True, upload only 10 files to polar_csv_test/ folder
        overwrite: If True, overwrite existing blobs; if False, fail if blob exists
        
    Returns:
        Tuple of (successful_uploads, failed_uploads, uploaded_blob_urls)
    """
    csv_files = sorted(hr_data_dir.glob('*.CSV'))
    
    # Limit to 10 files if in test mode
    if test_run:
        csv_files = csv_files[:10]
        folder_name = "polar_csv_test"
    else:
        folder_name = "polar_csv"
    
    print("="*80)
    print(f"UPLOADING CSV FILES TO AZURE BLOB STORAGE")
    print("="*80)
    print(f"Mode: {'TEST RUN (10 files max)' if test_run else 'PRODUCTION (all files)'}")
    print(f"Overwrite: {'ENABLED' if overwrite else 'DISABLED (will fail if blob exists)'}")
    print(f"Target folder: {folder_name}/")
    print(f"Found {len(csv_files)} CSV file(s) to upload")
    print()
    
    successful = 0
    failed = 0
    blob_urls = []
    
    for i, csv_path in enumerate(csv_files, 1):
        print(f"\n--- Processing CSV {i}/{len(csv_files)}: {csv_path.name} ---")
        
        # Extract workout ID
        workout_id = extract_workout_id_from_csv(csv_path)
        
        if not workout_id:
            print(f"⚠️ Skipping {csv_path.name} - could not extract workout ID")
            failed += 1
            continue
        
        print(f"✅ Workout ID: {workout_id}")
        
        # Upload to Azure with appropriate folder
        blob_name = f"{folder_name}/{workout_id}.csv"
        
        try:
            blob_url = upload_file_to_azure_storage(csv_path, blob_name=blob_name, overwrite=overwrite)

            if blob_url:
                successful += 1
                blob_urls.append(blob_url)
            else:
                failed += 1
                
        except Exception as e:
            print(f"❌ Upload failed: {e}")
            failed += 1
    
    print()
    print("="*80)
    print(f"CSV UPLOAD SUMMARY: {successful} successful, {failed} failed")
    print("="*80)
    
    return successful, failed, blob_urls


def upload_tcx_files(hr_data_dir: Path, test_run: bool = False, overwrite: bool = True) -> Tuple[int, int, List[str]]:
    """Upload all TCX files from hr_data directory to Azure Blob Storage.
    
    Args:
        hr_data_dir: Path to hr_data directory
        test_run: If True, upload only 10 files to polar_tcx_test/ folder
        overwrite: If True, overwrite existing blobs; if False, fail if blob exists
        
    Returns:
        Tuple of (successful_uploads, failed_uploads, uploaded_blob_urls)
    """
    tcx_files = sorted(hr_data_dir.glob('*.tcx'))
    
    # Limit to 10 files if in test mode
    if test_run:
        tcx_files = tcx_files[:10]
        folder_name = "polar_tcx_test"
    else:
        folder_name = "polar_tcx"
    
    print("="*80)
    print(f"UPLOADING TCX FILES TO AZURE BLOB STORAGE")
    print("="*80)
    print(f"Mode: {'TEST RUN (10 files max)' if test_run else 'PRODUCTION (all files)'}")
    print(f"Overwrite: {'ENABLED' if overwrite else 'DISABLED (will fail if blob exists)'}")
    print(f"Target folder: {folder_name}/")
    print(f"Found {len(tcx_files)} TCX file(s) to upload")
    print()
    
    successful = 0
    failed = 0
    blob_urls = []
    
    for i, tcx_path in enumerate(tcx_files, 1):
        print(f"\n--- Processing TCX {i}/{len(tcx_files)}: {tcx_path.name} ---")
        
        # Extract workout ID
        workout_id = extract_workout_id_from_tcx(tcx_path)
        
        if not workout_id:
            print(f"⚠️ Skipping {tcx_path.name} - could not extract workout ID")
            failed += 1
            continue
        
        print(f"✅ Workout ID: {workout_id}")
        
        # Upload to Azure with appropriate folder
        blob_name = f"{folder_name}/{workout_id}.tcx"
        
        try:
            blob_url = upload_file_to_azure_storage(tcx_path, blob_name=blob_name, overwrite=overwrite)
            
            if blob_url:
                successful += 1
                blob_urls.append(blob_url)
            else:
                failed += 1
                
        except Exception as e:
            print(f"❌ Upload failed: {e}")
            failed += 1
    
    print()
    print("="*80)
    print(f"TCX UPLOAD SUMMARY: {successful} successful, {failed} failed")
    print("="*80)
    
    return successful, failed, blob_urls


# =============================================================================
# Main Execution
# =============================================================================

def main():
    """Main execution function."""
    
    # Check if Azure Storage is enabled
    if not is_azure_storage_enabled():
        print("❌ Azure Storage is not enabled!")
        print("Please set AZURE_STORAGE_ENABLED=true in your .env file")
        print("Also ensure AZURE_STORAGE_ACCOUNT_NAME is configured")
        return
    
    # Get Azure configuration
    config = get_azure_storage_config()
    print("☁️ Azure Storage Configuration:")
    print(f"  - Storage Account: {config['account_name']}")
    print(f"  - Container: {config['container_name']}")
    print()
    
    # Set hr_data directory
    hr_data_dir = repo_root / 'hr_data'
    
    if not hr_data_dir.exists():
        print(f"❌ hr_data directory not found: {hr_data_dir}")
        return
    
    print(f"📂 Processing files from: {hr_data_dir}")
    print()
    
    # Upload CSV files (pass TEST_RUN and OVERWRITE flags)
    csv_success, csv_failed, csv_urls = upload_csv_files(
        hr_data_dir, 
        test_run=TEST_RUN,
        overwrite=OVERWRITE_EXISTING_BLOBS
    )
    
    print("\n\n")
    
    # Upload TCX files (pass TEST_RUN and OVERWRITE flags)
    tcx_success, tcx_failed, tcx_urls = upload_tcx_files(
        hr_data_dir, 
        test_run=TEST_RUN,
        overwrite=OVERWRITE_EXISTING_BLOBS
    )
    
    # Final summary
    print("\n\n")
    print("="*80)
    print("FINAL SUMMARY")
    print("="*80)
    if TEST_RUN:
        print("🧪 TEST RUN MODE - Limited to 10 files per type")
    if not OVERWRITE_EXISTING_BLOBS:
        print("🛡️  OVERWRITE DISABLED - Uploads failed if blobs already existed")
    print(f"CSV Files:  {csv_success} uploaded, {csv_failed} failed")
    print(f"TCX Files:  {tcx_success} uploaded, {tcx_failed} failed")
    print(f"Total:      {csv_success + tcx_success} uploaded, {csv_failed + tcx_failed} failed")
    print("="*80)


# Execute main function
if __name__ == "__main__":
    main()
else:
    # When running in notebook
    main()
```
