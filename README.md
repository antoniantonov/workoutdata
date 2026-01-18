# workoutdata

Code to do some data analysis and plotting of heart rate data during exercise. Keep that heart rate up, folks! 

## Package Structure

The repository uses a modular `polar` package organized by functionality:

- **polar/api/** - Polar AccessLink API interactions (OAuth, users, exercises)
- **polar/storage/** - Database operations (DuckDB, PostgreSQL)
- **polar/converters/** - Data format conversion (TCX to CSV)
- **polar/ingest/** - Workout data ingestion and processing
- **polar/cloud/** - Cloud storage integrations (Azure)
- **polar/utils/** - Shared utilities (config, validations, rendering)

See [QUICKSTART.md](docs/QUICKSTART.md) for detailed usage instructions.

## Quick Start

```python
from polar import run_polar_workflow
from polar.storage import duckdb

# Run the complete workflow
result = run_polar_workflow(config=config, tokens_file="tokens.json")

# Import workouts to database
summary = duckdb.import_workout_from_directory(["*.CSV"], config)
```

# DuckDB

- To use the web app in a browser run this command in the terminal:
```bash
duckdb -ui
```