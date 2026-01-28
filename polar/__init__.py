"""Polar AccessLink workout data package.

This package provides a comprehensive toolkit for:
- OAuth authentication with Polar AccessLink API
- User and exercise management
- TCX to CSV conversion with proper metadata
- Database imports (DuckDB and PostgreSQL)
- Workout data visualization
- Cloud storage integration (Azure)

The package is organized into functional submodules:
- api: Polar API interactions (users, exercises, oauth, tokens)
- storage: Database operations (DuckDB, PostgreSQL)
- converters: Data format conversion (TCX to CSV)
- ingest: Workout data ingestion and processing
- cloud: Cloud storage integrations (Azure)
- utils: Shared utilities (config, validations, rendering, common tools)

For backward compatibility, all functions are re-exported at the package level.
"""

__version__ = "0.2.0"

# Re-export configuration
from polar.utils.config import load_configuration

# Re-export common utilities
from polar.utils.common import get_field

# Re-export token management
from polar.api.tokens import (
    save_tokens,
    load_tokens,
    encode_credentials,
    exchange_code_for_token,
    refresh_access_token,
)

# Re-export user management
from polar.api.users import (
    get_user_info,
    register_user,
    get_physical_info,
)

# Re-export OAuth flow
from polar.api.oauth import (
    create_callback_handler,
    start_callback_server,
    run_authorization_flow,
    complete_token_exchange,
)

# Re-export exercise management
from polar.api.exercises import (
    generate_workout_id_from_start_time,
    normalize_start_time,
    list_exercises,
    download_tcx_and_convert_to_csv,
    filter_new_exercises,
)

# Re-export TCX conversion
from polar.converters.tcx import convert_tcx_to_csv

# Re-export Azure storage
from polar.cloud.azure import (
    is_azure_storage_enabled,
    upload_file_to_azure_storage,
)

# Re-export workflow orchestration
from polar.workflow import run_polar_workflow

__all__ = [
    # Configuration
    "load_configuration",
    # Common utilities
    "get_field",
    # Token management
    "save_tokens",
    "load_tokens",
    "encode_credentials",
    "exchange_code_for_token",
    "refresh_access_token",
    # User management
    "get_user_info",
    "register_user",
    "get_physical_info",
    # OAuth
    "create_callback_handler",
    "start_callback_server",
    "run_authorization_flow",
    "complete_token_exchange",
    # Exercise management
    "generate_workout_id_from_start_time",
    "normalize_start_time",
    "list_exercises",
    "download_tcx_and_convert_to_csv",
    "filter_new_exercises",
    # Conversion
    "convert_tcx_to_csv",
    # Azure storage
    "is_azure_storage_enabled",
    "upload_file_to_azure_storage",
    # Workflow
    "run_polar_workflow",
]
