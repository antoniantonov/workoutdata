# Dockerfile for the Garmin Import Job.
# Transforms GarminDB SQLite data into DuckDB / PostgreSQL (garmin_ tables).
# The live-download and Dockerized authentication commands require the
# 'download' extra (garmindb + garth).

FROM python:3.14-slim

# Install uv (fast Python package manager).
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && curl -LsSf https://astral.sh/uv/install.sh | sh

ENV PATH="/root/.local/bin:/root/.cargo/bin:${PATH}"

WORKDIR /app

# Copy the self-contained job (build context = this job directory).
COPY . /app

# Install all runtime dependencies, including live download/authentication.
RUN uv sync --no-dev --extra download

# Cache the DuckDB UI extension in the image so the UI service has no
# extension-download dependency at startup.
RUN uv run python -c "import duckdb; duckdb.connect().execute('INSTALL ui')"

# Ensure logs stream straight to the container output.
ENV PYTHONUNBUFFERED=1
ENV IN_CONTAINER=true

CMD ["uv", "run", "python", "/app/main.py"]
