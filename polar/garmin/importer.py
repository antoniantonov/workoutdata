"""Functions for importing GarminDB health data into DuckDB or PostgreSQL.

This module reads data from GarminDB SQLite databases (via
:mod:`polar.garmin.reader`) and writes it to new tables in either DuckDB or
PostgreSQL, as configured by ``config['DATABASE_TYPE']``.

New tables created
------------------
``garmin_monitoring_hr``
    Continuous (all-day) heart rate timeseries from ``garmin_monitoring.db``.

``garmin_sleep``
    Nightly sleep sessions from ``garmin.db``.

``garmin_activities``
    Recorded activities with GPS start/stop coordinates from
    ``garmin_activities.db``.

``garmin_daily_summary``
    Per-day health summary from ``garmin.db``.

Each import function is idempotent: rows that already exist (matched by primary
key) are skipped or updated, so functions can be run multiple times safely.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_db_module(config: dict):
    """Return the correct storage module based on config['DATABASE_TYPE']."""
    db_type = config.get('DATABASE_TYPE', 'duckdb').lower()
    if db_type == 'postgres':
        from polar.storage import postgres as db_module
    else:
        from polar.storage import duckdb as db_module
    return db_module


def _import_to_duckdb(df: pd.DataFrame, table_name: str, pk_column: str, config: dict) -> dict:
    """Insert new rows from *df* into a DuckDB table, skipping existing PKs.

    Returns a stats dictionary with ``inserted`` and ``skipped`` counts.
    """
    import duckdb  # type: ignore

    db_path = config['DUCKDB_PATH']
    inserted = 0
    skipped = 0

    con = duckdb.connect(str(db_path))
    try:
        # Register the incoming dataframe as a view
        con.register('_garmin_incoming', df)

        # Create target table if it does not exist yet (schema from first load)
        con.execute(f"""
            CREATE TABLE IF NOT EXISTS {table_name} AS
            SELECT * FROM _garmin_incoming LIMIT 0
        """)

        # Determine which rows already exist
        existing = con.execute(
            f"SELECT {pk_column} FROM {table_name}"
        ).fetchdf()
        existing_pks = set(existing[pk_column].astype(str).tolist()) if len(existing) > 0 else set()

        incoming_pks = df[pk_column].astype(str).tolist()
        new_mask = [pk not in existing_pks for pk in incoming_pks]
        new_df = df[new_mask]

        skipped = len(df) - len(new_df)

        if len(new_df) > 0:
            con.register('_garmin_new', new_df)
            con.execute(f"INSERT INTO {table_name} SELECT * FROM _garmin_new")
            inserted = len(new_df)

    finally:
        con.close()

    return {'inserted': inserted, 'skipped': skipped}


def _import_to_postgres(df: pd.DataFrame, table_name: str, pk_column: str, config: dict) -> dict:
    """Insert new rows from *df* into a PostgreSQL table, skipping existing PKs.

    Returns a stats dictionary with ``inserted`` and ``skipped`` counts.
    """
    from polar.storage.postgres import get_postgres_connection

    conn = get_postgres_connection(config)
    inserted = 0
    skipped = 0

    try:
        with conn.cursor() as cur:
            # Build CREATE TABLE IF NOT EXISTS from DataFrame columns
            col_defs = _pg_column_definitions(df)
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS {table_name} (
                    {col_defs},
                    PRIMARY KEY ({pk_column})
                )
            """)
            conn.commit()

            # Fetch existing PKs
            cur.execute(f"SELECT {pk_column}::text FROM {table_name}")
            existing_pks = {row[0] for row in cur.fetchall()}

            # Insert only new rows
            for _, row in df.iterrows():
                pk_val = str(row[pk_column])
                if pk_val in existing_pks:
                    skipped += 1
                    continue

                cols = list(df.columns)
                placeholders = ', '.join(['%s'] * len(cols))
                col_names = ', '.join([f'"{c}"' for c in cols])
                values = tuple(
                    None if pd.isna(v) else v for v in row[cols]
                )
                cur.execute(
                    f"INSERT INTO {table_name} ({col_names}) VALUES ({placeholders})",
                    values,
                )
                inserted += 1

            conn.commit()
    finally:
        conn.close()

    return {'inserted': inserted, 'skipped': skipped}


def _pg_column_definitions(df: pd.DataFrame) -> str:
    """Return a comma-separated SQL column-definition string for a DataFrame."""
    type_map = {
        'int64': 'INTEGER',
        'int32': 'INTEGER',
        'float64': 'FLOAT',
        'float32': 'FLOAT',
        'bool': 'BOOLEAN',
        'datetime64[ns]': 'TIMESTAMP',
        'object': 'VARCHAR',
    }
    defs = []
    for col in df.columns:
        dtype = str(df[col].dtype)
        sql_type = type_map.get(dtype, 'VARCHAR')
        defs.append(f'"{col}" {sql_type}')
    return ', '.join(defs)


def _dispatch_import(
    df: pd.DataFrame,
    table_name: str,
    pk_column: str,
    config: dict,
) -> dict:
    """Route import to DuckDB or PostgreSQL based on config."""
    db_type = config.get('DATABASE_TYPE', 'duckdb').lower()
    if db_type == 'postgres':
        return _import_to_postgres(df, table_name, pk_column, config)
    return _import_to_duckdb(df, table_name, pk_column, config)


# ---------------------------------------------------------------------------
# Public importer functions
# ---------------------------------------------------------------------------

def import_garmin_heart_rate(
    garmin_db_dir: Optional[str | Path] = None,
    config: Optional[dict] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> dict:
    """Import continuous heart rate data from GarminDB into DuckDB or PostgreSQL.

    Reads the ``monitoring_hr`` table from ``garmin_monitoring.db`` and writes
    the data to a ``garmin_monitoring_hr`` table in the configured database.

    Parameters
    ----------
    garmin_db_dir:
        Directory containing GarminDB SQLite files.  Defaults to
        ``~/HealthData/DBs``.
    config:
        Configuration dictionary from :func:`polar.utils.config.load_configuration`.
    start_date:
        Optional ISO-8601 date string (``"YYYY-MM-DD"``) to import records
        from this date onward.
    end_date:
        Optional ISO-8601 date string (``"YYYY-MM-DD"``) to import records
        up to this date.

    Returns
    -------
    dict
        ``{"inserted": int, "skipped": int, "total": int}``

    Raises
    ------
    ValueError
        If *config* is ``None``.
    FileNotFoundError
        If the GarminDB monitoring database is not found.
    """
    if config is None:
        raise ValueError("config parameter is required and cannot be None")

    from polar.garmin.reader import read_heart_rate

    print("\n------------------------------------------------------")
    print("Importing Garmin heart rate data…")
    print("------------------------------------------------------")

    df = read_heart_rate(garmin_db_dir, start_date=start_date, end_date=end_date)

    if df.empty:
        print("⚠️  No heart rate data found.")
        return {'inserted': 0, 'skipped': 0, 'total': 0}

    # Ensure timestamp column is string-safe for PK comparisons
    df['timestamp'] = df['timestamp'].astype(str)

    stats = _dispatch_import(df, 'garmin_monitoring_hr', 'timestamp', config)
    stats['total'] = len(df)

    print(f"✅ Heart rate import complete — inserted: {stats['inserted']}, skipped: {stats['skipped']}")
    return stats


def import_garmin_sleep(
    garmin_db_dir: Optional[str | Path] = None,
    config: Optional[dict] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> dict:
    """Import sleep session data from GarminDB into DuckDB or PostgreSQL.

    Reads the ``sleep`` table from ``garmin.db`` and writes the data to a
    ``garmin_sleep`` table in the configured database.

    Parameters
    ----------
    garmin_db_dir:
        Directory containing GarminDB SQLite files.  Defaults to
        ``~/HealthData/DBs``.
    config:
        Configuration dictionary from :func:`polar.utils.config.load_configuration`.
    start_date:
        Optional ISO-8601 date string (``"YYYY-MM-DD"``) to import records
        from this date onward.
    end_date:
        Optional ISO-8601 date string (``"YYYY-MM-DD"``) to import records
        up to this date.

    Returns
    -------
    dict
        ``{"inserted": int, "skipped": int, "total": int}``

    Raises
    ------
    ValueError
        If *config* is ``None``.
    FileNotFoundError
        If the GarminDB main database is not found.
    """
    if config is None:
        raise ValueError("config parameter is required and cannot be None")

    from polar.garmin.reader import read_sleep

    print("\n------------------------------------------------------")
    print("Importing Garmin sleep data…")
    print("------------------------------------------------------")

    df = read_sleep(garmin_db_dir, start_date=start_date, end_date=end_date)

    if df.empty:
        print("⚠️  No sleep data found.")
        return {'inserted': 0, 'skipped': 0, 'total': 0}

    stats = _dispatch_import(df, 'garmin_sleep', 'day', config)
    stats['total'] = len(df)

    print(f"✅ Sleep import complete — inserted: {stats['inserted']}, skipped: {stats['skipped']}")
    return stats


def import_garmin_activities(
    garmin_db_dir: Optional[str | Path] = None,
    config: Optional[dict] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> dict:
    """Import activity data (including GPS coordinates) from GarminDB.

    Reads the ``activities`` table from ``garmin_activities.db`` and writes the
    data to a ``garmin_activities`` table in the configured database.

    Parameters
    ----------
    garmin_db_dir:
        Directory containing GarminDB SQLite files.  Defaults to
        ``~/HealthData/DBs``.
    config:
        Configuration dictionary from :func:`polar.utils.config.load_configuration`.
    start_date:
        Optional ISO-8601 date string (``"YYYY-MM-DD"``) to import activities
        starting from this date.
    end_date:
        Optional ISO-8601 date string (``"YYYY-MM-DD"``) to import activities
        up to this date.

    Returns
    -------
    dict
        ``{"inserted": int, "skipped": int, "total": int}``

    Raises
    ------
    ValueError
        If *config* is ``None``.
    FileNotFoundError
        If the GarminDB activities database is not found.
    """
    if config is None:
        raise ValueError("config parameter is required and cannot be None")

    from polar.garmin.reader import read_activities

    print("\n------------------------------------------------------")
    print("Importing Garmin activities data…")
    print("------------------------------------------------------")

    df = read_activities(garmin_db_dir, start_date=start_date, end_date=end_date)

    if df.empty:
        print("⚠️  No activity data found.")
        return {'inserted': 0, 'skipped': 0, 'total': 0}

    # Ensure datetime columns are string-safe
    for col in ('start_time', 'stop_time'):
        if col in df.columns:
            df[col] = df[col].astype(str)

    stats = _dispatch_import(df, 'garmin_activities', 'activity_id', config)
    stats['total'] = len(df)

    print(f"✅ Activities import complete — inserted: {stats['inserted']}, skipped: {stats['skipped']}")
    return stats


def import_garmin_daily_summary(
    garmin_db_dir: Optional[str | Path] = None,
    config: Optional[dict] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> dict:
    """Import daily health summary data from GarminDB into DuckDB or PostgreSQL.

    Reads the ``daily_summary`` table from ``garmin.db`` and writes the data to
    a ``garmin_daily_summary`` table in the configured database.

    Parameters
    ----------
    garmin_db_dir:
        Directory containing GarminDB SQLite files.  Defaults to
        ``~/HealthData/DBs``.
    config:
        Configuration dictionary from :func:`polar.utils.config.load_configuration`.
    start_date:
        Optional ISO-8601 date string (``"YYYY-MM-DD"``) to import records
        from this date onward.
    end_date:
        Optional ISO-8601 date string (``"YYYY-MM-DD"``) to import records
        up to this date.

    Returns
    -------
    dict
        ``{"inserted": int, "skipped": int, "total": int}``

    Raises
    ------
    ValueError
        If *config* is ``None``.
    FileNotFoundError
        If the GarminDB main database is not found.
    """
    if config is None:
        raise ValueError("config parameter is required and cannot be None")

    from polar.garmin.reader import read_daily_summary

    print("\n------------------------------------------------------")
    print("Importing Garmin daily summary data…")
    print("------------------------------------------------------")

    df = read_daily_summary(garmin_db_dir, start_date=start_date, end_date=end_date)

    if df.empty:
        print("⚠️  No daily summary data found.")
        return {'inserted': 0, 'skipped': 0, 'total': 0}

    stats = _dispatch_import(df, 'garmin_daily_summary', 'day', config)
    stats['total'] = len(df)

    print(f"✅ Daily summary import complete — inserted: {stats['inserted']}, skipped: {stats['skipped']}")
    return stats


def import_all_garmin_data(
    garmin_db_dir: Optional[str | Path] = None,
    config: Optional[dict] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> dict:
    """Import all supported GarminDB data types in one call.

    Runs :func:`import_garmin_heart_rate`, :func:`import_garmin_sleep`,
    :func:`import_garmin_activities`, and :func:`import_garmin_daily_summary`
    in sequence.  Each data type is imported independently; a failure for one
    type is caught and reported without aborting the remaining imports.

    Parameters
    ----------
    garmin_db_dir:
        Directory containing GarminDB SQLite files.  Defaults to
        ``~/HealthData/DBs``.
    config:
        Configuration dictionary from :func:`polar.utils.config.load_configuration`.
    start_date:
        Optional ISO-8601 date string (``"YYYY-MM-DD"``) applied to all
        import functions.
    end_date:
        Optional ISO-8601 date string (``"YYYY-MM-DD"``) applied to all
        import functions.

    Returns
    -------
    dict
        Nested dictionary keyed by data type with per-type stats::

            {
                "heart_rate":    {"inserted": int, "skipped": int, "total": int},
                "sleep":         {"inserted": int, "skipped": int, "total": int},
                "activities":    {"inserted": int, "skipped": int, "total": int},
                "daily_summary": {"inserted": int, "skipped": int, "total": int},
            }

    Raises
    ------
    ValueError
        If *config* is ``None``.
    """
    if config is None:
        raise ValueError("config parameter is required and cannot be None")

    results = {}
    kwargs = dict(garmin_db_dir=garmin_db_dir, config=config, start_date=start_date, end_date=end_date)

    importers = [
        ('heart_rate',    import_garmin_heart_rate),
        ('sleep',         import_garmin_sleep),
        ('activities',    import_garmin_activities),
        ('daily_summary', import_garmin_daily_summary),
    ]

    for key, fn in importers:
        try:
            results[key] = fn(**kwargs)
        except FileNotFoundError as exc:
            print(f"⚠️  Skipping {key}: {exc}")
            results[key] = {'inserted': 0, 'skipped': 0, 'total': 0, 'error': str(exc)}
        except Exception as exc:
            print(f"❌ Error importing {key}: {exc}")
            results[key] = {'inserted': 0, 'skipped': 0, 'total': 0, 'error': str(exc)}

    print("\n" + "=" * 50)
    print("GARMIN IMPORT SUMMARY")
    print("=" * 50)
    for key, stats in results.items():
        inserted = stats.get('inserted', 0)
        skipped = stats.get('skipped', 0)
        total = stats.get('total', 0)
        error = stats.get('error')
        if error:
            print(f"  {key:<16} ⚠️  {error}")
        else:
            print(f"  {key:<16} total={total:>8,}  inserted={inserted:>8,}  skipped={skipped:>8,}")
    print("=" * 50)

    return results


__all__ = [
    'import_garmin_heart_rate',
    'import_garmin_sleep',
    'import_garmin_activities',
    'import_garmin_daily_summary',
    'import_all_garmin_data',
]
