import pandas as pd
import duckdb
from pathlib import Path

def delete_workout_by_id(db_path: str, workout_id: str):
    """
    Delete all rows with the specified workoutId from both workout_metadata and timeseries tables.
    """
    try:
        con = duckdb.connect(db_path)
        query = "DELETE FROM workout_metadata WHERE workoutId = ?"
        con.execute(query, (workout_id,))
        query = "DELETE FROM timeseries WHERE workoutId = ?"
        con.execute(query, (workout_id,))
        print(f"Deleted rows with workoutId = '{workout_id}'")
    except Exception as e:
        print(f"Error during deletion: {e}")
    finally:
        con.close()

def fix_missing_hr(df):
    """
    Fix missing HR values by linear interpolation between known values.
    
    For sequential null HR values, interpolates linearly between the last known
    HR value before the nulls and the first known HR value after the nulls.
    
    :param df: DataFrame with 'HR (bpm)' and 'time' columns
    :return: DataFrame with interpolated HR values
    """
    
    hr_key = 'HR (bpm)'

    # Remove leading rows where HR is None
    first_valid_idx = df[hr_key].first_valid_index()
    # Remove trailing rows where HR is None
    last_valid_idx = df[hr_key].last_valid_index()

    if first_valid_idx is None or last_valid_idx is None:
        print("No valid HR values found.")
        raise ValueError("DataFrame contains no valid HR values.")

    if first_valid_idx > 0 or last_valid_idx < df.shape[0] - 1:
        print(f"Leading trim: [0:{first_valid_idx}], Trailing trim: [{last_valid_idx}:{df.shape[0] - 1}]")
        
    # Keep only rows between first and last valid HR (inclusive)
    df_fixed = df.loc[first_valid_idx:last_valid_idx].copy()
    
    # Find all null positions
    null_mask = df_fixed[hr_key].isnull()
    
    if not null_mask.any():
        print("No missing HR values found.")
        return df_fixed
    
    # Get groups of consecutive nulls
    null_groups = []
    in_null_group = False
    start_idx = None
    
    for i, is_null in enumerate(null_mask):
        if is_null and not in_null_group:
            # Start of a new null group
            start_idx = i
            in_null_group = True
        elif not is_null and in_null_group:
            # End of current null group
            null_groups.append((start_idx, i - 1))
            in_null_group = False
    
    # Handle case where nulls go to the end
    if in_null_group:
        null_groups.append((start_idx, len(df_fixed) - 1))
    
    print(f"Found {len(null_groups)} groups of consecutive null HR values")
    
    # Process each group of nulls
    for group_start, group_end in null_groups:
        null_count = group_end - group_start + 1
        
        # Find the last known HR value before nulls
        before_hr = None
        if group_start > 0:
            before_hr = df_fixed.iloc[group_start - 1][hr_key]

        # Find the first known HR value after nulls
        after_hr = None
        if group_end < len(df_fixed) - 1:
            after_hr = df_fixed.iloc[group_end + 1][hr_key]
        
        print(f"Null group: indices {group_start}-{group_end} ({null_count} nulls)")
        print(f"  Before HR: {before_hr}, After HR: {after_hr}")
        
        # Interpolate values
        if before_hr is not None and after_hr is not None:
            # Linear interpolation between two known values
            # Important: hr_diff is signed value and needs to remain this way in order to calculate delta_per_step correctly
            # if left HR is higher than right HR, we need negative delta_per_step in order to decrement the values.
            hr_diff = after_hr - before_hr  
            delta_per_step = hr_diff / (null_count + 1)
            
            print(f"  HR difference: {hr_diff}, Delta per step: {delta_per_step:.2f}")
            
            for i, null_idx in enumerate(range(group_start, group_end + 1)):
                interpolated_value = round(before_hr + (i + 1) * delta_per_step, 0)
                df_fixed.iloc[null_idx, df_fixed.columns.get_loc(hr_key)] = interpolated_value

        elif before_hr is not None:
            # Only have before value - forward fill
            print(f"  Forward filling with HR: {before_hr}")
            for null_idx in range(group_start, group_end + 1):
                df_fixed.iloc[null_idx, df_fixed.columns.get_loc(hr_key)] = before_hr

        elif after_hr is not None:
            # Only have after value - backward fill
            print(f"  Backward filling with HR: {after_hr}")
            for null_idx in range(group_start, group_end + 1):
                df_fixed.iloc[null_idx, df_fixed.columns.get_loc(hr_key)] = after_hr
        else:
            print(f"  Warning: No surrounding HR values found for interpolation")
    
    # Summary
    remaining_nulls = df_fixed[hr_key].isnull().sum()
    fixed_nulls = null_mask.sum() - remaining_nulls
    
    print(f"\nInterpolation complete:")
    print(f"  Original null values: {null_mask.sum()}")
    print(f"  Fixed values: {fixed_nulls}")
    print(f"  Remaining nulls: {remaining_nulls}")
    
    return df_fixed

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
        
        # create "yyyy-mm-dd_HHMMSS" style id (remove “:” so it is filename-safe)
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


def import_workout_from_directory(data_dir, glob_patterns):
    """Batch-import workout CSV files from a directory into the DuckDB database."""

    data_dir_path = Path(data_dir).resolve()

    if isinstance(glob_patterns, (str, Path)):
        patterns = [str(glob_patterns)]
    else:
        patterns = [str(pattern) for pattern in glob_patterns]

    csv_paths = []
    for pattern in patterns:
        csv_paths.extend(sorted(data_dir_path.glob(pattern)))

    # Deduplicate while preserving order
    seen = {}
    csv_files = [seen.setdefault(path, path) for path in csv_paths if path not in seen]

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

    db_path = data_dir_path / "database_v2.duckdb"
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