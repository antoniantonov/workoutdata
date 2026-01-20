"""Configuration management for workout data project.

This module centralizes all configuration loading from environment variables,
including paths, database connections, API credentials, and Azure Storage settings.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict

def load_configuration() -> Dict[str, object]:
    """Load configuration from environment variables.
    
    Loads all project configuration including:
    - Polar API credentials and settings
    - Database paths
    - File paths for tokens and data files
    - Output directories
    - Azure Storage settings (optional)
    
    Returns:
        Dict containing all configuration values:
            Polar API Settings:
            - CLIENT_ID: Polar API client ID
            - CLIENT_SECRET: Polar API client secret
            - REDIRECT_PORT: Port for OAuth callback (default: 5000)
            - MEMBER_ID: Optional Polar member ID
            - AUTH_URL: Polar authorization URL (env: POLAR_AUTH_URL)
            - TOKEN_URL: Polar token exchange URL (env: POLAR_TOKEN_URL)
            - API_BASE: Polar API base URL (env: POLAR_API_BASE)
            - ALLOW_PORT_FALLBACK: Whether to try alternative ports
            
            File Paths:
            - TOKENS_FILE: Path to token storage file
            - DUCKDB_PATH: Path to DuckDB database file
            - VO2MAX_DATA_PATH: Path to VO2max data CSV file
            - ZONES_CSV_PATH: Path to HR zones CSV file
            - OUTPUT_DIR: Directory for output files (TCX, CSV exports)
            
            PostgreSQL Database (optional):
            - POSTGRES_CONNECTION_STRING: Full PostgreSQL connection string (preferred)
            - POSTGRES_HOST: PostgreSQL server hostname
            - POSTGRES_PORT: PostgreSQL server port (default: 5432)
            - POSTGRES_DATABASE: Database name (default: 'workoutdata')
            - POSTGRES_USER: PostgreSQL username
            - POSTGRES_PASSWORD: PostgreSQL password
            
            Azure Storage (optional):
            - AZURE_STORAGE_ENABLED: Whether Azure Storage upload is enabled
            - AZURE_STORAGE_ACCOUNT_NAME: Azure Storage account name
            - AZURE_STORAGE_CONTAINER_NAME: Blob container name (default: 'workout-data')
    
    Raises:
        ValueError: If required environment variables are missing
    """
    # Optional: Load from .env file if python-dotenv is available
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    # =============================================================================
    # Polar API Configuration
    # =============================================================================
    CLIENT_ID = os.getenv('POLAR_CLIENT_ID')
    CLIENT_SECRET = os.getenv('POLAR_CLIENT_SECRET')
    REDIRECT_PORT = int(os.getenv('POLAR_REDIRECT_PORT', '5000'))
    MEMBER_ID = os.getenv('POLAR_MEMBER_ID')
    ALLOW_PORT_FALLBACK = os.getenv('ALLOW_PORT_FALLBACK', 'true').lower() == 'true'

    # Validate required Polar API variables
    missing_vars = []
    if not CLIENT_ID:
        missing_vars.append('POLAR_CLIENT_ID')
    if not CLIENT_SECRET:
        missing_vars.append('POLAR_CLIENT_SECRET')

    if missing_vars:
        raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}")

    # API endpoints (with environment variable overrides)
    AUTH_URL = os.getenv('POLAR_AUTH_URL', "https://flow.polar.com/oauth2/authorization")
    TOKEN_URL = os.getenv('POLAR_TOKEN_URL', "https://polarremote.com/v2/oauth2/token")
    API_BASE = os.getenv('POLAR_API_BASE', "https://www.polaraccesslink.com/v3")

    # =============================================================================
    # File Paths Configuration
    # =============================================================================
    
    # Get base directory (parent of src directory)
    base_dir = Path(__file__).parent.parent
    
    # Token storage file
    tokens_file_env = os.getenv('POLAR_TOKENS_FILE')
    if tokens_file_env:
        TOKENS_FILE = Path(tokens_file_env)
        if not TOKENS_FILE.is_absolute():
            TOKENS_FILE = base_dir / TOKENS_FILE
    else:
        TOKENS_FILE = base_dir / "notebooks" / "tokens_polar.json"

    # DuckDB database path
    duckdb_path_env = os.getenv('DUCKDB_PATH')
    if duckdb_path_env:
        DUCKDB_PATH = Path(duckdb_path_env)
        if not DUCKDB_PATH.is_absolute():
            DUCKDB_PATH = base_dir / DUCKDB_PATH
    else:
        DUCKDB_PATH = base_dir / "hr_data" / "database_v2.duckdb"

    # VO2max data file
    vo2max_path_env = os.getenv('VO2MAX_DATA_PATH')
    if vo2max_path_env:
        VO2MAX_DATA_PATH = Path(vo2max_path_env)
        if not VO2MAX_DATA_PATH.is_absolute():
            VO2MAX_DATA_PATH = base_dir / VO2MAX_DATA_PATH
    else:
        VO2MAX_DATA_PATH = base_dir / "data" / "v02max_data.csv"

    # HR zones data file
    zones_csv_path_env = os.getenv('ZONES_CSV_PATH')
    if zones_csv_path_env:
        ZONES_CSV_PATH = Path(zones_csv_path_env)
        if not ZONES_CSV_PATH.is_absolute():
            ZONES_CSV_PATH = base_dir / ZONES_CSV_PATH
    else:
        ZONES_CSV_PATH = base_dir / "hr_data" / "zones.csv"

    # Output directory for exercise files
    output_dir_env = os.getenv('OUTPUT_DIR')
    if output_dir_env:
        OUTPUT_DIR = Path(output_dir_env)
        if not OUTPUT_DIR.is_absolute():
            OUTPUT_DIR = base_dir / OUTPUT_DIR
    else:
        OUTPUT_DIR = base_dir / "hr_data"

    # Ensure output directory exists
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # =============================================================================
    # PostgreSQL Database Configuration (optional)
    # =============================================================================
    # Support both connection string or individual parameters
    POSTGRES_CONNECTION_STRING = os.getenv('POSTGRES_CONNECTION_STRING')
    POSTGRES_HOST = os.getenv('POSTGRES_HOST')
    POSTGRES_PORT = os.getenv('POSTGRES_PORT', '5432')
    POSTGRES_DATABASE = os.getenv('POSTGRES_DATABASE', 'workoutdata')
    POSTGRES_USER = os.getenv('POSTGRES_USER')
    POSTGRES_PASSWORD = os.getenv('POSTGRES_PASSWORD')

    # =============================================================================
    # Database Type Configuration
    # =============================================================================
    # Controls which database backend to use for imports
    # Valid values: 'duckdb' (default) or 'postgres'
    DATABASE_TYPE = os.getenv('DATABASE_TYPE', 'duckdb').lower()
    if DATABASE_TYPE not in ['duckdb', 'postgres']:
        raise ValueError(f"Invalid DATABASE_TYPE '{DATABASE_TYPE}'. Must be 'duckdb' or 'postgres'")

    # =============================================================================
    # Azure Storage Configuration (optional)
    # =============================================================================
    AZURE_STORAGE_ENABLED = os.getenv('AZURE_STORAGE_ENABLED', 'false').lower() == 'true'
    AZURE_STORAGE_ACCOUNT_NAME = os.getenv('AZURE_STORAGE_ACCOUNT_NAME')
    AZURE_STORAGE_CONTAINER_NAME = os.getenv('AZURE_STORAGE_CONTAINER_NAME', 'workout-data')

    print(f"✅ Configuration loaded")
    print(f"  - Client ID: {CLIENT_ID[:8]}...")
    print(f"  - Redirect Port: {REDIRECT_PORT}")
    print(f"  - Member ID: {MEMBER_ID if MEMBER_ID else 'Not set (will be obtained)'}")
    print(f"  - Database Type: {DATABASE_TYPE.upper()}")
    print(f"  - DuckDB Path: {DUCKDB_PATH}")
    print(f"  - Tokens File: {TOKENS_FILE}")
    print(f"  - VO2max Data: {VO2MAX_DATA_PATH}")
    print(f"  - Zones CSV: {ZONES_CSV_PATH}")
    print(f"  - Output Dir: {OUTPUT_DIR}")
    if POSTGRES_CONNECTION_STRING or (POSTGRES_HOST and POSTGRES_USER and POSTGRES_PASSWORD):
        print(f"  - PostgreSQL: {POSTGRES_HOST or 'via connection string'}/{POSTGRES_DATABASE}")
    else:
        print(f"  - PostgreSQL: Not configured")
    if AZURE_STORAGE_ENABLED:
        print(f"  - Azure Storage: {AZURE_STORAGE_ACCOUNT_NAME}/{AZURE_STORAGE_CONTAINER_NAME}")
    else:
        print(f"  - Azure Storage: Disabled")

    return {
        # Polar API
        'CLIENT_ID': CLIENT_ID,
        'CLIENT_SECRET': CLIENT_SECRET,
        'REDIRECT_PORT': REDIRECT_PORT,
        'MEMBER_ID': MEMBER_ID,
        'AUTH_URL': AUTH_URL,
        'TOKEN_URL': TOKEN_URL,
        'API_BASE': API_BASE,
        'ALLOW_PORT_FALLBACK': ALLOW_PORT_FALLBACK,

        # Database Configuration
        'DATABASE_TYPE': DATABASE_TYPE,

        # File Paths
        'TOKENS_FILE': TOKENS_FILE,
        'DUCKDB_PATH': DUCKDB_PATH,
        'VO2MAX_DATA_PATH': VO2MAX_DATA_PATH,
        'ZONES_CSV_PATH': ZONES_CSV_PATH,
        'OUTPUT_DIR': OUTPUT_DIR,

        # PostgreSQL Database (optional)
        'POSTGRES_CONNECTION_STRING': POSTGRES_CONNECTION_STRING,
        'POSTGRES_HOST': POSTGRES_HOST,
        'POSTGRES_PORT': POSTGRES_PORT,
        'POSTGRES_DATABASE': POSTGRES_DATABASE,
        'POSTGRES_USER': POSTGRES_USER,
        'POSTGRES_PASSWORD': POSTGRES_PASSWORD,

        # Azure Storage (optional)
        'AZURE_STORAGE_ENABLED': AZURE_STORAGE_ENABLED,
        'AZURE_STORAGE_ACCOUNT_NAME': AZURE_STORAGE_ACCOUNT_NAME,
        'AZURE_STORAGE_CONTAINER_NAME': AZURE_STORAGE_CONTAINER_NAME,
    }


__all__ = ['load_configuration']
