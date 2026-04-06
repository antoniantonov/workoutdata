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

# Polar Download → CSV → Azure Upload

## Overview
This notebook downloads **all** exercises from Polar AccessLink API, converts each TCX file to
Polar-compatible CSV, and uploads both TCX and CSV to Azure Blob Storage.

## What This Notebook Does
Executes a single function call that orchestrates:
1. **OAuth Authorization**: Handles token management (loads existing or runs OAuth flow)
2. **User Registration**: Registers user with Polar API (idempotent operation)
3. **User Info Retrieval**: Fetches physical parameters (weight, height, HR max, VO2max) for CSV metadata
4. **Exercise Download**: Downloads ALL exercises as TCX (no deduplication)
5. **TCX → CSV Conversion**: Converts each TCX file to Polar-compatible CSV format
6. **Azure Upload**: Uploads both TCX and CSV files to Azure Blob Storage

## Prerequisites
- Polar API credentials set in `.env` (POLAR_CLIENT_ID, POLAR_CLIENT_SECRET)
- Azure Storage enabled: `AZURE_STORAGE_ENABLED=true`, `AZURE_STORAGE_ACCOUNT_NAME=<account>`
- Authenticated via `az login` (local) or Managed Identity (Azure)

## Key Differences from `polar_accesslink_workflow_v0.2.md`
- **No database operations** — does not read from or write to DuckDB/PostgreSQL
- **No deduplication** — downloads every exercise every time
- **Azure required** — raises an error if Azure Storage is not enabled
- Uses `run_polar_download_and_upload()` from `polar/workflow.py`

+++

## Workflow Execution

This single cell runs the entire download-and-upload workflow.

```{code-cell}
import sys
from pathlib import Path

# Add repo root to path
repo_root = Path.cwd().parent if 'notebooks' in str(Path.cwd()) else Path.cwd()
sys.path.insert(0, str(repo_root))

import importlib
from polar import workflow
importlib.reload(workflow)

from polar.workflow import run_polar_download_and_upload
from polar.utils.config import load_configuration

# Load configuration
config = load_configuration()

# Execute the download-and-upload workflow
# This function handles:
# - OAuth token validation and authorization (if needed)
# - User registration with Polar API
# - User info retrieval (weight, height, HR max for CSV conversion)
# - Download ALL exercises as TCX
# - TCX to CSV conversion with proper metadata
# - Upload both TCX and CSV to Azure Blob Storage
result = run_polar_download_and_upload(
    config=config,
    timeout=300  # 5 minute timeout for authorization flow
)

# Display summary
exercises = result['exercises']
downloaded_tcx_files = result['downloaded_tcx_files']
processed_csv_files = result['processed_csv_files']
azure_uploads = result['azure_uploads']

print(f"\n📊 Summary:")
print(f"  - Total exercises from Polar: {len(exercises)}")
print(f"  - Downloaded TCX files: {len(downloaded_tcx_files)}")
print(f"  - Converted CSV files: {len(processed_csv_files)}")
print(f"  - Azure uploads: {len(azure_uploads)}")

if azure_uploads:
    print(f"\n☁️ Uploaded blobs:")
    for url in azure_uploads:
        print(f"  - {url}")
```
