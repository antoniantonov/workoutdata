"""GarminDB configuration helpers.

Manages the GarminConnectConfig.json file that garmindb uses for credentials
and data directory settings. Configures garmindb to store its SQLite databases
in `data/sqlite/` within this repository instead of the default ~/HealthData/.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


# Repository root (two levels up from this file for garmin/config.py at repo root)
REPO_ROOT = Path(__file__).resolve().parent.parent
GARMINDB_DATA_DIR = REPO_ROOT / "data" / "sqlite"

# GarminDB config location
GARMINDB_CONFIG_DIR = Path.home() / ".GarminDb"
GARMINDB_CONFIG_FILE = GARMINDB_CONFIG_DIR / "GarminConnectConfig.json"


def get_db_dir() -> Path:
    """Return the path where GarminDB SQLite databases are stored."""
    return GARMINDB_DATA_DIR


def get_config_path() -> Path:
    """Return the path to the GarminConnectConfig.json file."""
    return GARMINDB_CONFIG_FILE


def setup_config(
    username: str,
    password: str,
    *,
    monitoring_start_date: str = "01/01/2024",
    sleep_start_date: str = "01/01/2024",
    weight_start_date: str = "01/01/2024",
    rhr_start_date: str = "01/01/2024",
    hrv_start_date: str = "01/01/2024",
    download_latest_activities: int = 25,
    download_all_activities: int = 1000,
    metric: bool = True,
) -> Path:
    """Create or update the GarminConnectConfig.json file.

    Parameters
    ----------
    username:
        Garmin Connect email/username.
    password:
        Garmin Connect password.
    monitoring_start_date:
        Start date for monitoring data download (MM/DD/YYYY format).
    sleep_start_date:
        Start date for sleep data download (MM/DD/YYYY format).
    weight_start_date:
        Start date for weight data download (MM/DD/YYYY format).
    rhr_start_date:
        Start date for resting heart rate data download (MM/DD/YYYY format).
    hrv_start_date:
        Start date for HRV data download (MM/DD/YYYY format).
    download_latest_activities:
        Number of latest activities to download incrementally.
    download_all_activities:
        Maximum number of activities to download in full sync.
    metric:
        Whether to use metric units (default True).

    Returns
    -------
    Path
        Path to the created/updated config file.
    """
    config = {
        "db": {
            "type": "sqlite"
        },
        "garmin": {
            "domain": "garmin.com"
        },
        "credentials": {
            "user": username,
            "secure_password": False,
            "password": password
        },
        "data": {
            "weight_start_date": weight_start_date,
            "sleep_start_date": sleep_start_date,
            "rhr_start_date": rhr_start_date,
            "hrv_start_date": hrv_start_date,
            "monitoring_start_date": monitoring_start_date,
            "download_latest_activities": download_latest_activities,
            "download_all_activities": download_all_activities,
        },
        "directories": {
            "relative_to_home": False,
            "base_dir": str(GARMINDB_DATA_DIR),
            "mount_dir": "/Volumes/GARMIN"
        },
        "enabled_stats": {
            "monitoring": True,
            "steps": True,
            "itime": True,
            "sleep": True,
            "rhr": True,
            "hrv": True,
            "weight": True,
            "activities": True,
        },
        "course_views": {
            "steps": []
        },
        "modes": {},
        "activities": {
            "display": []
        },
        "settings": {
            "metric": metric,
            "default_display_activities": ["walking", "running", "cycling"]
        },
        "checkup": {
            "look_back_days": 90
        }
    }

    GARMINDB_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    GARMINDB_CONFIG_FILE.write_text(json.dumps(config, indent=4))

    print(f"✅ GarminDB config written to: {GARMINDB_CONFIG_FILE}")
    print(f"   Database directory: {GARMINDB_DATA_DIR}")
    return GARMINDB_CONFIG_FILE
