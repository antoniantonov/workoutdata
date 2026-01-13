"""PostgreSQL-specific import functions for workout data.

This module provides PostgreSQL-specific helpers for:
- Deleting workout records by ID
- Importing workout CSVs with proper schema management
- Batch importing from directories
- Importing calorie calculation data

The functions handle the Polar CSV format with metadata rows and time-series data.
Connects to Azure PostgreSQL database using connection string from environment.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd  # type: ignore
import psycopg  # type: ignore
from psycopg import sql # type: ignore
from IPython.display import display

from config import load_configuration
from import_tools import fix_missing_hr, expand_table_with_missing_bpm


# =============================================================================
# User Info Database Management (PostgreSQL)
# =============================================================================

def ensure_userinfo_table(conn) -> None:
    """Ensure the userinfo table exists in the PostgreSQL database.
    
    Creates the userinfo table with schema for storing user profile information
    from both get_user_info and get_physical_info API calls.
    
    Args:
        conn: Active PostgreSQL connection (psycopg.Connection)
    """
    with conn.cursor() as cur:
        cur.execute("""
        CREATE TABLE IF NOT EXISTS userinfo (
            polar_user_id INTEGER PRIMARY KEY,
            first_name VARCHAR(255),
            last_name VARCHAR(255),
            birthdate VARCHAR(50),
            gender VARCHAR(50),
            weight FLOAT,
            height FLOAT,
            maximum_heart_rate INTEGER,
            resting_heart_rate INTEGER,
            aerobic_threshold INTEGER,
            anaerobic_threshold INTEGER,
            vo2_max FLOAT,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        conn.commit()
        print("✅ Userinfo table ensured")


def get_userinfo_from_db(conn, polar_user_id: int) -> Optional[dict]:
    """Retrieve user info from PostgreSQL database.
    
    Args:
        conn: Active PostgreSQL connection (psycopg.Connection)
        polar_user_id: Polar user ID
    
    Returns:
        Dictionary with user info, or None if not found
    """
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM userinfo WHERE polar_user_id = %s",
                (polar_user_id,)
            )
            result = cur.fetchone()
            
            if result:
                columns = [desc[0] for desc in cur.description]
                return dict(zip(columns, result))
            return None
    except Exception as e:
        print(f"⚠️  Error reading from userinfo table: {e}")
        return None


def save_userinfo_to_db(conn, user_data: dict) -> None:
    """Save or update user info in PostgreSQL database.
    
    Args:
        conn: Active PostgreSQL connection (psycopg.Connection)
        user_data: Dictionary with user information (must include polar_user_id)
    """
    if 'polar_user_id' not in user_data:
        print("⚠️  Cannot save userinfo: polar_user_id missing")
        return
    
    ensure_userinfo_table(conn)
    
    try:
        with conn.cursor() as cur:
            # Upsert: Delete old record if exists, then insert new
            cur.execute(
                "DELETE FROM userinfo WHERE polar_user_id = %s",
                (user_data['polar_user_id'],)
            )
            
            # Build insert statement dynamically based on available fields
            fields = list(user_data.keys())
            placeholders = ', '.join(['%s' for _ in fields])
            field_names = ', '.join([f'"{f}"' for f in fields])
            
            cur.execute(
                f"INSERT INTO userinfo ({field_names}, last_updated) VALUES ({placeholders}, CURRENT_TIMESTAMP)",
                tuple(user_data[f] for f in fields)
            )
            conn.commit()
            print(f"✅ Userinfo saved to database for user {user_data['polar_user_id']}")
    except Exception as e:
        print(f"⚠️  Error saving to userinfo table: {e}")
        conn.rollback()


def get_default_physical_info() -> dict:
    """Get hardcoded default physical info values.
    
    Returns:
        Dictionary with default physical information
    """
    return {
        'weight': 78.0,
        'height': 175.0,
        'maximum_heart_rate': 188,
        'resting_heart_rate': 55,
        'aerobic_threshold': 140,
        'anaerobic_threshold': 165,
        'vo2_max': 58.0
    }


def get_existing_workout_ids(config: dict) -> set:
    """Get set of existing workout IDs from PostgreSQL database.
    
    Parameters
    ----------
    config : dict
        Configuration dictionary from load_configuration()
    
    Returns
    -------
    set
        Set of workout IDs that already exist in workout_metadata table
    
    Raises
    ------
    ValueError
        If config is None
    """
    if config is None:
        raise ValueError("config parameter is required and cannot be None")
    
    existing_ids = set()
    
    try:
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
    except Exception as e:
        print(f"⚠️  Error checking existing workouts in PostgreSQL: {e}")
    
    return existing_ids


def get_postgres_connection():
    """
    Get a connection to the PostgreSQL database using configuration from environment.
    
    Returns
    -------
    psycopg.Connection
        Active PostgreSQL connection
    
    Raises
    ------
    ValueError
        If required PostgreSQL configuration is missing
    """
    config = load_configuration()
    
    # Check if we have connection string or individual parameters
    conn_string = config.get('POSTGRES_CONNECTION_STRING')
    
    if conn_string:
        # Use connection string if provided
        return psycopg.connect(conn_string)
    else:
        # Build connection from individual parameters
        host = config.get('POSTGRES_HOST')
        port = config.get('POSTGRES_PORT', 5432)
        database = config.get('POSTGRES_DATABASE')
        user = config.get('POSTGRES_USER')
        password = config.get('POSTGRES_PASSWORD')
        
        if not all([host, database, user, password]):
            raise ValueError(
                "Missing required PostgreSQL configuration. "
                "Provide either POSTGRES_CONNECTION_STRING or all of: "
                "POSTGRES_HOST, POSTGRES_DATABASE, POSTGRES_USER, POSTGRES_PASSWORD"
            )
        
        return psycopg.connect(
            host=host,
            port=port,
            dbname=database,
            user=user,
            password=password,
            sslmode='require'  # Azure PostgreSQL requires SSL
        )


def ensure_database_exists():
    """
    Ensure the PostgreSQL database exists. If not, create it.
    Note: This requires connecting to the default 'postgres' database first.
    """
    config = load_configuration()
    database_name = config.get('POSTGRES_DATABASE')
    
    if not database_name:
        raise ValueError("POSTGRES_DATABASE not configured")
    
    # Connect to default 'postgres' database to check/create target database
    try:
        # Temporarily connect to 'postgres' database
        conn_params = {}
        conn_string = config.get('POSTGRES_CONNECTION_STRING')
        
        if conn_string:
            # Parse connection string and replace dbname with 'postgres'
            # This is a simplified approach - in production, use proper URL parsing
            import re
            modified_string = re.sub(r'dbname=\w+', 'dbname=postgres', conn_string)
            conn = psycopg.connect(modified_string)
        else:
            host = config.get('POSTGRES_HOST')
            port = config.get('POSTGRES_PORT', 5432)
            user = config.get('POSTGRES_USER')
            password = config.get('POSTGRES_PASSWORD')
            
            conn = psycopg.connect(
                host=host,
                port=port,
                dbname='postgres',  # Connect to default database
                user=user,
                password=password,
                sslmode='require'
            )
        
        conn.autocommit = True  # Required for CREATE DATABASE
        
        with conn.cursor() as cur:
            # Check if database exists
            cur.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s",
                (database_name,)
            )
            exists = cur.fetchone()
            
            if not exists:
                # Create database
                cur.execute(sql.SQL("CREATE DATABASE {}").format(
                    sql.Identifier(database_name)
                ))
                print(f"✅ Created database: {database_name}")
            else:
                print(f"ℹ️  Database '{database_name}' already exists")
        
        conn.close()
        
    except Exception as e:
        print(f"⚠️  Could not create database (may already exist or require admin): {e}")


def clean_column_name(col_name: str) -> str:
    """
    Clean column name by removing special characters.
    Keeps only: letters, numbers, parentheses (), forward/back slashes /\
    
    Parameters
    ----------
    col_name : str
        Original column name
    
    Returns
    -------
    str
        Cleaned column name
    """
    # Keep only letters, numbers, spaces, parentheses, and slashes
    # Pattern: keep alphanumeric, space, (, ), /, \
    cleaned = re.sub(r'[^a-zA-Z0-9\s()/\\]', '', col_name)
    # Clean up multiple spaces
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned


def ensure_table_exists(conn, table_name: str, df: pd.DataFrame, 
                        primary_key: Optional[str] = None, 
                        foreign_keys: Optional[dict] = None, 
                        indexes: Optional[list] = None,
                        column_type_overrides: Optional[dict] = None):
    """
    Ensure a table exists in PostgreSQL database with schema inferred from DataFrame.
    Creates table if it doesn't exist.
    
    Parameters
    ----------
    conn : psycopg.Connection
        Active PostgreSQL connection
    table_name : str
        Name of the table to create
    df : pd.DataFrame
        DataFrame whose columns will be used to infer the table schema
    primary_key : str, optional
        Name of the primary key column
    foreign_keys : dict, optional
        Dictionary mapping column names to foreign key constraints
        Example: {'workoutId': 'REFERENCES workout_metadata("workoutId") ON DELETE CASCADE'}
    indexes : list, optional
        List of column names to create indexes on
    column_type_overrides : dict, optional
        Dictionary mapping column names to specific PostgreSQL types
        Example: {'workoutId': 'VARCHAR(50)', 'id': 'SERIAL'}
    """
    # Infer column types from DataFrame
    columns = {}
    for col in df.columns:
        dtype = df[col].dtype
        if pd.api.types.is_integer_dtype(dtype):
            columns[col] = 'INT'
        elif pd.api.types.is_float_dtype(dtype):
            columns[col] = 'FLOAT'
        else:
            # Default to TEXT for string/object types
            columns[col] = 'TEXT'
    
    # Apply overrides
    if column_type_overrides:
        columns.update(column_type_overrides)
    
    with conn.cursor() as cur:
        # Build column definitions
        col_defs = []
        for col_name, col_type in columns.items():
            col_def = f'"{col_name}" {col_type}'
            
            # Add primary key constraint
            if primary_key and col_name == primary_key:
                col_def += ' PRIMARY KEY'
            
            # Add foreign key constraint
            if foreign_keys and col_name in foreign_keys:
                col_def += f' {foreign_keys[col_name]}'
            
            col_defs.append(col_def)
        
        # Create table
        create_table_sql = f"""
            CREATE TABLE IF NOT EXISTS {table_name} (
                {', '.join(col_defs)}
            )
        """
        cur.execute(create_table_sql)
        
        # Create indexes
        if indexes:
            for idx_col in indexes:
                idx_name = f"idx_{table_name}_{idx_col.replace(' ', '_').lower()}"
                cur.execute(f"""
                    CREATE INDEX IF NOT EXISTS {idx_name}
                    ON {table_name}("{idx_col}")
                """)
        
        conn.commit()


def delete_workout_by_id(workout_id: str, conn_string: Optional[str] = None):
    """
    Delete all rows with the specified workoutId from both workout_metadata and timeseries tables.
    
    Parameters
    ----------
    workout_id : str
        The workout ID to delete.
    conn_string : str or None (optional)
        PostgreSQL connection string. If None, loads from config.
    """
    try:
        if conn_string:
            conn = psycopg.connect(conn_string)
        else:
            conn = get_postgres_connection()
        
        with conn.cursor() as cur:
            # Delete from timeseries first (foreign key constraint)
            cur.execute(
                'DELETE FROM timeseries WHERE "workoutId" = %s',
                (workout_id,)
            )
            timeseries_deleted = cur.rowcount
            
            # Delete from workout_metadata
            cur.execute(
                'DELETE FROM workout_metadata WHERE "workoutId" = %s',
                (workout_id,)
            )
            metadata_deleted = cur.rowcount
            
            conn.commit()
            print(f"✅ Deleted workout '{workout_id}': {metadata_deleted} metadata row(s), {timeseries_deleted} timeseries row(s)")
    
    except Exception as e:
        print(f"❌ Error during deletion: {e}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()


def import_workout_csv(csv_path: str, conn, approved_columns=None):
    """
    Import workout CSV file into PostgreSQL database using the provided connection.

    Steps: build workoutId, ensure tables exist, interpolate missing HR,
    filter columns, then insert.

    :param csv_path: Path to the CSV file to import.
    :param conn: PostgreSQL connection object (psycopg.Connection).
    :param approved_columns: List of column names to preserve in timeseries data.
        All other columns are excluded to avoid schema drift.
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
        
        # Clean column names in metadata
        metadata_df.columns = [clean_column_name(col) for col in metadata_df.columns]
        
        # create "DD-MM-YYYY_HHMMSS" style id (remove ":" so it is filename-safe)
        metadata_df["workoutId"] = (
            metadata_df["Date"].astype(str).str.strip() + "_" +
            metadata_df["Start time"].astype(str).str.replace(":", "", regex=False).str.strip()
        )
        workoutId = metadata_df.at[0, "workoutId"]
        print(f"Workout ID: {workoutId}")
    
        # ------------------------------------------------------------------
        # 2.  Ensure workout_metadata table exists
        # ------------------------------------------------------------------
        ensure_table_exists(
            conn, 
            'workout_metadata', 
            metadata_df,
            primary_key='workoutId',
            column_type_overrides={'workoutId': 'VARCHAR(50)'}
        )
        
        # Check if workout already exists
        with conn.cursor() as cur:
            cur.execute(
                'SELECT 1 FROM workout_metadata WHERE "workoutId" = %s LIMIT 1',
                (workoutId,)
            )
            result = cur.fetchone()
            
            if result:
                print(f"Found workout with ID: {workoutId}. Skipping this file import.")
                return 'skipped'
        
        # Insert metadata
        with conn.cursor() as cur:
            # Get column names from metadata_df
            columns = list(metadata_df.columns)
            values = [metadata_df.at[0, col] for col in columns]
            
            # Build INSERT statement
            placeholders = ', '.join(['%s'] * len(columns))
            col_names = ', '.join([f'"{col}"' for col in columns])
            
            insert_query = f"""
                INSERT INTO workout_metadata ({col_names})
                VALUES ({placeholders})
                ON CONFLICT ("workoutId") DO NOTHING
            """
            
            cur.execute(insert_query, values)
            inserted = cur.rowcount
            conn.commit()
            print(f"Number of inserted rows in workout metadata: {inserted}.")
        
        # ------------------------------------------------------------------
        # 3.  Read the time-series rows, add FK, fix HR gaps
        # ------------------------------------------------------------------
        df = pd.read_csv(csv_path, skiprows=2)
        
        # Clean column names in timeseries
        df.columns = [clean_column_name(col) for col in df.columns]
        
        df["workoutId"] = workoutId
        df = fix_missing_hr(df)
        
        # ------------------------------------------------------------------
        # 4.  Filter columns if approved_columns specified
        # ------------------------------------------------------------------
        if approved_columns is not None:
            # Clean approved column names too
            approved_columns_clean = [clean_column_name(col) for col in approved_columns]
            # Ensure required ID column retained
            approved_set = set(approved_columns_clean) | {"workoutId"}
            # Keep only approved columns that exist in DataFrame
            df = df[[col for col in df.columns if col in approved_set]]
        
        # ------------------------------------------------------------------
        # Ensure timeseries table exists
        # ------------------------------------------------------------------
        ensure_table_exists(
            conn, 
            'timeseries', 
            df,
            primary_key='id',
            foreign_keys={'workoutId': 'REFERENCES workout_metadata("workoutId") ON DELETE CASCADE'},
            indexes=['workoutId'],
            column_type_overrides={'id': 'SERIAL', 'workoutId': 'VARCHAR(50)'}
        )
        
        # ------------------------------------------------------------------
        # 5.  Insert into timeseries
        # ------------------------------------------------------------------
        if len(df) > 0:
            with conn.cursor() as cur:
                # Get column names
                columns = list(df.columns)
                col_names = ', '.join([f'"{col}"' for col in columns])
                placeholders = ', '.join(['%s'] * len(columns))
                
                insert_query = f"""
                    INSERT INTO timeseries ({col_names})
                    VALUES ({placeholders})
                """
                
                # Insert rows in batches
                values_list = [tuple(row) for row in df.itertuples(index=False, name=None)]
                cur.executemany(insert_query, values_list)
                
                inserted = cur.rowcount
                conn.commit()
                
                print(f"Number of inserted rows in workout timeseries: {inserted}. Number of expected rows: {len(df)}")
                print("✅ Imported.")
                return 'imported'
        else:
            print("⚠️  No time-series data to import")
            return 'imported'
    
    except Exception as e:
        print("❌ Error while importing workout CSV:")
        print(e)
        conn.rollback()
        
        # Cleanup on error
        if workoutId:
            try:
                print(f"Cleaning up workoutId = '{workoutId}'...")
                with conn.cursor() as cur:
                    cur.execute('DELETE FROM timeseries WHERE "workoutId" = %s', (workoutId,))
                    cur.execute('DELETE FROM workout_metadata WHERE "workoutId" = %s', (workoutId,))
                    conn.commit()
            except Exception as cleanup_error:
                print(f"⚠️ Cleanup warning: {cleanup_error}")
                conn.rollback()
        
        return 'error'


def import_workout_from_directory(
    glob_patterns: str | Path | Iterable[str | Path],
    data_dir: Optional[str | Path] = None,
    conn_string: Optional[str] = None
) -> dict[str, int]:
    """
    Batch-import workout CSV files from a directory into the PostgreSQL database.

    Parameters
    ----------
    glob_patterns : str, Path, or Iterable[str or Path]
        Glob pattern(s) to match CSV files (e.g., "Anton_Antonov*.CSV").
    data_dir : str, Path, or None (optional)
        Path to the directory containing workout CSV files. If None, loads OUTPUT_DIR from config.
    conn_string : str or None (optional)
        PostgreSQL connection string. If None, loads from config.

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
    
    # Ensure database exists
    ensure_database_exists()
    
    # Get connection
    if conn_string:
        conn = psycopg.connect(conn_string)
    else:
        conn = get_postgres_connection()

    try:
        for csv_path in csv_files:
            try:
                result = import_workout_csv(str(csv_path), conn, approved_columns=approved_columns)

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
        conn.close()

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


def calculate_and_import_calories(v02max_data_path: str | Path, conn_string: Optional[str] = None):
    """
    Process VO2max data to calculate calorie burn per HR and import to PostgreSQL.
    
    Parameters
    ----------
    v02max_data_path : str or Path
        Path to the CSV file containing VO2max/Calorie data.
    conn_string : str or None (optional)
        PostgreSQL connection string. If None, loads from config.
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

    # Write the collapsed DataFrame to PostgreSQL
    # Ensure database exists
    ensure_database_exists()
    
    # Get connection
    if conn_string:
        conn = psycopg.connect(conn_string)
    else:
        conn = get_postgres_connection()
    
    try:
        with conn.cursor() as cur:
            # Create calories_per_hr table if not exists
            cur.execute("""
                CREATE TABLE IF NOT EXISTS calories_per_hr (
                    "HR" FLOAT PRIMARY KEY,
                    "Calories" FLOAT,
                    "Calories_Second" FLOAT
                )
            """)
            
            # Clear existing data
            cur.execute("TRUNCATE TABLE calories_per_hr")
            
            # Insert new data
            for _, row in collapsed_df.iterrows():
                cur.execute(
                    """
                    INSERT INTO calories_per_hr ("HR", "Calories", "Calories_Second")
                    VALUES (%s, %s, %s)
                    ON CONFLICT ("HR") DO UPDATE SET
                        "Calories" = EXCLUDED."Calories",
                        "Calories_Second" = EXCLUDED."Calories_Second"
                    """,
                    (row['HR'], row['Calories'], row['Calories_Second'])
                )
            
            conn.commit()
            print(f"✅ Data imported to PostgreSQL table 'calories_per_hr' with {len(collapsed_df)} rows")
    
    finally:
        conn.close()


__all__ = [
    # Workout import functions
    'get_existing_workout_ids',
    'get_postgres_connection',
    'ensure_database_exists',
    'clean_column_name',
    'ensure_table_exists',
    'delete_workout_by_id',
    'import_workout_csv',
    'import_workout_from_directory',
    'calculate_and_import_calories',
    
    # User info database functions
    'ensure_userinfo_table',
    'get_userinfo_from_db',
    'save_userinfo_to_db',
    'get_default_physical_info',
]
