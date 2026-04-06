---
jupytext:
  text_representation:
    extension: .md
    format_name: myst
    format_version: 0.13
    jupytext_version: 1.16.6
kernelspec:
  display_name: Python 3
  language: python
  name: python3
---

# Calorie Calculator and HR Zones Importer

This notebook calculates calorie burn rates based on heart rate data and imports them into the database. It also ensures HR zones are properly configured.

## Process
1.  **Load Configuration**: Loads database and path settings from environment variables.
2.  **Import HR Zones**: Ensures the `hr_zones` table exists and is populated from `zones.csv`.
3.  **Load VO2max Data**: Reads VO2max/Calorie data from a CSV file (`v02max_data.csv`).
4.  **Expand & Interpolate**: Expands the data to fill in missing heart rate values using linear interpolation.
5.  **Filter & Collapse**: 
    -   Slices the data up to the maximum heart rate (ignoring cool-down phase).
    -   Sorts by HR.
    -   Collapses consecutive duplicate HR values by averaging calories.
6.  **Import Calories**: Imports the processed data into the `calories_per_hr` table.

## Usage
- `ensure_hr_zones_table(config)`: Imports HR zones from zones.csv
- `calculate_and_import_calories(path, config)`: Calculates and imports calorie data

```{code-cell} ipython3
import sys
from pathlib import Path

# Add src to path
repo_root = Path.cwd().parent if 'notebooks' in str(Path.cwd()) else Path.cwd()
sys.path.insert(0, str(repo_root))

import importlib
from polar.storage import duckdb
from polar.storage import postgres
from polar.utils.config import load_configuration

# Reload the modules to ensure we have the latest changes
importlib.reload(duckdb)
importlib.reload(postgres)

# Load configuration
config = load_configuration()

# Define path to VO2max data
# The data file is in data/v02max_data.csv relative to repo root
v02max_data_path = Path('../data/v02max_data.csv')

# Get database type from configuration
db_type = config.get('DATABASE_TYPE', 'duckdb')

print(f"📊 Using {db_type.upper()} database")
print("="*60)

if db_type == 'postgres':
    from polar.storage.postgres import ensure_hr_zones_table, calculate_and_import_calories
    print("\n📈 Step 1: Importing HR zones to Postgres...")
    ensure_hr_zones_table(config)
    print("\n🔥 Step 2: Calculating and importing calorie data to Postgres...")
    calculate_and_import_calories(v02max_data_path, config)
else:  # default to duckdb
    from polar.storage.duckdb import ensure_hr_zones_table, calculate_and_import_calories
    print("\n📈 Step 1: Importing HR zones to DuckDB...")
    ensure_hr_zones_table(config)
    print("\n🔥 Step 2: Calculating and importing calorie data to DuckDB´...")
    calculate_and_import_calories(v02max_data_path, config)

print("\n" + "="*60)
print("✅ All imports completed successfully!")
```
