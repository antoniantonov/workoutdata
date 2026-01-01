"""Exercise management for Polar AccessLink API.

This module provides utilities for managing exercises including:
- Listing exercises from Polar API
- Displaying exercises in formatted output
- Downloading and converting exercise TCX data
- Normalizing exercise timestamps
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import duckdb  # type: ignore
import pandas as pd  # type: ignore
import requests  # type: ignore

from common_tools import get_field
from config import load_configuration
from converters import convert_tcx_to_csv
from postgresdb_import import get_postgres_connection


# =============================================================================
# Workout ID Helpers
# =============================================================================

def generate_workout_id_from_start_time(start_time_str: str) -> str:
    """Generate workoutId from exercise start time string.
    
    Converts start time from Polar API format to workoutId format.
    
    Args:
        start_time_str: Start time string from Polar API (e.g., "2025-05-11T10:59:46.000")
    
    Returns:
        WorkoutId in format "DD-MM-YYYY_HHMMSS" (e.g., "11-05-2025_105946")
    """
    # Handle various formats from Polar API
    # Remove timezone info if present
    clean_time = start_time_str.replace('Z', '').replace('+00:00', '')
    
    # Try parsing with milliseconds first, then without
    for fmt in ['%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S']:
        try:
            dt = datetime.fromisoformat(clean_time)
            break
        except ValueError:
            continue
    else:
        # Fallback: try direct parsing
        dt = datetime.fromisoformat(clean_time)
    
    # Format: DD-MM-YYYY_HHMMSS
    return dt.strftime('%d-%m-%Y_%H%M%S')


def get_existing_workout_ids(db_path: Optional[Path] = None) -> set:
    """Get set of existing workout IDs from the database.
    
    Supports both DuckDB and PostgreSQL based on DATABASE_TYPE configuration.
    
    Args:
        db_path: Path to DuckDB database file (optional, loads from config if None).
                 Ignored when using PostgreSQL.
    
    Returns:
        Set of workout IDs that already exist in workout_metadata table
    """
    existing_ids = set()
    
    try:
        config = load_configuration()
        db_type = config.get('DATABASE_TYPE', 'duckdb')
        
        if db_type == 'postgres':
            # PostgreSQL connection
            conn = get_postgres_connection()
            try:
                with conn.cursor() as cur:
                    # Check if table exists
                    cur.execute("""
                        SELECT EXISTS (
                            SELECT FROM information_schema.tables 
                            WHERE table_name = 'workout_metadata'
                        )
                    """)
                    table_exists = cur.fetchone()[0]
                    
                    if table_exists:
                        # Get all existing workout IDs
                        cur.execute('SELECT "workoutId" FROM workout_metadata')
                        rows = cur.fetchall()
                        existing_ids = {row[0] for row in rows}
                        print(f"✅ Found {len(existing_ids)} existing workouts in PostgreSQL database")
                    else:
                        print("⚠️ workout_metadata table does not exist yet in PostgreSQL")
            finally:
                conn.close()
        
        else:  # Default to DuckDB
            if db_path is None:
                db_path = config['DUCKDB_PATH']
            
            con = duckdb.connect(str(db_path))
            try:
                # Check if table exists
                result = con.execute("""
                    SELECT table_name FROM information_schema.tables 
                    WHERE table_name = 'workout_metadata'
                """).fetchone()
                
                if result:
                    # Get all existing workout IDs
                    rows = con.execute("SELECT workoutId FROM workout_metadata").fetchall()
                    existing_ids = {row[0] for row in rows}
                    print(f"✅ Found {len(existing_ids)} existing workouts in DuckDB database")
                else:
                    print("⚠️ workout_metadata table does not exist yet in DuckDB")
            finally:
                con.close()
    
    except Exception as e:
        print(f"⚠️  Error checking existing workouts: {e}")
    
    return existing_ids


# =============================================================================
# Exercise Management Functions
# =============================================================================

def normalize_start_time(exercise: Dict[str, object]) -> datetime:
    """Normalize exercise start time to datetime object.
    
    Handles various timestamp formats and field names from Polar API.
    
    Args:
        exercise: Exercise dictionary
    
    Returns:
        datetime object, or empty string if parsing fails
    """
    raw = get_field(exercise, 'start_time', 'start-time', 'local_start_time', 'local-start-time')
    if not raw:
        return ''
    # Handle potential trailing Z
    raw_norm = raw.replace('Z', '+00:00') if raw.endswith('Z') else raw
    try:
        return datetime.fromisoformat(raw_norm)
    except ValueError:
        return raw


def list_exercises(
    access_token: str,
    api_base: str = "https://www.polaraccesslink.com/v3"
) -> List[Dict[str, object]]:
    """List user exercises using Polar AccessLink API.
    
    Args:
        access_token: OAuth access token
        api_base: Polar API base URL
    
    Returns:
        List of exercise dictionaries
    
    Raises:
        Exception: If exercise listing fails
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json"
    }
    
    print("Listing exercises via /users/{user}/exercises API...\n")
    exercises_url = f"{api_base}/exercises"
    resp = requests.get(exercises_url, headers=headers)

    exercises = []
    if resp.status_code == 200:
        body = resp.json()
        if isinstance(body, list):
            exercises = body
        elif isinstance(body, dict):
            exercises = body.get('exercises', [])
        else:
            print(f"⚠️  Unexpected exercises payload type: {type(body).__name__}")
        print(f"✅ Retrieved {len(exercises)} exercise(s)")
    elif resp.status_code == 204:
        print("⚠️ No exercises available (204 No Content)")
    else:
        print(f"❌ Failed to list exercises: {resp.status_code}")
        print(f"   Response: {resp.text}")
        raise Exception("Exercise listing failed")
    
    return exercises


def display_exercises(exercises: List[Dict[str, object]]) -> None:
    """Display exercises in a formatted table.
    
    Args:
        exercises: List of exercise dictionaries
    """
    if not exercises:
        print("⚠️ No exercises to display.")
        return
    
    print("\n" + "="*80)
    print("AVAILABLE EXERCISES (new API)")
    print("="*80)

    for i, ex in enumerate(exercises):
        print(f"\n{i+1}. Exercise ID: {get_field(ex, 'id', 'exercise_id')}")
        print(f"   Start Time: {get_field(ex, 'start_time', 'start-time', 'local_start_time')}")
        print(f"   Duration: {ex.get('duration', 'Unknown')}")
        print(f"   Sport: {get_field(ex, 'sport', 'detailed_sport_info')}")
    print("\n" + "="*80)


def download_tcx_and_convert_to_csv(
    exercise_id: str,
    access_token: str,
    output_dir: Path,
    name: str,
    height: float,
    weight: float,
    hr_max: int,
    hr_sit: int,
    vo2max: int,
    api_base: str = "https://www.polaraccesslink.com/v3",
    start_time: Optional[str] = None
) -> Optional[tuple[Path, Path]]:
    """Download and parse TCX data for an exercise.
    
    Uses convert_tcx_to_csv to convert TCX to Polar-compatible CSV format.
    User info parameters must be provided (fetched once before calling this function).
    
    Args:
        exercise_id: Exercise ID to download
        access_token: OAuth access token
        output_dir: Directory to save CSV output
        name: User name for CSV metadata
        height: User height in cm
        weight: User weight in kg
        hr_max: Maximum heart rate
        hr_sit: Resting heart rate
        vo2max: VO2max value
        api_base: Polar API base URL
        start_time: Start time from exercise listing API (local time).
                   If provided, this is used for workoutId and CSV metadata
                   instead of the UTC time in the TCX file.
                   Format: "2025-05-11T10:59:46.000" (ISO format)
    
    Returns:
        Tuple of (csv_path, tcx_path) with paths to created files, or None if download fails
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json"
    }
    
    # Parse start_time to generate date/time strings for CSV metadata and filename
    override_date_str = None
    override_time_str = None
    workout_id = None
    csv_filename = f"polar_latest_exercise_{exercise_id}.csv"  # fallback name
    
    if start_time:
        try:
            from datetime import datetime as dt
            # Parse the start time from exercise listing (local time)
            # Format: "2025-05-11T10:59:46.000" or "2025-05-11T10:59:46"
            start_time_clean = start_time.replace('Z', '') if start_time.endswith('Z') else start_time
            if '.' in start_time_clean:
                parsed_dt = dt.fromisoformat(start_time_clean.split('+')[0])
            else:
                parsed_dt = dt.fromisoformat(start_time_clean.split('+')[0])
            
            # Generate DD-MM-YYYY and HH:MM:SS for CSV metadata
            override_date_str = parsed_dt.strftime('%d-%m-%Y')
            override_time_str = parsed_dt.strftime('%H:%M:%S')
            
            # Generate workoutId: DD-MM-YYYY_HHMMSS
            workout_id = f"{parsed_dt.strftime('%d-%m-%Y')}_{parsed_dt.strftime('%H%M%S')}"
            
            # Generate filename: Anton_Antonov_yyyy-mm-dd_HHMMSS_tcx_convert.CSV
            csv_filename = f"Anton_Antonov_{parsed_dt.strftime('%Y-%m-%d')}_{parsed_dt.strftime('%H%M%S')}_tcx_convert.CSV"
            print(f"✅ Using start time from exercise listing: {start_time}")
        except Exception as e:
            print(f"⚠️  Could not parse start_time '{start_time}': {e}")
            print("  Falling back to TCX file timestamps")
    
    # Fetch TCX for exercise
    print("\nDownloading TCX for exercise...")
    tcx_url = f"{api_base}/exercises/{exercise_id}/tcx"
    tcx_headers = {**headers, "Accept": "application/vnd.garmin.tcx+xml"}
    tcx_resp = requests.get(tcx_url, headers=tcx_headers)

    if tcx_resp.status_code != 200:
        print(f"❌ Failed to fetch TCX for exercise {exercise_id}: {tcx_resp.status_code}")
        snippet = tcx_resp.text[:500] if hasattr(tcx_resp, 'text') else b""
        print(f"   Response: {snippet}...")
        return None
    
    print("✅ TCX downloaded")
    
    # Save TCX file (kept for reference, not deleted)
    output_dir.mkdir(exist_ok=True)
    tcx_path = output_dir / f"exercise_{exercise_id}.tcx"
    
    with open(tcx_path, 'wb') as f:
        f.write(tcx_resp.content)
    print(f"✅ TCX saved: {tcx_path}")
    
    # Convert TCX to CSV using convert_tcx_to_csv
    csv_path = output_dir / csv_filename
    
    convert_tcx_to_csv(
        tcx_path=tcx_path,
        output_csv_path=csv_path,
        name=name,
        height=height,
        weight=weight,
        hr_max=hr_max,
        hr_sit=hr_sit,
        vo2max=vo2max,
        override_date_str=override_date_str,
        override_time_str=override_time_str
    )
    
    print(f"✅ CSV saved: {csv_path}")
    if workout_id:
        print(f"✅ WorkoutId: {workout_id}")
    
    # Return paths to both files
    return (csv_path, tcx_path)


def filter_new_exercises(exercises: List[Dict[str, object]], db_path: Optional[Path] = None) -> List[Dict[str, object]]:
    """Filter exercises to only include those not already in the database.
    
    Supports both DuckDB and PostgreSQL based on DATABASE_TYPE configuration.
    
    Args:
        exercises: List of exercise dictionaries from Polar API
        db_path: Path to DuckDB database file (optional, loads from config if None).
                 Ignored when using PostgreSQL.
    
    Returns:
        List of exercises that don't have a corresponding workoutId in the database
    """
    existing_ids = get_existing_workout_ids(db_path)
    
    new_exercises = []
    for ex in exercises:
        start_time = get_field(ex, 'start_time', 'start-time', 'local_start_time')
        if start_time:
            workout_id = generate_workout_id_from_start_time(start_time)
            if workout_id not in existing_ids:
                new_exercises.append(ex)
            else:
                exercise_id = get_field(ex, 'id', 'exercise_id')
                print(f"  ⏭ Skipping exercise {exercise_id} (workoutId {workout_id} already exists)")
    
    print(f"\n✅ Found {len(new_exercises)} new exercise(s) to import (out of {len(exercises)} total)")
    return new_exercises


__all__ = [
    # Workout ID helpers
    'generate_workout_id_from_start_time',
    'get_existing_workout_ids',
    
    # Exercise management
    'normalize_start_time',
    'list_exercises',
    'display_exercises',
    'download_tcx_and_convert_to_csv',
    'filter_new_exercises',
]
