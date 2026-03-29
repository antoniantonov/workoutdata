"""Garmin Connect data integration module.

This module provides a thin wrapper around the official garmindb PyPI package
(https://github.com/tcgoetz/GarminDB) for downloading, importing, and analyzing
Garmin Connect health data.

The garmindb package stores data in SQLite databases. This wrapper configures
it to use `data/sqlite/` within the workoutdata repository.

Usage::

    from garmin import setup_config, download_all, download_latest

    # First time: set up GarminConnectConfig.json
    setup_config('your@email.com', 'your-password')

    # Download all data
    download_all()

    # Or just latest data
    download_latest()
"""

from garmin.config import setup_config, get_config_path, get_db_dir
from garmin.cli_wrapper import (
    download_all,
    download_latest,
    import_data,
    analyze_data,
    backup_data,
)

__all__ = [
    'setup_config',
    'get_config_path',
    'get_db_dir',
    'download_all',
    'download_latest',
    'import_data',
    'analyze_data',
    'backup_data',
]
