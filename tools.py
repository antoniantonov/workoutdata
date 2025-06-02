import pandas as pd
import duckdb
from pathlib import Path

def import_workout_csv(csv_path: str, con: duckdb.DuckDBPyConnection):
    """
    Import workout CSV file into DuckDB database using the provided connection.
    
    :param csv_path: Path to the CSV file to import.
    :param con: DuckDB connection object.
    """
    
    try:
        # ------------------------------------------------------------------
        # 1.  Read the CSV and build the workoutId
        # ------------------------------------------------------------------
        print(f"Importing csv file '{csv_path}'")
        csv_file = Path(csv_path)
        
        # -- first row holds file-wide metadata
        metadata_df = pd.read_csv(csv_path, nrows=1)
        
        # create "yyyy-mm-dd_HHMMSS" style id (remove “:” so it is filename-safe)
        metadata_df["workoutId"] = (
            metadata_df["Date"].astype(str).str.strip() + "_" +
            metadata_df["Start time"].astype(str).str.replace(":", "", regex=False).str.strip()
        )
    
        # ------------------------------------------------------------------
        # 2.  Ensure tables exist
        # ------------------------------------------------------------------
        # Register the metadata view for DuckDB
        con.register("meta_view", metadata_df)
        con.execute("""
        CREATE TABLE IF NOT EXISTS workout_metadata AS
        SELECT * FROM meta_view LIMIT 0;           -- schema only
        """)

        workoutId = metadata_df.at[0, "workoutId"]
        result = con.execute("""
        SELECT 1
        FROM workout_metadata
        WHERE workoutId = ?
        LIMIT 1
        """, (workoutId,)).fetchall()

        if result:
            print(f"Found workout with ID: {workoutId}. Skipping this file import.")
            return
        
        result = con.execute("""
        INSERT INTO workout_metadata
        SELECT *
        FROM meta_view AS m
        WHERE NOT EXISTS (
            SELECT 1 FROM workout_metadata w WHERE w.workoutId = m.workoutId
        );
        """)
        print(f"\tNumber of inserted rows in workout metadata: {result.rowcount}.")
        
        # ------------------------------------------------------------------
        # 3.  Read the time-series rows, add FK to link back to metadata,
        #     and append them to the timeseries table
        # ------------------------------------------------------------------
        df = pd.read_csv(csv_path, skiprows=2)
        
        # keep link to its parent workout
        df["workoutId"] = metadata_df.at[0, "workoutId"]
        
        # Register the time-series view for DuckDB
        con.register("ts_view", df)
        con.execute("""
        CREATE TABLE IF NOT EXISTS timeseries AS
        SELECT * FROM ts_view LIMIT 0;           -- schema only
        """)
        
        result = con.execute("""
        INSERT INTO timeseries
        SELECT * FROM ts_view;
        """)
        print(f"\tNumber of inserted rows in workout timeseries: {result.rowcount}.")
        print("\tImported.")
    
    except Exception as e:
        print("❌ Error while importing workout CSV:")
        print(e)

# ------------------------------------------------------------------
# 1.  Scans the directory for all svc files
# ------------------------------------------------------------------
data_dir = Path("./hr_data").resolve()

# Get all CSV files starting with 'Anton_Antonov' (case-sensitive)
csv_files = sorted(data_dir.glob("Anton_Antonov*.CSV"))

if not csv_files:
    print(f"No workout CSV files found in {data_dir}.")
else:
    print(f"Found {len(csv_files)} CSV file(s). Importing...\n")

    # Create the DuckDB connection
    db_path = data_dir / "database.duckdb"  # Set the path to the DuckDB database
    con = duckdb.connect(db_path)

    # ------------------------------------------------------------------
    # 2.  Import all csv files that were found.
    # ------------------------------------------------------------------
    for csv_path in csv_files:
        import_workout_csv(str(csv_path), con)
        
    # Close the connection after use
    con.close()