"""Workflow tools for Polar AccessLink OAuth and Exercise Management.

This module serves as the main entry point and re-exports all functionality
from the refactored submodules:
- api.tokens: Token management (save, load, exchange, refresh)
- api.users: User management (registration, info, physical info, database)
- api.oauth: OAuth callback server and authorization flow
- converters.tcx: TCX to CSV conversion
- api.exercises: Exercise listing and downloading
- utils.validations: Token and environment validation
- cloud.azure: Azure Blob Storage upload functionality

All functions maintain backward compatibility with the original module.
"""
from __future__ import annotations

# Re-export configuration
from polar.utils.config import load_configuration

# Re-export token management
from polar.api.tokens import (
    save_tokens,
    load_tokens,
    encode_credentials,
    exchange_code_for_token,
    refresh_access_token,
)

# Re-export user management (API functions only)
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

# Re-export TCX conversion
from polar.converters.tcx import convert_tcx_to_csv

# Re-export exercise management
from polar.api.exercises import (
    generate_workout_id_from_start_time,
    normalize_start_time,
    list_exercises,
    display_exercises,
    download_tcx_and_convert_to_csv,
    filter_new_exercises,
)

# Re-export common tools
from polar.utils.common import get_field

# Re-export Azure Storage (optional)
from polar.cloud.azure import (
    is_azure_storage_enabled,
    get_azure_storage_config,
    upload_file_to_azure_storage,
    list_azure_storage_blobs,
)

# Standard library imports needed for run_polar_workflow
from pathlib import Path
from typing import Dict


def run_polar_workflow(
    config: Dict,
    timeout: int = 300
) -> Dict[str, object]:
    """Execute complete Polar AccessLink workflow.
    
    This function orchestrates the entire workflow:
    1. Validate configuration (tokens loaded from config or run authorization)
    2. Register user (idempotent)
    3. List and download all new exercises (not already in database)
    4. Upload CSV files to Azure Storage (if enabled)
    
    Args:
        config: Configuration dictionary from load_configuration()
            Should include ACCESS_TOKEN and TOKEN_TYPE if already authorized
        timeout: Timeout for authorization flow in seconds (default: 300)
    
    Returns:
        Dictionary containing:
            - config: Configuration dictionary
            - polar_user_id: Polar user ID
            - access_token: OAuth access token
            - exercises: List of all exercises
            - new_exercises: List of newly downloaded exercises
            - tcx_dataframes: List of DataFrames with trackpoint data
            - azure_uploads: List of Azure blob URLs (if enabled)
    
    Raises:
        ValueError: If configuration is invalid
        Exception: If any step fails
    """
    if config is None:
        raise ValueError("config parameter is required")
    
    print("="*80)
    print("POLAR ACCESSLINK COMPLETE WORKFLOW")
    print("="*80 + "\n")
    
    # Step 1: Use provided configuration
    print("Step 1: Using provided configuration...")
    output_dir = config['OUTPUT_DIR']
    print()
    
    # Check Azure Storage status
    azure_enabled = is_azure_storage_enabled()
    if azure_enabled:
        print("☁️ Azure Storage upload is ENABLED")
        azure_config = get_azure_storage_config()
        print(f"  - Storage Account: {azure_config['account_name']}")
        print(f"  - Container: {azure_config['container_name']}")
    else:
        print("ℹ️ Azure Storage upload is disabled")
    print()
    
    # Step 2: Check token validity and authorize if needed
    print("Step 2: Checking token validity...")
    access_token = config.get('ACCESS_TOKEN')
    if access_token:
        print("✅ Valid token found in configuration")
    else:
        print("⚠️  No valid token found. Starting authorization flow...")
        print()
        
        # Run authorization flow
        auth_code, redirect_uri = run_authorization_flow(
            client_id=config['CLIENT_ID'],
            redirect_port=config['REDIRECT_PORT'],
            allow_port_fallback=config['ALLOW_PORT_FALLBACK'],
            auth_url=config['AUTH_URL'],
            timeout=timeout
        )
        
        # Exchange code for tokens
        token_response = complete_token_exchange(
            auth_code=auth_code,
            redirect_uri=redirect_uri,
            client_id=config['CLIENT_ID'],
            client_secret=config['CLIENT_SECRET'],
            token_url=config['TOKEN_URL'],
            tokens_file=config.get('TOKENS_FILE')
        )
        
        access_token = token_response.get('access_token')
    
    print()
    
    # Step 3: Register user
    print("Step 3: Registering user...")
    polar_user_id = register_user(
        access_token=access_token,
        config=config,
        member_id=config['MEMBER_ID']
    )
    print()
    
    # Step 4: List and download all new exercises
    print("Step 4: Fetching and exporting new exercises...")
    
    if not polar_user_id:
        raise Exception("Polar user ID unavailable. Cannot proceed with exercise fetch.")
    
    # List exercises
    exercises = list_exercises(
        access_token=access_token,
        api_base=config['API_BASE']
    )
    
    downloaded_tcx_files = []
    processed_csv_files = []
    new_exercises = []
    azure_uploads = []
    
    if exercises:
        # Display all exercises
        display_exercises(exercises)
        
        # Filter to only new exercises (not already in database)
        new_exercises = filter_new_exercises(exercises, config)
        
        if new_exercises:
            print(f"\n✅ Found {len(new_exercises)} new exercise(s) to download")
            
            # Fetch user info ONCE before processing exercises
            print("\nFetching user info for CSV conversion parameters...")
            user_info = get_user_info(polar_user_id, access_token, config=config)
            
            # Extract user name with default
            name = "Anton Antonov "  # Default
            if user_info and 'first-name' in user_info and 'last-name' in user_info:
                name = f"{user_info.get('first-name', '')} {user_info.get('last-name', '')} "
                print(f"✅ User name: {name.strip()}")
            
            # Get physical information using dedicated function
            physical_info = get_physical_info(polar_user_id, access_token, config=config)
            
            # Extract parameters from physical_info
            weight = physical_info.get('weight', 0.0)
            height = physical_info.get('height', 0.0)
            hr_max = physical_info.get('maximum-heart-rate', 0)
            hr_sit = physical_info.get('resting-heart-rate', 0)
            vo2max = physical_info.get('vo2-max', 0)
            
            for i, exercise in enumerate(new_exercises, 1):
                exercise_id = get_field(exercise, 'id', 'exercise_id')
                start_time = get_field(exercise, 'start_time', 'start-time', 'local_start_time')
                
                print(f"\n--- Processing exercise {i}/{len(new_exercises)} ---")
                print(f"Exercise ID: {exercise_id}")
                print(f"Start Time: {start_time}")
                
                # Download TCX and convert to CSV
                result = download_tcx_and_convert_to_csv(
                    exercise_id=exercise_id,
                    access_token=access_token,
                    output_dir=output_dir,
                    name=name,
                    height=height,
                    weight=weight,
                    hr_max=hr_max,
                    hr_sit=hr_sit,
                    vo2max=vo2max,
                    api_base=config['API_BASE'],
                    start_time=start_time
                )
                
                if result is not None:
                    csv_path, tcx_path = result
                    downloaded_tcx_files.append(tcx_path)
                    processed_csv_files.append(csv_path)
                    
                    # Upload to Azure Storage if enabled
                    if azure_enabled and start_time:
                        # Generate workout ID for Azure blob name
                        workout_id = generate_workout_id_from_start_time(start_time)
                        
                        # Upload CSV to polar_csv folder
                        try:
                            csv_blob_name = f"polar_csv/{workout_id}.csv"
                            csv_blob_url = upload_file_to_azure_storage(csv_path, blob_name=csv_blob_name)
                            if csv_blob_url:
                                azure_uploads.append(csv_blob_url)
                        except Exception as e:
                            print(f"⚠️ Azure CSV upload failed for workout {workout_id}: {e}")
                            # Rename only the CSV file to indicate upload failure
                            csv_path_obj = Path(csv_path)
                            csv_failed_path = csv_path_obj.with_stem(csv_path_obj.stem + '_failed')
                            csv_path_obj.rename(csv_failed_path)
                            print(f"  Renamed CSV file to: {csv_failed_path.name}")
                        
                        # Upload TCX to polar_tcx folder
                        try:
                            tcx_blob_name = f"polar_tcx/{workout_id}.tcx"
                            tcx_blob_url = upload_file_to_azure_storage(tcx_path, blob_name=tcx_blob_name)
                            if tcx_blob_url:
                                azure_uploads.append(tcx_blob_url)
                        except Exception as e:
                            print(f"⚠️ Azure TCX upload failed for workout {workout_id}: {e}")
                            # Rename only the TCX file to indicate upload failure
                            tcx_path_obj = Path(tcx_path)
                            tcx_failed_path = tcx_path_obj.with_stem(tcx_path_obj.stem + '_failed')
                            tcx_path_obj.rename(tcx_failed_path)
                            print(f"  Renamed TCX file to: {tcx_failed_path.name}")
        else:
            print("\n⚠️ All exercises are already in the database. Nothing new to download.")
    else:
        print("⚠️ No exercises available to download.")
    
    print()
    print("="*80)
    print("✅ WORKFLOW COMPLETE")
    if downloaded_tcx_files:
        print(f"  Downloaded and processed {len(downloaded_tcx_files)} new exercise(s)")
    if azure_uploads:
        print(f"  Uploaded {len(azure_uploads)} file(s) (tcx and csv) to Azure Storage")
    print("="*80)
    
    return {
        'config': config,
        'polar_user_id': polar_user_id,
        'access_token': access_token,
        'exercises': exercises,
        'new_exercises': new_exercises,
        'downloaded_tcx_files': downloaded_tcx_files,
        'processed_csv_files': processed_csv_files,
        'azure_uploads': azure_uploads,
    }


__all__ = [
    # Token management
    'save_tokens',
    'load_tokens',
    'encode_credentials',
    'exchange_code_for_token',
    'refresh_access_token',

    # User management (API functions)
    'get_user_info',
    'register_user',
    'get_physical_info',

    # Exercise management
    'generate_workout_id_from_start_time',
    'normalize_start_time',
    'list_exercises',
    'display_exercises',
    'download_tcx_and_convert_to_csv',
    'filter_new_exercises',

    # Azure Storage
    'is_azure_storage_enabled',
    'get_azure_storage_config',
    'upload_file_to_azure_storage',
    'list_azure_storage_blobs',

    # Complete workflow
    'run_polar_workflow',
]
