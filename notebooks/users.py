"""User management for Polar AccessLink API.

This module provides utilities for managing user information including:
- User registration with Polar API
- Fetching user info and physical info
- Database operations for user info caching
- Default physical info values
- Workout ID generation from timestamps
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import duckdb  # type: ignore
import requests  # type: ignore

from common_tools import get_field


# =============================================================================
# User Info Database Management
# =============================================================================

def ensure_userinfo_table(db_path: Path) -> None:
    """Ensure the userinfo table exists in the database.
    
    Creates the userinfo table with schema for storing user profile information
    from both get_user_info and get_physical_info API calls.
    
    Args:
        db_path: Path to DuckDB database file
    """
    con = duckdb.connect(str(db_path))
    try:
        con.execute("""
        CREATE TABLE IF NOT EXISTS userinfo (
            polar_user_id INTEGER PRIMARY KEY,
            first_name VARCHAR,
            last_name VARCHAR,
            birthdate VARCHAR,
            gender VARCHAR,
            weight FLOAT,
            height FLOAT,
            maximum_heart_rate INTEGER,
            resting_heart_rate INTEGER,
            aerobic_threshold INTEGER,
            anaerobic_threshold INTEGER,
            vo2_max FLOAT,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        print("✅ Userinfo table ensured")
    finally:
        con.close()


def get_userinfo_from_db(db_path: Path, polar_user_id: int) -> Optional[Dict[str, object]]:
    """Retrieve user info from database.
    
    Args:
        db_path: Path to DuckDB database file
        polar_user_id: Polar user ID
    
    Returns:
        Dictionary with user info, or None if not found
    """
    con = duckdb.connect(str(db_path))
    try:
        result = con.execute(
            "SELECT * FROM userinfo WHERE polar_user_id = ?",
            (polar_user_id,)
        ).fetchone()
        
        if result:
            columns = [desc[0] for desc in con.description]
            return dict(zip(columns, result))
        return None
    except Exception as e:
        print(f"⚠️  Error reading from userinfo table: {e}")
        return None
    finally:
        con.close()


def save_userinfo_to_db(db_path: Path, user_data: Dict[str, object]) -> None:
    """Save or update user info in database.
    
    Args:
        db_path: Path to DuckDB database file
        user_data: Dictionary with user information (must include polar_user_id)
    """
    if 'polar_user_id' not in user_data:
        print("⚠️  Cannot save userinfo: polar_user_id missing")
        return
    
    ensure_userinfo_table(db_path)
    
    con = duckdb.connect(str(db_path))
    try:
        # Upsert: Delete old record if exists, then insert new
        con.execute(
            "DELETE FROM userinfo WHERE polar_user_id = ?",
            (user_data['polar_user_id'],)
        )
        
        # Build insert statement dynamically based on available fields
        fields = list(user_data.keys())
        placeholders = ', '.join(['?' for _ in fields])
        field_names = ', '.join(fields)
        
        con.execute(
            f"INSERT INTO userinfo ({field_names}, last_updated) VALUES ({placeholders}, CURRENT_TIMESTAMP)",
            tuple(user_data[f] for f in fields)
        )
        print(f"✅ Userinfo saved to database for user {user_data['polar_user_id']}")
    except Exception as e:
        print(f"⚠️  Error saving to userinfo table: {e}")
    finally:
        con.close()


def get_default_physical_info() -> Dict[str, object]:
    """Get hardcoded default physical info values.
    
    Returns:
        Dictionary with default physical information
    """
    return {
        'weight': 78.0,
        'height': 175.0,
        'maximum_heart_rate': 188,
        'resting_heart_rate': 55,
        'aerobic_threshold': 140,
        'anaerobic_threshold': 165,
        'vo2_max': 58.0
    }


# =============================================================================
# User Management Functions
# =============================================================================

def get_user_info(
    member_or_user_id: str,
    access_token: str,
    api_base: str = "https://www.polaraccesslink.com/v3",
    db_path: Optional[Path] = None
) -> Optional[Dict[str, object]]:
    """Fetch user info from Polar API to get polar-user-id.
    
    Args:
        member_or_user_id: Member ID or user ID to fetch info for
        access_token: OAuth access token
        api_base: Polar API base URL
        db_path: Optional path to DuckDB database for saving user info
    
    Returns:
        Dictionary containing user info, or None if request fails
    """
    print(f"Fetching user info for ID: {member_or_user_id}...")
    
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json"
    }
    
    response = requests.get(f"{api_base}/users/{member_or_user_id}", headers=headers)
    
    if response.status_code == 200:
        user_info = response.json()
        print(f"✅ User info retrieved")
        
        # Save to database if db_path provided
        if db_path and 'polar-user-id' in user_info:
            user_data = {
                'polar_user_id': int(user_info['polar-user-id']),
                'first_name': user_info.get('first-name'),
                'last_name': user_info.get('last-name'),
                'birthdate': user_info.get('birthdate'),
                'gender': user_info.get('gender')
            }
            # Remove None values
            user_data = {k: v for k, v in user_data.items() if v is not None}
            save_userinfo_to_db(db_path, user_data)
        
        return user_info
    else:
        print(f"⚠️  Failed to get user info: {response.status_code}")
        return None


def register_user(
    access_token: str,
    member_id: Optional[str] = None,
    api_base: str = "https://www.polaraccesslink.com/v3"
) -> Optional[int]:
    """Register user with Polar AccessLink API (idempotent).
    
    Handles both new registration (201) and already registered (409) cases.
    
    Args:
        access_token: OAuth access token
        member_id: Optional Polar member ID
        api_base: Polar API base URL
    
    Returns:
        polar_user_id if successful, None otherwise
    
    Raises:
        Exception: If registration fails with unexpected status code
    """
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
        user_info = get_user_info(user_id_to_fetch, access_token, api_base)
        
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
    api_base: str = "https://www.polaraccesslink.com/v3",
    db_path: Optional[Path] = None
) -> Dict[str, object]:
    """Get user's physical information from Polar API.
    
    Returns physical parameters including weight, height, heart rate zones,
    and VO2max. Fetches data from Polar API using transaction-based workflow.
    Falls back to database values if API returns no data, then to hardcoded defaults.
    Saves retrieved data to database for future use.
    
    Args:
        polar_user_id: Polar user ID
        access_token: OAuth access token
        api_base: Polar API base URL
        db_path: Optional path to DuckDB database for loading/saving physical info
    
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
    """
    # Try to get from database first as fallback
    db_info = None
    if db_path:
        db_info = get_userinfo_from_db(db_path, polar_user_id)
    
    # Hardcoded default values (final fallback)
    defaults = get_default_physical_info()
    
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
        
        # Save to database if db_path provided
        if db_path:
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
            save_userinfo_to_db(db_path, user_data)
        
        return result
        
    except requests.exceptions.RequestException as e:
        return return_fallback(f"⚠️  API request failed: {e}")
    except Exception as e:
        return return_fallback(f"⚠️  Unexpected error fetching physical info: {e}")


__all__ = [
    # Database operations
    'ensure_userinfo_table',
    'get_userinfo_from_db',
    'save_userinfo_to_db',
    'get_default_physical_info',
    
    # User management
    'get_user_info',
    'register_user',
    'get_physical_info',
]
