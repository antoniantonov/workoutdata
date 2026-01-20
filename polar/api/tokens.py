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


# Valid OAuth 2.0 token types
VALID_TOKEN_TYPES = {'Bearer', 'MAC', 'Basic'}


def validate_tokens(tokens: Dict[str, Optional[str]]) -> Dict[str, Optional[str]]:
    """Validate token dictionary structure and values.
    
    Args:
        tokens: Dictionary containing ACCESS_TOKEN, REFRESH_TOKEN, TOKEN_TYPE
    
    Returns:
        The validated tokens dictionary
    
    Raises:
        ValueError: If tokens are invalid (missing, empty, or wrong format)
    """
    access_token = tokens.get('ACCESS_TOKEN')
    refresh_token = tokens.get('REFRESH_TOKEN')
    token_type = tokens.get('TOKEN_TYPE')
    
    # Validate ACCESS_TOKEN (required, must be > 10 characters)
    if not access_token:
        raise ValueError("ACCESS_TOKEN is required and cannot be null or empty")
    if len(access_token) <= 10:
        raise ValueError(f"ACCESS_TOKEN must be greater than 10 characters (got {len(access_token)})")
    
    # Validate REFRESH_TOKEN if specified (must be > 10 characters)
    if refresh_token is not None and refresh_token != '':
        if len(refresh_token) <= 10:
            raise ValueError(f"REFRESH_TOKEN must be greater than 10 characters (got {len(refresh_token)})")
    
    # Validate TOKEN_TYPE (must be one of valid types)
    if not token_type:
        raise ValueError("TOKEN_TYPE is required and cannot be null or empty")
    if token_type not in VALID_TOKEN_TYPES:
        raise ValueError(
            f"TOKEN_TYPE '{token_type}' is not valid. "
            f"Must be one of: {', '.join(sorted(VALID_TOKEN_TYPES))}"
        )
    
    return tokens


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


def load_tokens(tokens_file: Path = Path("tokens_polar.json")) -> Dict[str, str]:
    """Load tokens from JSON file.
    
    Args:
        tokens_file: Path to token storage file (default: tokens_polar.json)
    
    Returns:
        Dictionary containing tokens (access_token, refresh_token, token_type)
    
    Raises:
        FileNotFoundError: If token file doesn't exist
        ValueError: If token file contains invalid JSON
    """
    if not tokens_file.exists():
        raise FileNotFoundError(f"Token file not found: {tokens_file}")
    
    try:
        with open(tokens_file, 'r') as f:
            tokens = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in token file {tokens_file}: {e}")
    
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


__all__ = [
    'validate_tokens',
    'save_tokens',
    'load_tokens',
    'encode_credentials',
    'exchange_code_for_token',
    'refresh_access_token',
]
