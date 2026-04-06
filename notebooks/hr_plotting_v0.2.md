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

# Heart Rate Workout Visualization

This notebook provides interactive visualizations for heart rate workout data stored in DuckDB.

## Features

- **Line Plot with HR Zones**: Visualize heart rate over time with colored background zones
- **Pie Chart**: Show time distribution across different HR zones with calorie calculations
- **Multi-workout Comparison**: Compare multiple workouts on the same plot

## Data Sources

- **Database**: `../hr_data/database_v2.duckdb`
  - `timeseries` table: Per-second HR measurements
  - `workout_metadata` table: Workout summary info
  - `calories_per_hr` table: Calorie burn rates by HR
- **Zones**: `../hr_data/zones.csv` defines HR zone boundaries

## Usage

1. Run the import cell to load the rendering functions
2. Set workout IDs in the visualization cells
3. Toggle `usePlot` and `usePieChart` flags as needed

```{code-cell} ipython3
import sys
from pathlib import Path

# Add src to path
repo_root = Path.cwd().parent if 'notebooks' in str(Path.cwd()) else Path.cwd()
sys.path.insert(0, str(repo_root))

from polar.utils import rendering as rendering_tools
import importlib
importlib.reload(rendering_tools)

# Import rendering functions and config
from polar.utils.rendering import plot_hr_with_zones
from polar.utils.config import load_configuration

# Load configuration
config = load_configuration()

workout_ids = [
    '25-01-2026_111846'
]
plot_hr_with_zones(workout_ids, config)
```

```{code-cell} ipython3
import sys
from pathlib import Path

# Add src to path
repo_root = Path.cwd().parent if 'notebooks' in str(Path.cwd()) else Path.cwd()
sys.path.insert(0, str(repo_root))

from polar.utils import rendering as rendering_tools
import importlib
importlib.reload(rendering_tools)

# Import rendering functions and config
from polar.utils.rendering import plot_hr_with_zones, piechart_hr_with_zones
from polar.utils.config import load_configuration

# Load configuration (uses environment variables, defaults to DuckDB)
config = load_configuration()

usePieChart = True
usePlot = True
# List of workout IDs
workout_ids = [
    '25-01-2026_111846',
]

# Loop through each workout ID and plot
for workoutId in workout_ids:
    if usePlot:
        plot_hr_with_zones(workoutId, config)
    if usePieChart:
        piechart_hr_with_zones(workoutId, config)
```
