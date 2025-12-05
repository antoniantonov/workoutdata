"""Azure Blob Storage utilities for workout data.

This module provides functionality to upload workout CSV files to Azure Blob Storage
using DefaultAzureCredential for authentication, which supports:
- Azure CLI credentials (when running locally after `az login`)
- Managed Identity (when running in Azure)
- Environment variables (AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, AZURE_TENANT_ID)

Configuration is loaded from environment variables:
- AZURE_STORAGE_ACCOUNT_NAME: Name of the Azure Storage account
- AZURE_STORAGE_CONTAINER_NAME: Name of the blob container (default: 'workout-data')
- AZURE_STORAGE_ENABLED: Set to 'true' to enable uploads (default: 'false')
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

# Azure SDK imports - these are optional dependencies
try:
    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import BlobServiceClient, ContentSettings
    AZURE_SDK_AVAILABLE = True
except ImportError:
    AZURE_SDK_AVAILABLE = False
    DefaultAzureCredential = None
    BlobServiceClient = None
    ContentSettings = None


def is_azure_storage_enabled() -> bool:
    """Check if Azure Storage upload is enabled.
    
    Returns:
        True if AZURE_STORAGE_ENABLED is 'true' and required config is set
    """
    enabled = os.getenv('AZURE_STORAGE_ENABLED', 'false').lower() == 'true'
    account_name = os.getenv('AZURE_STORAGE_ACCOUNT_NAME')
    
    return enabled and bool(account_name) and AZURE_SDK_AVAILABLE


def get_azure_storage_config() -> dict:
    """Get Azure Storage configuration from environment variables.
    
    Returns:
        Dictionary with storage configuration:
            - account_name: Azure Storage account name
            - container_name: Blob container name
            - enabled: Whether uploads are enabled
    
    Raises:
        ValueError: If required configuration is missing when enabled
    """
    enabled = os.getenv('AZURE_STORAGE_ENABLED', 'false').lower() == 'true'
    account_name = os.getenv('AZURE_STORAGE_ACCOUNT_NAME')
    container_name = os.getenv('AZURE_STORAGE_CONTAINER_NAME', 'workout-data')
    
    if enabled and not account_name:
        raise ValueError(
            "AZURE_STORAGE_ENABLED is true but AZURE_STORAGE_ACCOUNT_NAME is not set. "
            "Please set the storage account name or disable Azure Storage uploads."
        )
    
    if enabled and not AZURE_SDK_AVAILABLE:
        raise ValueError(
            "AZURE_STORAGE_ENABLED is true but Azure SDK is not installed. "
            "Please install azure-storage-blob and azure-identity packages:\n"
            "  pip install azure-storage-blob azure-identity"
        )
    
    return {
        'account_name': account_name,
        'container_name': container_name,
        'enabled': enabled,
    }


def get_blob_service_client() -> 'BlobServiceClient':
    """Create a BlobServiceClient using DefaultAzureCredential.
    
    Uses DefaultAzureCredential which supports:
    - Azure CLI credentials (local development after `az login`)
    - Managed Identity (Azure VMs, App Service, Functions)
    - Environment variables (AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, AZURE_TENANT_ID)
    
    Returns:
        BlobServiceClient configured with DefaultAzureCredential
    
    Raises:
        ValueError: If Azure SDK is not available or storage account not configured
    """
    if not AZURE_SDK_AVAILABLE:
        raise ValueError(
            "Azure SDK is not installed. "
            "Please install azure-storage-blob and azure-identity packages:\n"
            "  pip install azure-storage-blob azure-identity"
        )
    
    config = get_azure_storage_config()
    
    if not config['account_name']:
        raise ValueError("AZURE_STORAGE_ACCOUNT_NAME environment variable is not set")
    
    account_url = f"https://{config['account_name']}.blob.core.windows.net"
    
    # Use DefaultAzureCredential for flexible authentication
    credential = DefaultAzureCredential()
    
    return BlobServiceClient(account_url=account_url, credential=credential)


def upload_csv_to_azure(
    csv_path: Path,
    blob_name: Optional[str] = None,
    container_name: Optional[str] = None,
    overwrite: bool = True
) -> Optional[str]:
    """Upload a CSV file to Azure Blob Storage.
    
    Args:
        csv_path: Path to the CSV file to upload
        blob_name: Name for the blob in storage (default: filename from csv_path)
        container_name: Container name (default: from AZURE_STORAGE_CONTAINER_NAME)
        overwrite: Whether to overwrite existing blob (default: True)
    
    Returns:
        URL of the uploaded blob, or None if upload is disabled/fails
    
    Raises:
        FileNotFoundError: If the CSV file doesn't exist
        ValueError: If Azure Storage is not properly configured
    """
    # Check if Azure Storage is enabled
    if not is_azure_storage_enabled():
        print("ℹ️ Azure Storage upload is disabled. Set AZURE_STORAGE_ENABLED=true to enable.")
        return None
    
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")
    
    config = get_azure_storage_config()
    
    # Use provided container or default from config
    if container_name is None:
        container_name = config['container_name']
    
    # Use provided blob name or default to filename
    if blob_name is None:
        blob_name = csv_path.name
    
    try:
        # Get blob service client
        blob_service_client = get_blob_service_client()
        
        # Get container client
        container_client = blob_service_client.get_container_client(container_name)
        
        # Ensure container exists
        try:
            container_client.create_container()
            print(f"✅ Created container: {container_name}")
        except Exception as e:
            # Container may already exist, which is fine
            if "ContainerAlreadyExists" not in str(e):
                # Log but continue - container might exist
                pass
        
        # Get blob client
        blob_client = container_client.get_blob_client(blob_name)
        
        # Set content settings for CSV
        content_settings = ContentSettings(
            content_type='text/csv',
            content_encoding='utf-8'
        )
        
        # Upload the file
        print(f"📤 Uploading {csv_path.name} to Azure Blob Storage...")
        
        with open(csv_path, 'rb') as data:
            blob_client.upload_blob(
                data,
                overwrite=overwrite,
                content_settings=content_settings
            )
        
        blob_url = blob_client.url
        print(f"✅ Uploaded to Azure: {blob_url}")
        
        return blob_url
        
    except Exception as e:
        print(f"❌ Failed to upload to Azure Blob Storage: {e}")
        raise


def upload_workout_csv(
    csv_path: Path,
    workout_id: Optional[str] = None,
    subfolder: str = "workouts"
) -> Optional[str]:
    """Upload a workout CSV file to Azure Blob Storage with organized folder structure.
    
    Uploads to: {container}/{subfolder}/{workout_id or filename}
    
    Args:
        csv_path: Path to the workout CSV file
        workout_id: Workout ID to use in blob name (default: extracted from filename)
        subfolder: Subfolder in container (default: 'workouts')
    
    Returns:
        URL of the uploaded blob, or None if upload is disabled/fails
    """
    if not is_azure_storage_enabled():
        return None
    
    # Construct blob name with folder structure
    if workout_id:
        blob_name = f"{subfolder}/{workout_id}.csv"
    else:
        blob_name = f"{subfolder}/{csv_path.name}"
    
    return upload_csv_to_azure(csv_path, blob_name=blob_name)


def list_workout_blobs(
    container_name: Optional[str] = None,
    prefix: str = "workouts/"
) -> list:
    """List workout CSV blobs in Azure Storage.
    
    Args:
        container_name: Container name (default: from config)
        prefix: Blob name prefix to filter by (default: 'workouts/')
    
    Returns:
        List of blob names matching the prefix
    """
    if not is_azure_storage_enabled():
        print("ℹ️ Azure Storage is disabled. Returning empty list.")
        return []
    
    config = get_azure_storage_config()
    
    if container_name is None:
        container_name = config['container_name']
    
    try:
        blob_service_client = get_blob_service_client()
        container_client = blob_service_client.get_container_client(container_name)
        
        blobs = container_client.list_blobs(name_starts_with=prefix)
        return [blob.name for blob in blobs]
        
    except Exception as e:
        print(f"❌ Failed to list blobs: {e}")
        return []


__all__ = [
    'is_azure_storage_enabled',
    'get_azure_storage_config',
    'get_blob_service_client',
    'upload_csv_to_azure',
    'upload_workout_csv',
    'list_workout_blobs',
    'AZURE_SDK_AVAILABLE',
]
