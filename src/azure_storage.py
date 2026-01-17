"""Azure Blob Storage utilities for workout data.

This module provides functionality to upload files to Azure Blob Storage
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
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings


def is_azure_storage_enabled() -> bool:
    """Check if Azure Storage upload is enabled.
    
    Returns:
        True if AZURE_STORAGE_ENABLED is 'true' and required config is set
    """
    enabled = os.getenv('AZURE_STORAGE_ENABLED', 'false').lower() == 'true'
    account_name = os.getenv('AZURE_STORAGE_ACCOUNT_NAME')
    
    return enabled and bool(account_name)


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
    
    return {
        'account_name': account_name,
        'container_name': container_name,
        'enabled': enabled,
    }


def get_blob_service_client() -> BlobServiceClient:
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
    
    config = get_azure_storage_config()
    
    if not config['account_name']:
        raise ValueError("AZURE_STORAGE_ACCOUNT_NAME environment variable is not set")
    
    account_url = f"https://{config['account_name']}.blob.core.windows.net"
    
    # Use DefaultAzureCredential for flexible authentication
    credential = DefaultAzureCredential()
    
    return BlobServiceClient(account_url=account_url, credential=credential)


def upload_file_to_azure_storage(
    file_path: Path,
    blob_name: Optional[str] = None,
    container_name: Optional[str] = None,
    overwrite: bool = True
) -> Optional[str]:
    """Upload a file to Azure Blob Storage.
    
    Args:
        file_path: Path to the file to upload
        blob_name: Name for the blob in storage (default: filename from file_path)
        container_name: Container name (default: from AZURE_STORAGE_CONTAINER_NAME)
        overwrite: Whether to overwrite existing blob (default: True)
    
    Returns:
        URL of the uploaded blob, or None if upload is disabled/fails
    
    Raises:
        FileNotFoundError: If the file doesn't exist
        ValueError: If Azure Storage is not properly configured
    """
    # Check if Azure Storage is enabled
    if not is_azure_storage_enabled():
        print("⚠️ Azure Storage upload is disabled. Set AZURE_STORAGE_ENABLED=true to enable.")
        return None
    
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")
    
    config = get_azure_storage_config()
    
    # Use provided container or default from config
    if container_name is None:
        container_name = config['container_name']
    
    # Use provided blob name or default to filename
    if blob_name is None:
        blob_name = file_path.name
    
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
        
        # Determine content type based on file extension
        file_extension = file_path.suffix.lower()
        if file_extension == '.tcx' or file_extension == '.xml':
            content_type = 'application/xml'
        elif file_extension == '.csv':
            content_type = 'text/csv'
        elif file_extension == '.duckdb' or file_extension == '.db':
            content_type = 'application/x-duckdb'
        else:
            content_type = 'application/octet-stream'  # Generic binary for unknown types
        
        # Set content settings based on file type
        content_settings = ContentSettings(
            content_type=content_type,
            content_encoding='utf-8' if file_extension in ['.csv', '.tcx', '.xml'] else None
        )
        
        # Upload the file
        print(f"📤 Uploading {file_path.name} to Azure Blob Storage...")
        
        with open(file_path, 'rb') as data:
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


def list_azure_storage_blobs(
    container_name: Optional[str] = None,
    prefix: str = "workouts/"
) -> list:
    """List blobs in Azure Storage.
    
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
    'upload_file_to_azure_storage',
    'list_azure_storage_blobs',
]
