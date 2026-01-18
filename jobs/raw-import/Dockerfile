# Dockerfile for Raw Import Job
# Downloads TCX files from Polar API, converts to CSV, and uploads to Azure Storage

FROM python:3.11-slim

# Install system dependencies and uv
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && curl -LsSf https://astral.sh/uv/install.sh | sh

# Add uv to PATH
ENV PATH="/root/.cargo/bin:${PATH}"

# Set working directory
WORKDIR /app

# Copy the entire repository structure (needed for imports to work)
# This includes the polar module which is required
COPY . /app

# Change to the raw-import job directory
WORKDIR /app/jobs/raw-import

# Install dependencies using uv
# uv sync will create a virtual environment and install dependencies
RUN uv sync --no-dev

# The .env file and tokens_polar.json should be provided via volume mounts
# Example: docker run -v $(pwd)/.env:/app/jobs/raw-import/.env \
#                     -v $(pwd)/tokens_polar.json:/app/jobs/raw-import/tokens_polar.json \
#                     raw-import-job

# For production, you can also copy them during build (not recommended for secrets):
# COPY .env .env
# COPY tokens_polar.json tokens_polar.json

# Set environment variable to indicate we're in a container
ENV IN_CONTAINER=true

# Run the raw import job using uv
CMD ["uv", "run", "main.py"]

