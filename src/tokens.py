"""Token management for Polar AccessLink OAuth.

This module provides utilities for managing OAuth tokens including:
- Saving and loading tokens from JSON files
- Encoding credentials for Basic Auth
- Exchanging authorization codes for tokens
- Refreshing access tokens
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Dict, Optional

import requests  # type: ignore


def save_tokens(
    access_token: str,
    refresh_token: Optional[str] = None,
    token_type: str = "Bearer",
    tokens_file: Path = Path("tokens_polar.json")
) -> None:
    """Save tokens to JSON file (gitignored).
    
    Args:
        access_token: OAuth access token
        refresh_token: Optional OAuth refresh token
        token_type: Token type (default: "Bearer")
        tokens_file: Path to token storage file (default: tokens_polar.json)
    """
    tokens = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": token_type
    }
    with open(tokens_file, 'w') as f:
        json.dump(tokens, f, indent=2)
    print(f"✅ Tokens saved to {tokens_file}")


def load_tokens(tokens_file: Path = Path("tokens_polar.json")) -> Optional[Dict[str, str]]:
    """Load tokens from JSON file.
    
    Args:
        tokens_file: Path to token storage file (default: tokens_polar.json)
    
    Returns:
        Dictionary containing tokens, or None if file doesn't exist
    """
    if not tokens_file.exists():
        return None
    with open(tokens_file, 'r') as f:
        tokens = json.load(f)
    return tokens


def encode_credentials(client_id: str, client_secret: str) -> str:
    """Encode credentials in Base64 for Basic Auth.
    
    Args:
        client_id: Polar API client ID
        client_secret: Polar API client secret
    
    Returns:
        Base64-encoded credentials string
    """
    credentials = f"{client_id}:{client_secret}"
    encoded = base64.b64encode(credentials.encode('utf-8')).decode('utf-8')
    return encoded


def exchange_code_for_token(
    auth_code: str,
    redirect_uri: str,
    client_id: str,
    client_secret: str,
    token_url: str = "https://polarremote.com/v2/oauth2/token"
) -> Dict[str, object]:
    """Exchange authorization code for access and refresh tokens.
    
    CRITICAL: redirect_uri must EXACTLY match what was used in authorization request.
    
    Args:
        auth_code: Authorization code from OAuth callback
        redirect_uri: Redirect URI (must match the one used in authorization)
        client_id: Polar API client ID
        client_secret: Polar API client secret
        token_url: Token exchange endpoint URL
    
    Returns:
        Dictionary containing token response (access_token, refresh_token, etc.)
    
    Raises:
        Exception: If token exchange fails
    """
    print(f"Exchanging authorization code for tokens...")
    print(f"  Using redirect_uri: {redirect_uri}")
    
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": f"Basic {encode_credentials(client_id, client_secret)}"
    }
    
    data = {
        "grant_type": "authorization_code",
        "code": auth_code,
        "redirect_uri": redirect_uri  # MUST match authorization request
    }
    
    response = requests.post(token_url, headers=headers, data=data)
    
    if response.status_code != 200:
        print(f"❌ Token exchange failed: {response.status_code}")
        print(f"   Response: {response.text}")
        raise Exception(f"Token exchange failed: {response.text}")
    
    token_data = response.json()
    print("✅ Token exchange successful")
    return token_data


def refresh_access_token(
    refresh_token: str,
    client_id: str,
    client_secret: str,
    token_url: str = "https://polarremote.com/v2/oauth2/token"
) -> Dict[str, object]:
    """Refresh access token using refresh token.
    
    Args:
        refresh_token: OAuth refresh token
        client_id: Polar API client ID
        client_secret: Polar API client secret
        token_url: Token exchange endpoint URL
    
    Returns:
        Dictionary containing token response with new access_token
    
    Raises:
        Exception: If token refresh fails
    """
    print("Refreshing access token...")
    
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": f"Basic {encode_credentials(client_id, client_secret)}"
    }
    
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token
    }
    
    response = requests.post(token_url, headers=headers, data=data)
    
    if response.status_code != 200:
        print(f"❌ Token refresh failed: {response.status_code}")
        print(f"   Response: {response.text}")
        raise Exception(f"Token refresh failed: {response.text}")
    
    token_data = response.json()
    print("✅ Token refresh successful")
    return token_data


def is_token_valid(tokens_file: Path = Path("tokens_polar.json")) -> bool:
    """Check if a valid token exists in the token file.
    
    Args:
        tokens_file: Path to token storage file
    
    Returns:
        True if token file exists and contains valid access_token, False otherwise
    """
    if not tokens_file.exists():
        return False
    
    try:
        tokens = load_tokens(tokens_file)
        if not tokens:
            return False
        
        # Check if access_token exists and has reasonable length
        access_token = tokens.get('access_token')
        if not access_token or len(access_token) < 10:
            return False
        
        # Token exists and looks valid
        return True
    except (json.JSONDecodeError, Exception):
        return False


__all__ = [
    'save_tokens',
    'load_tokens',
    'encode_credentials',
    'exchange_code_for_token',
    'refresh_access_token',
    'is_token_valid',
]
