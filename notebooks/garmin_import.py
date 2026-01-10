"""Utility functions for importing Garmin FIT files into DuckDB.

This module provides helpers for:
- Parsing Garmin FIT files to extract GPS coordinates and elevation
- Importing Garmin workout data into a new DuckDB table
- Batch importing FIT files from directories

The functions extract the first GPS coordinate and elevation found in each workout
and create a table with schema:
- workoutId (VARCHAR PRIMARY KEY): Unique workout identifier
- latitude (DOUBLE): First GPS latitude in degrees
- longitude (DOUBLE): First GPS longitude in degrees  
- elevation (DOUBLE): First elevation/altitude in meters
- start_time (TIMESTAMP): Workout start time
- source_file (VARCHAR): Original FIT filename
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

import duckdb  # type: ignore

from config import load_configuration

# Configure logging
logger = logging.getLogger(__name__)


def generate_workout_id_from_datetime(dt: datetime) -> str:
    """
    Generate a workoutId from a datetime object.
    
    Uses the same format as the existing import_tools.py:
    "DD-MM-YYYY_HHMMSS" (e.g., "11-05-2025_105946")
    
    Parameters
    ----------
    dt : datetime
        The datetime to convert to workoutId format.
    
    Returns
    -------
    str
        The workoutId string in DD-MM-YYYY_HHMMSS format.
    """
    return dt.strftime("%d-%m-%Y_%H%M%S")


def parse_fit_file(fit_path: str | Path) -> dict | None:
    """
    Parse a FIT file and extract GPS coordinates and elevation.
    
    Parameters
    ----------
    fit_path : str or Path
        Path to the FIT file to parse.
    
    Returns
    -------
    dict or None
        Dictionary containing:
        - workoutId: str - Generated workout ID
        - latitude: float - First GPS latitude (degrees)
        - longitude: float - First GPS longitude (degrees)
        - elevation: float - First elevation/altitude (meters)
        - start_time: datetime - Workout start time
        
        Returns None if parsing fails or no valid GPS data found.
    """
    try:
        from fitparse import FitFile  # type: ignore
    except ImportError:
        raise ImportError(
            "fitparse package is required. Install with: pip install fitparse"
        )
    
    fit_path = Path(fit_path)
    if not fit_path.exists():
        logger.error(f"FIT file not found: {fit_path}")
        return None
    
    try:
        fitfile = FitFile(str(fit_path))
        
        # Variables to store first valid GPS and elevation data
        first_lat = None
        first_lon = None
        first_elevation = None
        start_time = None
        
        # First, try to get start time from session or file_id messages
        for message in fitfile.get_messages(['session', 'file_id', 'activity']):
            for field in message:
                if field.name == 'start_time' and field.value is not None:
                    start_time = field.value
                    break
                elif field.name == 'time_created' and field.value is not None and start_time is None:
                    start_time = field.value
            if start_time:
                break
        
        # Get record messages for GPS and elevation data
        for record in fitfile.get_messages('record'):
            record_data = {field.name: field.value for field in record}
            
            # Get first timestamp if start_time not set
            if start_time is None and 'timestamp' in record_data:
                start_time = record_data['timestamp']
            
            # Get first GPS coordinates
            if first_lat is None or first_lon is None:
                lat = record_data.get('position_lat')
                lon = record_data.get('position_long')
                
                # FIT files store positions in semicircles, convert to degrees
                if lat is not None and lon is not None:
                    # Semicircles to degrees: value * (180 / 2^31)
                    first_lat = lat * (180.0 / 2147483648.0)
                    first_lon = lon * (180.0 / 2147483648.0)
            
            # Get first elevation
            if first_elevation is None:
                # Try enhanced_altitude first (more precise), then regular altitude
                alt = record_data.get('enhanced_altitude') or record_data.get('altitude')
                if alt is not None:
                    first_elevation = float(alt)
            
            # If we have all data, we can stop iterating
            if first_lat is not None and first_lon is not None and first_elevation is not None:
                break
        
        # Generate workoutId from start time
        if start_time is None:
            logger.warning(f"No timestamp found in FIT file: {fit_path}")
            return None
        
        workout_id = generate_workout_id_from_datetime(start_time)
        
        return {
            'workoutId': workout_id,
            'latitude': first_lat,
            'longitude': first_lon,
            'elevation': first_elevation,
            'start_time': start_time,
        }
        
    except Exception as e:
        logger.error(f"Error parsing FIT file {fit_path}: {e}")
        return None


def import_garmin_fit(
    fit_path: str | Path,
    con: duckdb.DuckDBPyConnection
) -> str:
    """
    Import a single Garmin FIT file into DuckDB.
    
    Creates or updates the garmin_gps_data table with GPS and elevation data.
    
    Parameters
    ----------
    fit_path : str or Path
        Path to the FIT file to import.
    con : duckdb.DuckDBPyConnection
        DuckDB connection object.
    
    Returns
    -------
    str
        Import status: 'imported', 'skipped', or 'error'
    """
    fit_path = Path(fit_path)
    print(f"--------------------------------------------------")
    print(f"Importing FIT file: {fit_path.name}")
    
    try:
        # Parse the FIT file
        data = parse_fit_file(fit_path)
        
        if data is None:
            print(f"❌ Failed to parse FIT file: {fit_path}")
            return 'error'
        
        workout_id = data['workoutId']
        print(f"Workout ID: {workout_id}")
        
        # Ensure table exists
        con.execute("""
            CREATE TABLE IF NOT EXISTS garmin_gps_data (
                workoutId VARCHAR PRIMARY KEY,
                latitude DOUBLE,
                longitude DOUBLE,
                elevation DOUBLE,
                start_time TIMESTAMP,
                source_file VARCHAR
            )
        """)
        
        # Check if workout already exists
        result = con.execute(
            "SELECT 1 FROM garmin_gps_data WHERE workoutId = ? LIMIT 1",
            (workout_id,)
        ).fetchall()
        
        if result:
            print(f"⏭️ Workout with ID {workout_id} already exists. Skipping.")
            return 'skipped'
        
        # Insert the data
        con.execute(
            """
            INSERT INTO garmin_gps_data (workoutId, latitude, longitude, elevation, start_time, source_file)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                workout_id,
                data['latitude'],
                data['longitude'],
                data['elevation'],
                data['start_time'],
                fit_path.name
            )
        )
        
        # Print summary
        print(f"  Latitude: {data['latitude']}")
        print(f"  Longitude: {data['longitude']}")
        print(f"  Elevation: {data['elevation']} m")
        print(f"  Start Time: {data['start_time']}")
        print("✅ Imported.")
        return 'imported'
        
    except Exception as e:
        print(f"❌ Error importing FIT file: {e}")
        logger.exception(f"Error importing {fit_path}")
        return 'error'


def import_garmin_from_directory(
    glob_patterns: str | Path | Iterable[str | Path],
    data_dir: Optional[str | Path] = None,
    db_path: Optional[str | Path] = None
) -> dict[str, int]:
    """
    Batch-import Garmin FIT files from a directory into DuckDB.
    
    Parameters
    ----------
    glob_patterns : str, Path, or Iterable[str or Path]
        Glob pattern(s) to match FIT files (e.g., "*.fit", "*.FIT").
    data_dir : str, Path, or None (optional)
        Path to the directory containing FIT files. If None, uses current directory.
    db_path : str, Path, or None (optional)
        Path to the DuckDB database file. If None, loads DUCKDB_PATH from config.
    
    Returns
    -------
    dict
        Dictionary with processing statistics:
        {
            "total": int,      # Total number of files found
            "processed": int,  # Number of files successfully imported
            "skipped": int,    # Number of files skipped (duplicates)
            "errors": int,     # Number of files that failed to import
        }
    """
    # Load configuration and resolve paths
    config = load_configuration()
    
    if data_dir is None:
        data_dir_path = Path.cwd()
    else:
        data_dir_path = Path(data_dir).resolve()
    
    if db_path is None:
        db_path = config['DUCKDB_PATH']
    else:
        db_path = Path(db_path).resolve()
    
    # Handle single or multiple patterns
    if isinstance(glob_patterns, (str, Path)):
        patterns = [str(glob_patterns)]
    else:
        patterns = [str(pattern) for pattern in glob_patterns]
    
    # Collect all matching files
    fit_paths = []
    for pattern in patterns:
        fit_paths.extend(sorted(data_dir_path.glob(pattern)))
    
    # Deduplicate while preserving order
    fit_files = list(dict.fromkeys(fit_paths))
    
    total_files = len(fit_files)
    processed_files = 0
    skipped_files = 0
    error_files = 0
    
    if not fit_files:
        print(f"No FIT files found in {data_dir_path} matching patterns: {patterns}")
        return {
            "total": total_files,
            "processed": processed_files,
            "skipped": skipped_files,
            "errors": error_files,
        }
    
    print(f"Found {total_files} FIT file(s). Processing...\n")
    
    con = duckdb.connect(str(db_path))
    
    try:
        for fit_path in fit_files:
            try:
                result = import_garmin_fit(fit_path, con)
                
                if result == 'imported':
                    processed_files += 1
                elif result == 'skipped':
                    skipped_files += 1
                else:
                    error_files += 1
                    
            except Exception as e:
                error_files += 1
                print(f"❌ Error processing {fit_path}: {e}")
    
    finally:
        con.close()
    
    # Print summary
    print("\n" + "="*50)
    print("GARMIN FIT FILE PROCESSING REPORT")
    print("="*50)
    print(f"Total files found:      {total_files}")
    print(f"Successfully processed: {processed_files}")
    print(f"Skipped (duplicates):   {skipped_files}")
    print(f"Errors encountered:     {error_files}")
    print("="*50)
    
    if total_files > 0:
        success_rate = (processed_files / total_files) * 100
        print(f"Success rate:           {success_rate:.1f}%")
        
        if skipped_files > 0:
            print(f"Note: {skipped_files} file(s) were skipped because they already exist in the database.")
        
        if error_files > 0:
            print(f"Warning: {error_files} file(s) encountered errors during processing.")
    
    print("="*50)
    
    return {
        "total": total_files,
        "processed": processed_files,
        "skipped": skipped_files,
        "errors": error_files,
    }


def delete_garmin_workout_by_id(
    workout_id: str,
    db_path: Optional[str | Path] = None
):
    """
    Delete a workout from the garmin_gps_data table by workoutId.
    
    Parameters
    ----------
    workout_id : str
        The workout ID to delete.
    db_path : str, Path, or None (optional)
        Path to the DuckDB database file. If None, loads DUCKDB_PATH from config.
    """
    config = load_configuration()
    
    if db_path is None:
        db_path = config['DUCKDB_PATH']
    else:
        db_path = Path(db_path).resolve()
    
    try:
        con = duckdb.connect(str(db_path))
        query = "DELETE FROM garmin_gps_data WHERE workoutId = ?"
        con.execute(query, (workout_id,))
        print(f"Deleted workout with workoutId = '{workout_id}' from garmin_gps_data")
    except Exception as e:
        print(f"Error during deletion: {e}")
    finally:
        con.close()


def get_garmin_workout(
    workout_id: str,
    db_path: Optional[str | Path] = None
) -> dict | None:
    """
    Get a Garmin workout record by workoutId.
    
    Parameters
    ----------
    workout_id : str
        The workout ID to retrieve.
    db_path : str, Path, or None (optional)
        Path to the DuckDB database file. If None, loads DUCKDB_PATH from config.
    
    Returns
    -------
    dict or None
        Dictionary with workout data, or None if not found.
    """
    config = load_configuration()
    
    if db_path is None:
        db_path = config['DUCKDB_PATH']
    else:
        db_path = Path(db_path).resolve()
    
    try:
        con = duckdb.connect(str(db_path))
        result = con.execute(
            "SELECT * FROM garmin_gps_data WHERE workoutId = ?",
            (workout_id,)
        ).fetchone()
        
        if result:
            columns = ['workoutId', 'latitude', 'longitude', 'elevation', 'start_time', 'source_file']
            return dict(zip(columns, result))
        return None
    except Exception as e:
        print(f"Error retrieving workout: {e}")
        return None
    finally:
        con.close()


def list_garmin_workouts(
    db_path: Optional[str | Path] = None,
    limit: int = 100
) -> list[dict]:
    """
    List all Garmin workouts from the database.
    
    Parameters
    ----------
    db_path : str, Path, or None (optional)
        Path to the DuckDB database file. If None, loads DUCKDB_PATH from config.
    limit : int
        Maximum number of records to return.
    
    Returns
    -------
    list[dict]
        List of workout dictionaries.
    """
    config = load_configuration()
    
    if db_path is None:
        db_path = config['DUCKDB_PATH']
    else:
        db_path = Path(db_path).resolve()
    
    try:
        con = duckdb.connect(str(db_path))
        # Use parameterized query to prevent SQL injection
        results = con.execute(
            "SELECT * FROM garmin_gps_data ORDER BY start_time DESC LIMIT ?",
            (limit,)
        ).fetchall()
        
        columns = ['workoutId', 'latitude', 'longitude', 'elevation', 'start_time', 'source_file']
        return [dict(zip(columns, row)) for row in results]
    except Exception as e:
        print(f"Error listing workouts: {e}")
        return []
    finally:
        con.close()


__all__ = [
    'generate_workout_id_from_datetime',
    'parse_fit_file',
    'import_garmin_fit',
    'import_garmin_from_directory',
    'delete_garmin_workout_by_id',
    'get_garmin_workout',
    'list_garmin_workouts',
]
