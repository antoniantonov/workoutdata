# Dockerfile for Polar Import Job (Full Workflow)
# Downloads TCX from Polar API, converts to CSV, uploads to Azure,
# imports into database (DuckDB or PostgreSQL), and cleans up.

FROM python:3.14-slim

# Install system dependencies, Azure CLI, and uv
RUN apt-get update && apt-get install -y \
    curl \
    ca-certificates \
    apt-transport-https \
    lsb-release \
    gnupg \
    && curl -sL https://aka.ms/InstallAzureCLIDeb | bash \
    && rm -rf /var/lib/apt/lists/* \
    && curl -LsSf https://astral.sh/uv/install.sh | sh

# Add uv to PATH
ENV PATH="/root/.local/bin:/root/.cargo/bin:${PATH}"

# Set working directory to repository root
WORKDIR /app

# Copy the entire repository structure (needed for polar module imports)
COPY . /app

# Install dependencies from the import job's pyproject.toml
WORKDIR /app/jobs/import
RUN uv sync --no-dev

# Switch back to repo root as working directory.
# config.py resolves paths relative to both cwd and __file__,
# so running from repo root ensures consistent path resolution.
WORKDIR /app

# Set environment variable to indicate we're in a container
ENV IN_CONTAINER=true
# Ensure Python output is sent straight to the container logs
ENV PYTHONUNBUFFERED=1

# Run the import job using uv (with pyproject.toml from jobs/import)
CMD ["uv", "run", "--project", "/app/jobs/import", "python", "/app/jobs/import/main.py"]
