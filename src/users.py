"""User management for Polar AccessLink API.

This module provides utilities for managing user information including:
- User registration with Polar API
- Fetching user info and physical info
- Workout ID generation from timestamps

Database operations are delegated to database-specific modules:
- duckdb_import for DuckDB operations
- postgresdb_import for PostgreSQL operations
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import requests  # type: ignore

from common_tools import get_field


# =============================================================================
# Database Delegation Helpers
# =============================================================================

def _get_db_module(config: Dict):
    """Get appropriate database module based on config.
    
    Args:
        config: Configuration dictionary with DATABASE_TYPE key
    
    Returns:
        Either duckdb_import or postgresdb_import module
    """
    db_type = config.get('DATABASE_TYPE', 'duckdb')
    
    if db_type == 'postgres':
        import postgresdb_import
        return postgresdb_import
    else:
        import duckdb_import
        return duckdb_import


def _get_db_context(config: Dict):
    """Get database connection context based on config.
    
    Args:
        config: Configuration dictionary
    
    Returns:
        Database connection context (Path for DuckDB, Connection for PostgreSQL)
    """
    db_type = config.get('DATABASE_TYPE', 'duckdb')
    
    if db_type == 'postgres':
        import postgresdb_import
        return postgresdb_import.get_postgres_connection(config)
    else:
        return config.get('DUCKDB_PATH')


def _close_db_context(db_context, config: Dict):
    """Close database context if needed (only for PostgreSQL).
    
    Args:
        db_context: Database connection context
        config: Configuration dictionary
    """
    db_type = config.get('DATABASE_TYPE', 'duckdb')
    
    if db_type == 'postgres' and db_context:
        db_context.close()


# =============================================================================
# User Management Functions
# =============================================================================

def get_user_info(
    member_or_user_id: str,
    access_token: str,
    config: Dict
) -> Optional[Dict[str, object]]:
    """Fetch user info from Polar API to get polar-user-id.
    
    Args:
        member_or_user_id: Member ID or user ID to fetch info for
        access_token: OAuth access token
        config: Configuration dictionary (contains API_BASE and database path)
    
    Returns:
        Dictionary containing user info, or None if request fails
    
    Raises:
        ValueError: If config is None
    """
    if config is None:
        raise ValueError("config parameter is required and cannot be None")
    
    # Get API base URL from config or use default
    api_base = config.get('API_BASE', 'https://www.polaraccesslink.com/v3')
    
    print(f"Fetching user info for ID: {member_or_user_id}...")
    
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json"
    }
    
    response = requests.get(f"{api_base}/users/{member_or_user_id}", headers=headers)
    
    if response.status_code == 200:
        user_info = response.json()
        print(f"✅ User info retrieved")
        
        # Save to database if config provided
        if config and 'polar-user-id' in user_info:
            db_module = _get_db_module(config)
            db_context = _get_db_context(config)
            
            try:
                user_data = {
                    'polar_user_id': int(user_info['polar-user-id']),
                    'first_name': user_info.get('first-name'),
                    'last_name': user_info.get('last-name'),
                    'birthdate': user_info.get('birthdate'),
                    'gender': user_info.get('gender')
                }
                # Remove None values
                user_data = {k: v for k, v in user_data.items() if v is not None}
                db_module.save_userinfo_to_db(db_context, user_data)
            finally:
                _close_db_context(db_context, config)
        
        return user_info
    else:
        print(f"⚠️  Failed to get user info: {response.status_code}")
        return None


def register_user(
    access_token: str,
    config: Dict,
    member_id: Optional[str] = None
) -> Optional[int]:
    """Register user with Polar AccessLink API (idempotent).
    
    Handles both new registration (201) and already registered (409) cases.
    
    Args:
        access_token: OAuth access token
        member_id: Optional Polar member ID
        config: Configuration dictionary (contains API_BASE)
    
    Returns:
        polar_user_id if successful, None otherwise
    
    Raises:
        ValueError: If config is None
        Exception: If registration fails with unexpected status code
    """
    if config is None:
        raise ValueError("config parameter is required and cannot be None")
    
    # Get API base URL from config
    api_base = config.get('API_BASE', 'https://www.polaraccesslink.com/v3')
    
    print("Registering user...")

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    # Prepare registration payload
    registration_data = {}
    if member_id:
        registration_data["member-id"] = member_id

    response = requests.post(
        f"{api_base}/users",
        headers=headers,
        json=registration_data
    )

    polar_user_id = None

    if response.status_code == 201:
        # Successfully registered
        user_data = response.json()
        polar_user_id = user_data.get('polar-user-id')
        print(f"✅ User registered successfully")
        print(f"  Polar User ID: {polar_user_id}")
        
    elif response.status_code == 409:
        # User already registered
        print("⚠️ User already registered (409 Conflict)")
        
        # Fetch user info to get polar-user-id
        user_id_to_fetch = member_id if member_id else "self"
        user_info = get_user_info(user_id_to_fetch, access_token, config)
        
        if user_info:
            polar_user_id = user_info.get('polar-user-id')
            print(f"✅ Retrieved Polar User ID: {polar_user_id}")
        else:
            print("⚠️  Could not retrieve polar-user-id, will attempt to continue")
            
    else:
        print(f"❌ User registration failed: {response.status_code}")
        print(f"   Response: {response.text}")
        raise Exception(f"User registration failed: {response.text}")

    print(f"\n✅ User registration complete. Polar User ID: {polar_user_id or 'Unknown'}")
    return polar_user_id


def get_physical_info(
    polar_user_id: int,
    access_token: str,
    config: Dict
) -> Dict[str, object]:
    """Get user's physical information from Polar API.
    
    Returns physical parameters including weight, height, heart rate zones,
    and VO2max. Fetches data from Polar API using transaction-based workflow.
    Falls back to database values if API returns no data, then to hardcoded defaults.
    Saves retrieved data to database for future use.
    
    Args:
        polar_user_id: Polar user ID
        access_token: OAuth access token
        config: Configuration dictionary (contains API_BASE and database path)
    
    Returns:
        Dictionary containing physical information with structure:
        {
            "weight": float (kg),
            "height": float (cm),
            "maximum-heart-rate": int (bpm),
            "resting-heart-rate": int (bpm),
            "aerobic-threshold": int (bpm) or None,
            "anaerobic-threshold": int (bpm) or None,
            "vo2-max": int or None,
            ... (other fields from API if available)
        }
    
    Raises:
        ValueError: If config is None
    """
    if config is None:
        raise ValueError("config parameter is required and cannot be None")
    
    # Get API base URL from config
    api_base = config.get('API_BASE', 'https://www.polaraccesslink.com/v3')
    
    # Get database module and context
    db_module = _get_db_module(config)
    db_context = _get_db_context(config)
    
    # Try to get from database first as fallback
    db_info = None
    try:
        db_info = db_module.get_userinfo_from_db(db_context, polar_user_id)
    except Exception as e:
        print(f"❌ Failed to retrieve physical info from database: {e}")
    
    # Hardcoded default values (final fallback)
    defaults = db_module.get_default_physical_info()
    
    # Build fallback info: prefer database values, then defaults
    if db_info:
        fallback_info = {
            "weight": db_info.get('weight', defaults['weight']),
            "height": db_info.get('height', defaults['height']),
            "maximum-heart-rate": db_info.get('maximum_heart_rate', defaults['maximum_heart_rate']),
            "resting-heart-rate": db_info.get('resting_heart_rate', defaults['resting_heart_rate']),
            "aerobic-threshold": db_info.get('aerobic_threshold', defaults['aerobic_threshold']),
            "anaerobic-threshold": db_info.get('anaerobic_threshold', defaults['anaerobic_threshold']),
            "vo2-max": db_info.get('vo2_max', defaults['vo2_max'])
        }
        fallback_source = "database"
    else:
        fallback_info = {
            "weight": defaults['weight'],
            "height": defaults['height'],
            "maximum-heart-rate": defaults['maximum_heart_rate'],
            "resting-heart-rate": defaults['resting_heart_rate'],
            "aerobic-threshold": defaults['aerobic_threshold'],
            "anaerobic-threshold": defaults['anaerobic_threshold'],
            "vo2-max": defaults['vo2_max']
        }
        fallback_source = "hardcoded defaults"
    
    def return_fallback(reason: str = "") -> Dict[str, object]:
        """Return fallback info with appropriate message."""
        if reason:
            print(reason)
        if fallback_source == "database":
            print("✅ Using physical info from database")
        else:
            print("⚠️ Using hardcoded default values")
        return fallback_info
    
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json"
    }
    
    try:
        # Step 1: Create physical info transaction
        print(f"Creating physical info transaction for user {polar_user_id}...")
        transaction_url = f"{api_base}/users/{polar_user_id}/physical-information-transactions"
        transaction_resp = requests.post(transaction_url, headers=headers)
        
        if transaction_resp.status_code == 204:
            return return_fallback("⚠️ No new physical info data available from API")
        
        if transaction_resp.status_code != 201:
            return return_fallback(f"⚠️  Transaction creation failed: {transaction_resp.status_code}")
        
        transaction_data = transaction_resp.json()
        transaction_id = transaction_data.get('transaction-id')
        
        if not transaction_id:
            return return_fallback("⚠️  No transaction ID received")
        
        # Step 2: List physical infos in transaction
        print(f"Listing physical infos in transaction {transaction_id}...")
        list_url = f"{api_base}/users/{polar_user_id}/physical-information-transactions/{transaction_id}"
        list_resp = requests.get(list_url, headers=headers)
        
        if list_resp.status_code != 200:
            # Try to commit transaction before returning
            requests.put(list_url, headers=headers)
            return return_fallback(f"⚠️  Could not list physical infos: {list_resp.status_code}")
        
        list_data = list_resp.json()
        physical_info_urls = list_data.get('physical-informations', [])
        print(f"✅ Found {len(physical_info_urls)} physical info record(s) in transaction")
        
        if not physical_info_urls:
            # Commit transaction before returning
            requests.put(list_url, headers=headers)
            return return_fallback("⚠️ No physical infos found in transaction")
        
        # Step 3: Get the newest physical info (last in list)
        # Physical infos are ordered by creation date, newest last
        newest_info_url = physical_info_urls[-1]
        print(f"Fetching newest physical info...")
        
        info_resp = requests.get(newest_info_url, headers=headers)
        
        if info_resp.status_code != 200:
            # Commit transaction before returning
            requests.put(list_url, headers=headers)
            return return_fallback(f"⚠️  Could not fetch physical info: {info_resp.status_code}")
        
        physical_info = info_resp.json()
        
        # Step 4: Commit transaction
        print("Committing physical info transaction...")
        commit_resp = requests.put(list_url, headers=headers)
        
        if commit_resp.status_code != 200:
            print(f"⚠️  Transaction commit failed: {commit_resp.status_code}")
        else:
            print("✅ Transaction committed successfully")
        
        # Extract values from API response, using defaults as fallback
        result = {
            "weight": physical_info.get('weight', defaults['weight']),
            "height": physical_info.get('height', defaults['height']),
            "maximum-heart-rate": physical_info.get('maximum-heart-rate', defaults['maximum_heart_rate']),
            "resting-heart-rate": physical_info.get('resting-heart-rate', defaults['resting_heart_rate']),
            "aerobic-threshold": physical_info.get('aerobic-threshold', defaults['aerobic_threshold']),
            "anaerobic-threshold": physical_info.get('anaerobic-threshold', defaults['anaerobic_threshold']),
            "vo2-max": physical_info.get('vo2-max', defaults['vo2_max'])
        }
        
        print(f"✅ Physical info from API: {result['weight']}kg, {result['height']}cm, HR max: {result['maximum-heart-rate']}")
        
        # Save to database
        try:
            user_data = {
                'polar_user_id': polar_user_id,
                'weight': result['weight'],
                'height': result['height'],
                'maximum_heart_rate': result['maximum-heart-rate'],
                'resting_heart_rate': result['resting-heart-rate'],
                'aerobic_threshold': result['aerobic-threshold'],
                'anaerobic_threshold': result['anaerobic-threshold'],
                'vo2_max': result['vo2-max']
            }
            # Remove None values
            user_data = {k: v for k, v in user_data.items() if v is not None}
            db_module.save_userinfo_to_db(db_context, user_data)
        finally:
            _close_db_context(db_context, config)
        
        return result
        
    except requests.exceptions.RequestException as e:
        return return_fallback(f"⚠️  API request failed: {e}")
    except Exception as e:
        return return_fallback(f"⚠️  Unexpected error fetching physical info: {e}")
    finally:
        _close_db_context(db_context, config)


__all__ = [
    # User management (Polar API functions)
    'get_user_info',
    'register_user',
    'get_physical_info',
]
