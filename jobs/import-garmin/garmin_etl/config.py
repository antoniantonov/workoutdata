"""Configuration loading for the Garmin import job.

All configuration is read from environment variables (optionally via a ``.env``
file). Relative paths are resolved against the job directory (the parent of this
package) so the job behaves identically whether it is launched from the repo
root, the job folder, or inside the Docker container (WORKDIR ``/app``).

Key switches
------------
- ``DATABASE_TYPE``    : ``duckdb`` (default) or ``postgres`` — selects the layer.
- ``GARMIN_DOWNLOAD``  : ``true`` to run the optional GarminDB download phase.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict


def _resolve(base_dir: Path, value: str) -> Path:
    """Resolve a possibly-relative path against ``base_dir``."""
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path


def load_garmin_configuration() -> Dict[str, object]:
    """Load Garmin import job configuration from environment variables.

    Returns
    -------
    dict
        Configuration values consumed by the transform / storage layers.

    Raises
    ------
    ValueError
        If ``DATABASE_TYPE`` is invalid, or PostgreSQL settings are incomplete
        when ``DATABASE_TYPE='postgres'``.
    """
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    # Job directory = parent of the garmin_etl package directory.
    job_dir = Path(__file__).resolve().parent.parent

    # ── Database backend switch ─────────────────────────────────────────────
    database_type = os.getenv("DATABASE_TYPE", "duckdb").lower()
    if database_type not in ("duckdb", "postgres"):
        raise ValueError(
            f"Invalid DATABASE_TYPE '{database_type}'. Must be 'duckdb' or 'postgres'."
        )

    # ── Download toggle ─────────────────────────────────────────────────────
    garmin_download = os.getenv("GARMIN_DOWNLOAD", "false").lower() == "true"
    garmin_download_latest = os.getenv("GARMIN_DOWNLOAD_LATEST", "true").lower() == "true"
    # When the download phase is required, a download failure aborts the job
    # instead of silently falling back to existing (stale) SQLite databases.
    garmin_download_required = os.getenv("GARMIN_DOWNLOAD_REQUIRED", "true").lower() == "true"

    # ── Paths ───────────────────────────────────────────────────────────────
    # Directory containing the GarminDB SQLite databases
    # (garmin_activities.db, garmin.db).
    garmin_db_dir = _resolve(
        job_dir, os.getenv("GARMIN_DB_DIR", "local_data/garmin_sqlite/DBs")
    )

    # Dedicated DuckDB output file (does NOT clobber Polar's database_v2.duckdb).
    garmin_duckdb_path = _resolve(
        job_dir, os.getenv("GARMIN_DUCKDB_PATH", "local_data/garmin.duckdb")
    )

    # GarminDB base dir (used by the optional download phase). Its DBs land in
    # ``<base>/DBs`` which should match ``GARMIN_DB_DIR``.
    garmin_base_dir = _resolve(
        job_dir, os.getenv("GARMIN_BASE_DIR", "local_data/garmin_sqlite")
    )

    # GarminDB config dir (holds GarminConnectConfig.json + garth_session).
    garmin_config_dir_env = os.getenv("GARMIN_CONFIG_DIR")
    garmin_config_dir = (
        _resolve(job_dir, garmin_config_dir_env) if garmin_config_dir_env else None
    )

    # Timezone used only as future correlation metadata (timestamps are stored
    # as local-naive wall clock values, not localized).
    garmin_local_timezone = os.getenv("GARMIN_LOCAL_TIMEZONE", "UTC")

    # ── PostgreSQL configuration ────────────────────────────────────────────
    postgres_connection_string = os.getenv("POSTGRES_CONNECTION_STRING")
    postgres_host = os.getenv("POSTGRES_HOST")
    postgres_port = os.getenv("POSTGRES_PORT", "5432")
    postgres_database = os.getenv("POSTGRES_DATABASE", "workoutdata")
    postgres_user = os.getenv("POSTGRES_USER")
    postgres_password = os.getenv("POSTGRES_PASSWORD")
    postgres_sslmode = os.getenv("POSTGRES_SSLMODE", "prefer")

    if database_type == "postgres" and not postgres_connection_string:
        missing = [
            name
            for name, val in (
                ("POSTGRES_HOST", postgres_host),
                ("POSTGRES_DATABASE", postgres_database),
                ("POSTGRES_USER", postgres_user),
                ("POSTGRES_PASSWORD", postgres_password),
            )
            if not val
        ]
        if missing:
            raise ValueError(
                "DATABASE_TYPE is 'postgres' but missing required configuration. "
                f"Provide POSTGRES_CONNECTION_STRING or all of: {', '.join(missing)}"
            )

    # ── Azure Storage (optional) ────────────────────────────────────────────
    azure_storage_enabled = os.getenv("AZURE_STORAGE_ENABLED", "false").lower() == "true"
    azure_storage_account_name = os.getenv("AZURE_STORAGE_ACCOUNT_NAME")
    azure_storage_container_name = os.getenv("AZURE_STORAGE_CONTAINER_NAME", "workout-data")

    config: Dict[str, object] = {
        "JOB_DIR": job_dir,
        "DATABASE_TYPE": database_type,
        "GARMIN_DOWNLOAD": garmin_download,
        "GARMIN_DOWNLOAD_LATEST": garmin_download_latest,
        "GARMIN_DOWNLOAD_REQUIRED": garmin_download_required,
        "GARMIN_DB_DIR": garmin_db_dir,
        "GARMIN_DUCKDB_PATH": garmin_duckdb_path,
        "GARMIN_BASE_DIR": garmin_base_dir,
        "GARMIN_CONFIG_DIR": garmin_config_dir,
        "GARMIN_LOCAL_TIMEZONE": garmin_local_timezone,
        "POSTGRES_CONNECTION_STRING": postgres_connection_string,
        "POSTGRES_HOST": postgres_host,
        "POSTGRES_PORT": postgres_port,
        "POSTGRES_DATABASE": postgres_database,
        "POSTGRES_USER": postgres_user,
        "POSTGRES_PASSWORD": postgres_password,
        "POSTGRES_SSLMODE": postgres_sslmode,
        "AZURE_STORAGE_ENABLED": azure_storage_enabled,
        "AZURE_STORAGE_ACCOUNT_NAME": azure_storage_account_name,
        "AZURE_STORAGE_CONTAINER_NAME": azure_storage_container_name,
    }

    print("✅ Garmin configuration loaded")
    print(f"  - Database type: {database_type.upper()}")
    print(f"  - Download phase: {'enabled' if garmin_download else 'disabled (transform-only)'}")
    print(f"  - Garmin SQLite dir: {garmin_db_dir}")
    if database_type == "duckdb":
        print(f"  - DuckDB path: {garmin_duckdb_path}")
    else:
        if postgres_connection_string:
            print("  - PostgreSQL: via connection string")
        else:
            print(f"  - PostgreSQL: {postgres_host}:{postgres_port}/{postgres_database}")
    print(f"  - Azure Storage: {'enabled' if azure_storage_enabled else 'disabled'}")

    return config


__all__ = ["load_garmin_configuration"]
