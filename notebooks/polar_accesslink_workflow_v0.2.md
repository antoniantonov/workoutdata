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

# Polar AccessLink OAuth Workflow (Version 2)

## Overview
This notebook provides a streamlined, single-function approach to download workout data from Polar AccessLink API. All the complex OAuth and API logic has been extracted into the `workflow_tools.py` module.

## What This Notebook Does
Executes the complete Polar AccessLink workflow in one function call:
1. **OAuth Authorization**: Handles token management (loads existing or runs OAuth flow)
2. **User Registration**: Registers user with Polar API (idempotent operation)
3. **Exercise Discovery**: Lists all available exercises from your Polar account
4. **Data Download**: Downloads ALL new exercises (not already in database) as TCX and converts to Polar-compatible CSV format

## Implementation Details

### Module: `workflow_tools.py`
All functionality is implemented in [`workflow_tools.py`](workflow_tools.py), which contains:

- **OAuth Functions**: Token management, authorization code exchange, refresh tokens
- **User Management**: User registration and info retrieval from Polar API
- **Exercise Management**: List exercises, filter new exercises, download TCX data
- **TCX Conversion**: Convert TCX files to Polar-compatible CSV format with proper metadata
- **Complete Workflow**: `run_polar_workflow()` orchestrates all steps automatically

### How It Works
1. **Configuration**: Loads credentials from environment variables (`.env` file or system env)
2. **Token Check**: Validates existing tokens in `tokens_polar.json` or initiates OAuth flow
3. **Authorization** (if needed): Starts local callback server, opens browser for user consent, captures authorization code
4. **Token Exchange**: Exchanges authorization code for access and refresh tokens
5. **User Registration**: Registers with Polar AccessLink API (returns existing user if already registered)
6. **Fetch User Info**: Retrieves user profile data (name, weight, height, HR max, VO2max) for CSV conversion
7. **List Exercises**: Queries Polar API for all available exercises
8. **Filter New Exercises**: Identifies exercises not already in the database
9. **Download All New**: Downloads all new exercises as TCX files
10. **Convert to CSV**: Transforms TCX to Polar CSV format (metadata + time-series) using user parameters
11. **Save & Return**: Saves CSVs to `hr_data/` directory and returns parsed DataFrames

### CSV Format
The converted CSV matches Polar's export format:
- **Row 1**: Column headers (28 metadata columns)
- **Row 2**: Metadata values (date, sport, duration, calories, HR stats, user params, notes)
- **Row 3+**: Time-series data (Sample rate, Time, HR, Speed, Pace, etc.)

This format is compatible with `import_tools.py` for direct import into DuckDB.

## Security Warnings ⚠️
- **NEVER hard-code secrets** in notebooks or commit them to version control
- **Use environment variables** for sensitive credentials (`.env` file recommended)
- **Rotate any exposed secrets** immediately if accidentally committed
- The `tokens_polar.json` file is gitignored - do NOT commit it

## Setup Requirements

### Environment Variables
Create a `.env` file in the project root (gitignored):

```
POLAR_CLIENT_ID=your_client_id_here
POLAR_CLIENT_SECRET=your_client_secret_here
POLAR_REDIRECT_PORT=5000
POLAR_MEMBER_ID=your_member_id_here
```

### Polar Developer Account
1. Register your application at https://admin.polaraccesslink.com/
2. Configure redirect URI: `http://localhost:5000/callback` (or your chosen port)
3. Note your Client ID and Client Secret

## Related Files
- [`workflow_tools.py`](workflow_tools.py) - All OAuth and API logic
- [`import_tools.py`](import_tools.py) - DuckDB ingestion utilities
- [`populate_duckdb.md`](populate_duckdb.md) - Batch import CSVs to database

+++

## Complete Workflow Execution

This single cell runs the entire Polar AccessLink workflow using `run_polar_workflow()` from `workflow_tools.py`.

```{code-cell} ipython3
import sys
from pathlib import Path
from datetime import datetime, timezone

# Add src to path
repo_root = Path.cwd().parent if 'notebooks' in str(Path.cwd()) else Path.cwd()
sys.path.insert(0, str(repo_root))

import importlib
from polar import workflow
from polar.storage import duckdb
from polar.storage import postgres
from polar.ingest import workouts as import_tools
importlib.reload(workflow)
importlib.reload(duckdb)
importlib.reload(postgres)
importlib.reload(import_tools)

from polar.workflow import run_polar_workflow
from polar.utils.config import load_configuration

# Load configuration
config = load_configuration()

# Execute the complete workflow
# This function handles:
# - OAuth token validation and authorization (if needed)
# - User registration with Polar API
# - User info retrieval (weight, height, HR max for CSV conversion)
# - Exercise listing
# - Download ALL new exercises (not already in database)
# - TCX to CSV conversion with proper metadata
result = run_polar_workflow(
    config=config,
    timeout=300  # 5 minute timeout for authorization flow
)

# Access the workflow results
polar_user_id = result['polar_user_id']    # Polar user ID
access_token = result['access_token']      # OAuth access token
exercises = result['exercises']            # List of all exercises
new_exercises = result['new_exercises']    # Exercises that were newly downloaded
downloaded_tcx_files = result['downloaded_tcx_files']  # List of parsed time-series DataFrames
processed_csv_files = result['processed_csv_files']  # List of processed CSV file paths

# Display summary
print(f"\n✅ Workflow completed successfully!")
print(f"  - Polar User ID: {polar_user_id}")
print(f"  - Total exercises available: {len(exercises)}")
print(f"  - New exercises available for download: {len(new_exercises)}")

if new_exercises:
    from polar.workflow import get_field
    print(f"\n  New exercises:")
    for ex in new_exercises:
        exercise_id = get_field(ex, 'id', 'exercise_id')
        start_time = get_field(ex, 'start_time', 'start-time', 'local_start_time')
        print(f"    - {exercise_id} ({start_time})")

# Check file processing status
num_downloaded_tcx = len(downloaded_tcx_files) if downloaded_tcx_files else 0
num_processed_csv = len(processed_csv_files) if processed_csv_files else 0
num_new_exercises = len(new_exercises) if new_exercises else 0

if num_downloaded_tcx > 0 or num_processed_csv > 0:
    print(f"\n📊 File Processing Summary:")
    if num_downloaded_tcx > 0:
        print(f"  - Downloaded TCX files: {num_downloaded_tcx}")
    if num_processed_csv > 0:
        print(f"  - Processed CSV files: {num_processed_csv}")
    
    # Warning: Mismatch between downloaded and processed files
    if num_downloaded_tcx != num_processed_csv:
        print(f"\n⚠️  WARNING: Mismatch detected!")
        print(f"    Downloaded TCX files ({num_downloaded_tcx}) != Processed CSV files ({num_processed_csv})")
        print(f"    Some downloaded files were probably not processed correctly.")
    
    # Warning: Mismatch between new exercises and downloaded files
    if num_downloaded_tcx != num_new_exercises:
        print(f"\n⚠️  WARNING: Download issue detected!")
        print(f"    New exercises ({num_new_exercises}) != Downloaded TCX files ({num_downloaded_tcx})")
        print(f"    Some workouts were probably not found or unable to be downloaded.")

if processed_csv_files:
    print(f"\nNext step: Importing CSVs to database...")
    print(f"\n------------------------------------------------------\n")

    glob_patterns = ["Anton_Antonov*.CSV"]
    
    # Get database type from configuration
    db_type = config.get('DATABASE_TYPE', 'duckdb')
    
    print(f"📊 Using {db_type.upper()} database for import")
    print(f"------------------------------------------------------\n")
    
    # Import to the appropriate database based on configuration
    if db_type == 'postgres':
        summary = postgres.import_workout_from_directory(glob_patterns, config)
    else:  # default to duckdb
        summary = duckdb.import_workout_from_directory(glob_patterns, config)
    
    print(summary)
    
    # Upload database to Azure (DuckDB only)
    if db_type == 'duckdb':
        duckdb.upload_database_to_azure(config)
    
    # Cleanup: Delete successfully processed files
    print(f"\n------------------------------------------------------")
    print("Cleanup: Deleting processed files...")
    print(f"------------------------------------------------------\n")
    
    deletion_patterns = ["*.CSV", "*.tcx"]
    deletion_summary = import_tools.delete_files_from_directory(deletion_patterns, config)
    deletion_summary
else:
    print("No new TCX files were downloaded to import.")
```
