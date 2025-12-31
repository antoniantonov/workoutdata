"""DuckDB-specific import functions for workout data.

This module provides DuckDB-specific helpers for:
- Deleting workout records by ID
- Importing workout CSVs with proper schema management
- Batch importing from directories
- Importing calorie calculation data

The functions handle the Polar CSV format with metadata rows and time-series data.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

import duckdb  # type: ignore
import pandas as pd  # type: ignore
from IPython.display import display

from config import load_configuration
from import_tools import fix_missing_hr, expand_table_with_missing_bpm


def delete_workout_by_id(workout_id: str, db_path: Optional[str | Path] = None):
    """
    Delete all rows with the specified workoutId from both workout_metadata and timeseries tables.
    
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
        query = "DELETE FROM workout_metadata WHERE workoutId = ?"
        con.execute(query, (workout_id,))
        query = "DELETE FROM timeseries WHERE workoutId = ?"
        con.execute(query, (workout_id,))
        print(f"Deleted rows with workoutId = '{workout_id}'")
    except Exception as e:
        print(f"Error during deletion: {e}")
    finally:
        con.close()


def import_workout_csv(csv_path: str, con: duckdb.DuckDBPyConnection, approved_columns=None):
    """
    Import workout CSV file into DuckDB database using the provided connection.

    Steps: build workoutId, ensure metadata/timeseries tables exist, interpolate missing HR,
    null out disallowed columns, then insert.

    :param csv_path: Path to the CSV file to import.
    :param con: DuckDB connection object.
    :param approved_columns: Iterable of column names whose values should be preserved in the
        timeseries DataFrame (workoutId will always be preserved). All other columns are blanked (set to NULL)
        rather than dropped to avoid accidental schema drift.
    """
    
    workoutId = None
    try:
        # ------------------------------------------------------------------
        # 1.  Read the CSV and build the workoutId
        # ------------------------------------------------------------------
        print("--------------------------------------------------")
        print(f"Importing csv file '{csv_path}'")
        csv_file = Path(csv_path)
        
        # -- first row holds file-wide metadata
        metadata_df = pd.read_csv(csv_path, nrows=1)
        
        # create "yyyy-mm-dd_HHMMSS" style id (remove ":" so it is filename-safe)
        metadata_df["workoutId"] = (
            metadata_df["Date"].astype(str).str.strip() + "_" +
            metadata_df["Start time"].astype(str).str.replace(":", "", regex=False).str.strip()
        )
        workoutId = metadata_df.at[0, "workoutId"]
        print(f"Workout ID: {workoutId}")
    
        # ------------------------------------------------------------------
        # 2.  Ensure tables exist
        # ------------------------------------------------------------------
        con.register("meta_view", metadata_df)
        con.execute("""
        CREATE TABLE IF NOT EXISTS workout_metadata AS
        SELECT * FROM meta_view LIMIT 0;           -- schema only
        """)

        result = con.execute("""
        SELECT 1
        FROM workout_metadata
        WHERE workoutId = ?
        LIMIT 1
        """, (workoutId,)).fetchall()

        if result:
            print(f"Found workout with ID: {workoutId}. Skipping this file import.")
            return 'skipped'
        
        con.execute("""
        INSERT INTO workout_metadata
        SELECT *
        FROM meta_view AS m
        WHERE NOT EXISTS (
            SELECT 1 FROM workout_metadata w WHERE w.workoutId = m.workoutId
        );
        """)
        # Verify insert
        result = con.execute("""
        SELECT 1
        FROM workout_metadata
        WHERE workoutId = ?
        LIMIT 1
        """, (workoutId,)).fetchall()
        print(f"Number of inserted rows in workout metadata: {len(result)}.")
        
        # ------------------------------------------------------------------
        # 3.  Read the time-series rows, add FK, fix HR gaps
        # ------------------------------------------------------------------
        df = pd.read_csv(csv_path, skiprows=2)
        df["workoutId"] = workoutId
        df = fix_missing_hr(df)
        
        # ------------------------------------------------------------------
        # 4.  Null out disallowed columns before registering view
        # ------------------------------------------------------------------
        if approved_columns is not None:
            # Ensure required ID column retained
            approved_set = set(approved_columns) | {"workoutId"}
            for col in df.columns:
                if col not in approved_set:
                    # Replace entire column values with NA (preserve column placeholder)
                    df[col] = pd.NA
        
        # ------------------------------------------------------------------
        # 5.  Register and insert into timeseries
        # ------------------------------------------------------------------
        con.register("ts_view", df)
        con.execute("""
        CREATE TABLE IF NOT EXISTS timeseries AS
        SELECT * FROM ts_view LIMIT 0;           -- schema only
        """)

        df_len = len(df)
        con.execute("""
        INSERT INTO timeseries
        SELECT * FROM ts_view;
        """)
        
        result = con.execute("""
        SELECT 1
        FROM timeseries
        WHERE workoutId = ?
        """, (workoutId,)).fetchall()
        print(f"Number of inserted rows in workout timeseries: {len(result)}. Number of expected rows: {df_len}")
        print("✅ Imported.")
        return 'imported'
    
    except Exception as e:
        print("❌ Error while importing workout CSV:")
        print(e)
        if workoutId:
            for table in ("timeseries", "workout_metadata"):
                try:
                    print(f"Cleaning up workoutId = '{workoutId}' from table '{table}'.")
                    con.execute(f"DELETE FROM {table} WHERE workoutId = ?", (workoutId,))
                except Exception as cleanup_error:
                    print(f"⚠️ Cleanup warning for table '{table}': {cleanup_error}")
        return 'error'


def import_workout_from_directory(
    glob_patterns: str | Path | Iterable[str | Path],
    data_dir: Optional[str | Path] = None,
    db_path: Optional[str | Path] = None
) -> dict[str, int]:
    """
    Batch-import workout CSV files from a directory into the DuckDB database.

    Parameters
    ----------
    glob_patterns : str, Path, or Iterable[str or Path]
        Glob pattern(s) to match CSV files (e.g., "Anton_Antonov*.CSV").
    data_dir : str, Path, or None (optional)
        Path to the directory containing workout CSV files. If None, loads OUTPUT_DIR from config.
    db_path : str, Path, or None (optional)
        Path to the DuckDB database file. If None, loads DUCKDB_PATH from config.

    Returns
    -------
    dict
        Dictionary with processing statistics:
        {
            "total": int,      # Total number of files found
            "processed": int,  # Number of files successfully imported
            "skipped": int,    # Number of files skipped (e.g., duplicates)
            "errors": int,     # Number of files that failed to import
        }

    Exceptions
    ----------
    Any exceptions during import are caught internally; error details are printed,
    and the error count is incremented in the returned dictionary.
    """
    # Load configuration and resolve paths
    config = load_configuration()
    
    if data_dir is None:
        data_dir_path = config['OUTPUT_DIR']
    else:
        data_dir_path = Path(data_dir).resolve()
    
    if db_path is None:
        db_path = config['DUCKDB_PATH']
    else:
        db_path = Path(db_path).resolve()

    if isinstance(glob_patterns, (str, Path)):
        patterns = [str(glob_patterns)]
    else:
        patterns = [str(pattern) for pattern in glob_patterns]

    csv_paths = []
    for pattern in patterns:
        csv_paths.extend(sorted(data_dir_path.glob(pattern)))

    # Deduplicate while preserving order
    csv_files = list(dict.fromkeys(csv_paths))

    total_files = len(csv_files)
    processed_files = 0
    skipped_files = 0
    error_files = 0

    approved_columns = ["Sample rate", "Time", "HR (bpm)"]

    if not csv_files:
        print(f"No workout CSV files found in {data_dir_path}.")
        return {
            "total": total_files,
            "processed": processed_files,
            "skipped": skipped_files,
            "errors": error_files,
        }

    print(f"Found {total_files} CSV file(s). Processing...\n")
    
    con = duckdb.connect(db_path)

    try:
        for csv_path in csv_files:
            try:
                result = import_workout_csv(str(csv_path), con, approved_columns=approved_columns)

                if result.lower() == 'imported':
                    processed_files += 1
                elif result.lower() == 'skipped':
                    skipped_files += 1
                elif result.lower() == 'error':
                    error_files += 1
                else:
                    print(f"❌ Unexpected result '{result}' from import_workout_csv for file {csv_path}")
                    error_files += 1

            except Exception as e:
                error_files += 1
                print(f"❌ Error processing {csv_path}: {e}")

    finally:
        con.close()

    print("\n" + "="*50)
    print("FILE PROCESSING REPORT")
    print("="*50)
    print(f"Total files found:     {total_files}")
    print(f"Successfully processed: {processed_files}")
    print(f"Skipped (duplicates):  {skipped_files}")
    print(f"Errors encountered:    {error_files}")
    print("="*50)

    if total_files > 0:
        success_rate = (processed_files / total_files) * 100
        print(f"Success rate:          {success_rate:.1f}%")

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


def import_to_duckdb(df: pd.DataFrame, table_name: str, db_file: str | Path = 'workout_data.db', replace: bool = False) -> Optional[duckdb.DuckDBPyConnection]:
    """
    Import a pandas DataFrame into a DuckDB table.
    
    Parameters
    ----------
    df : pandas.DataFrame
        The DataFrame to import into DuckDB
    table_name : str
        Name for the DuckDB table
    db_file : str or Path, optional
        Path to DuckDB database file (default: 'workout_data.db')
    replace : bool, optional
        Whether to replace an existing table (default: False)
    
    Returns
    -------
    duckdb.DuckDBPyConnection or None
        The active DuckDB connection (if not closed) or None.
    """
    db_path_str = str(db_file)
    con = duckdb.connect(db_path_str)
    
    try:
        # Check if table exists
        table_exists = con.execute(f"SELECT count(*) FROM information_schema.tables WHERE table_name='{table_name}'").fetchone()[0] > 0
        
        if table_exists:
            if replace:
                con.execute(f"DROP TABLE IF EXISTS {table_name}")
            else:
                print(f"Table '{table_name}' already exists. Set replace=True to overwrite.")
                return None
        
        # Import the DataFrame into a DuckDB table
        con.register('df_view', df)
        con.execute(f"CREATE TABLE {table_name} AS SELECT * FROM df_view")
        
        # Verify the import
        count = con.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
        print(f"Data imported to DuckDB table '{table_name}' with {count} rows")
        
    finally:
        con.close()
    return None


def calculate_and_import_calories(duckdb_path: str | Path, v02max_data_path: str | Path):
    """
    Process VO2max data to calculate calorie burn per HR and import to DuckDB.
    
    Parameters
    ----------
    duckdb_path : str or Path
        Path to the DuckDB database.
    v02max_data_path : str or Path
        Path to the CSV file containing VO2max/Calorie data.
    """
    print(f"Reading VO2max data from {v02max_data_path}...")
    df = pd.read_csv(v02max_data_path)
    
    # Keep only HR and Calories columns
    df = df[['HR', 'Calories']]
    print(f"Total rows in original DataFrame: {len(df)}")

    # Expand the table
    expanded_df = expand_table_with_missing_bpm(df)
    print(f"Total rows in expanded DataFrame: {len(expanded_df)}")

    # Set display options to show all rows and columns without truncation
    pd.set_option('display.max_rows', None)
    pd.set_option('display.max_columns', None)
    pd.set_option('display.width', None)
    pd.set_option('display.float_format', '{:.6f}'.format)

    # Find the index of the maximum HR value in expanded_df
    max_hr_value = expanded_df['HR'].max()
    max_hr_index = expanded_df[expanded_df['HR'] == max_hr_value].index[0]

    print(f"Maximum HR value in expanded_df: {max_hr_value} at index {max_hr_index}")

    # Create a subset of the expanded DataFrame from index 0 to the index of maximum HR
    hr_rise_expanded_df = expanded_df.loc[:max_hr_index].copy()
    print(f"Total rows in expanded sliced (0:{max_hr_index}) DataFrame : {len(hr_rise_expanded_df)}")

    # Sorting it because we can have the following sequence of HR
    # e.g., 150, 151, 152, 151, 150.
    # Sorting it will put all the same HR values next to each other, so the collapsing algo
    # below will be able to collapse them properly.
    hr_rise_expanded_df = hr_rise_expanded_df.sort_values(by='HR').reset_index(drop=True)

    # Display the subset of expanded DataFrame (data up to max HR)
    print("\nExpanded data from HR rise (index 0 to max HR):")
    display(hr_rise_expanded_df)

    # Create a group identifier for consecutive identical HR values
    hr_rise_expanded_df['group'] = (hr_rise_expanded_df['HR'] != hr_rise_expanded_df['HR'].shift()).cumsum()

    # Group by both group and HR to collapse only consecutive duplicates
    collapsed_df = hr_rise_expanded_df.groupby(['group', 'HR']).agg({
        'Calories': 'mean',
        'Calories_Second': 'mean'
    }).reset_index().drop('group', axis=1)
    
    print(f"Total rows in collapsed DataFrame: {len(collapsed_df)}")

    # Display the collapsed DataFrame
    print("\nFull collapsed DataFrame:")
    display(collapsed_df)

    # Write the collapsed DataFrame to DuckDB
    import_to_duckdb(collapsed_df, 'calories_per_hr', duckdb_path, replace=True)


__all__ = [
    'delete_workout_by_id',
    'import_workout_csv',
    'import_workout_from_directory',
    'import_to_duckdb',
    'calculate_and_import_calories',
]
