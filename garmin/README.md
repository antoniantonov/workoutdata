# Garmin Connect Data Integration

This module wraps the [GarminDB](https://github.com/tcgoetz/GarminDB) Python package
to download, import, and analyze health data from Garmin Connect.

## Setup

1. Install the garmindb package (already in requirements.txt):
   ```bash
   uv pip install garmindb
   ```

2. Configure your Garmin Connect credentials:
   ```python
   from garmin import setup_config
   setup_config('your@email.com', 'your-password')
   ```

3. Download your data:
   ```python
   from garmin import download_all
   download_all()  # Full download
   ```

4. For incremental updates:
   ```python
   from garmin import download_latest
   download_latest()  # Only latest data
   ```

## Data Storage

SQLite databases are stored in `data/sqlite/` (gitignored):
- `garmin.db` — sleep, resting heart rate, daily summaries
- `garmin_monitoring.db` — continuous heart rate monitoring
- `garmin_activities.db` — recorded activities with GPS data
- `garmin_summary.db` — daily/weekly/monthly/yearly summaries

## Jupyter Notebooks

GarminDB analysis notebooks are available in `garmin/GarminDB-notebooks/Jupyter/`:
- `activities.ipynb` — Activity analysis
- `daily.ipynb` — Daily health summary
- `daily_trends.ipynb` — Health trends over time
- `monitoring.ipynb` — Continuous monitoring data
- `summary.ipynb` — Overall summary

## Configuration

The GarminDB config file is stored at `~/.GarminDb/GarminConnectConfig.json` (outside the repo).
Use `garmin.setup_config()` to create or update it.
