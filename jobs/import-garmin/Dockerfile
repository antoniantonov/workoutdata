# Dockerfile for the Garmin Import Job.
# Transforms GarminDB SQLite data into DuckDB / PostgreSQL (garmin_ tables).
# The optional live-download phase (GARMIN_DOWNLOAD=true) additionally needs the
# 'download' extra (garmindb); enable it by switching the uv sync line below.

FROM python:3.11-slim

# Install uv (fast Python package manager).
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && curl -LsSf https://astral.sh/uv/install.sh | sh

ENV PATH="/root/.local/bin:/root/.cargo/bin:${PATH}"

WORKDIR /app

# Copy the self-contained job (build context = this job directory).
COPY . /app

# Install dependencies. The 'download' extra (garmindb) is required for the live
# download phase (GARMIN_DOWNLOAD=true). For transform-only, you may drop it:
#   RUN uv sync --no-dev
RUN uv sync --no-dev --extra download

# Ensure logs stream straight to the container output.
ENV PYTHONUNBUFFERED=1
ENV IN_CONTAINER=true

CMD ["uv", "run", "python", "/app/main.py"]
