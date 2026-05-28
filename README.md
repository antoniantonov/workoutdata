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

## Devcontainer

This repo includes a devcontainer setup for the Polar and Garmin Python/notebook workflows. VS Code auto-detects `.devcontainer/devcontainer.json`. It uses the Microsoft Python 3.14 Bookworm image and manages Python dependencies with `uv`.

Open the repository in VS Code and run **Dev Containers: Reopen in Container**. If you use the Dev Containers CLI directly, point it at `.devcontainer/devcontainer.json`.

Before starting the container, make sure the env files exist:

```bash
cp notebooks/.env.sample notebooks/.env
cp jobs/import/.env.example jobs/import/.env
```

The devcontainer also creates missing env files from those samples during initialization so Docker can pass them with `--env-file`. Edit those files with your local Polar, database, Azure, and Garmin settings. The notebooks still load `notebooks/.env` with `python-dotenv` when run outside the container.

The post-create setup runs:

```bash
uv sync --project .devcontainer
```

This creates `.devcontainer/.venv`, installs the editable local Polar and Garmin packages, and registers a Jupyter kernel named `Python (workoutdata devcontainer)`.

Useful checks inside the container:

```bash
uv run --project .devcontainer python --version
uv run --project .devcontainer python -c "import pandas, duckdb, plotly; import polar"
uv run --project .devcontainer python -m ipykernel --version
```

The existing root PostgreSQL `docker-compose.yml` and `jobs/import/` Docker workflow remain separate from the devcontainer.

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