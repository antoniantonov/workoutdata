"""Workflow tools for Polar AccessLink OAuth and Exercise Management.

This module contains all helper functions extracted from the polar_accesslink_workflow.ipynb
notebook. It provides utilities for:
- OAuth token management (save, load, exchange, refresh)
- Authorization code capture via local callback server
- User registration and info retrieval
- Exercise listing and TCX data export
- Validation checks

All functions maintain the exact logic from the original notebook cells.
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import threading
import webbrowser
import xml.etree.ElementTree as ET
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlencode, urlparse

import pandas as pd  # type: ignore
import requests  # type: ignore

# =============================================================================
# Configuration and Constants
# =============================================================================

def load_configuration() -> Dict[str, object]:
    """Load configuration from environment variables.
    
    Loads Polar API configuration including client credentials, redirect port,
    and member ID from environment variables. Optionally loads from .env file
    if python-dotenv is available.
    
    Returns:
        Dict containing:
            - CLIENT_ID: Polar API client ID
            - CLIENT_SECRET: Polar API client secret
            - REDIRECT_PORT: Port for OAuth callback (default: 5000)
            - MEMBER_ID: Optional Polar member ID
            - AUTH_URL: Polar authorization URL
            - TOKEN_URL: Polar token exchange URL
            - API_BASE: Polar API base URL
            - TOKENS_FILE: Path to token storage file
            - ALLOW_PORT_FALLBACK: Whether to try alternative ports
    
    Raises:
        ValueError: If required environment variables are missing
    """
    # Optional: Load from .env file if python-dotenv is available
    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv()
        print("✓ Loaded environment from .env file")
    except ImportError:
        print("ℹ python-dotenv not installed, using system environment variables")

    # Load configuration from environment variables
    CLIENT_ID = os.getenv('POLAR_CLIENT_ID')
    CLIENT_SECRET = os.getenv('POLAR_CLIENT_SECRET')
    REDIRECT_PORT = int(os.getenv('POLAR_REDIRECT_PORT', '5000'))
    MEMBER_ID = os.getenv('POLAR_MEMBER_ID')
    ALLOW_PORT_FALLBACK = os.getenv('ALLOW_PORT_FALLBACK', 'true').lower() == 'true'

    # Validate required environment variables
    missing_vars = []
    if not CLIENT_ID:
        missing_vars.append('POLAR_CLIENT_ID')
    if not CLIENT_SECRET:
        missing_vars.append('POLAR_CLIENT_SECRET')

    if missing_vars:
        raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}")

    print(f"✓ Configuration loaded")
    print(f"  - Client ID: {CLIENT_ID[:8]}...")
    print(f"  - Redirect Port: {REDIRECT_PORT}")
    print(f"  - Member ID: {MEMBER_ID if MEMBER_ID else 'Not set (will be obtained)'}")

    # API endpoints
    AUTH_URL = "https://flow.polar.com/oauth2/authorization"
    TOKEN_URL = "https://polarremote.com/v2/oauth2/token"
    API_BASE = "https://www.polaraccesslink.com/v3"

    # Token storage file
    TOKENS_FILE = Path("tokens_polar.json")

    return {
        'CLIENT_ID': CLIENT_ID,
        'CLIENT_SECRET': CLIENT_SECRET,
        'REDIRECT_PORT': REDIRECT_PORT,
        'MEMBER_ID': MEMBER_ID,
        'AUTH_URL': AUTH_URL,
        'TOKEN_URL': TOKEN_URL,
        'API_BASE': API_BASE,
        'TOKENS_FILE': TOKENS_FILE,
        'ALLOW_PORT_FALLBACK': ALLOW_PORT_FALLBACK,
    }


# =============================================================================
# Token Management Functions
# =============================================================================

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
    print(f"✓ Tokens saved to {tokens_file}")


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
    print("✓ Token exchange successful")
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
    print("✓ Token refresh successful")
    return token_data


def ensure_token(tokens_file: Path = Path("tokens_polar.json")) -> Optional[str]:
    """Placeholder for token management - load existing or prompt for new authorization.
    
    Args:
        tokens_file: Path to token storage file
    
    Returns:
        Access token if available, None otherwise
    """
    tokens = load_tokens(tokens_file)
    if tokens:
        print(f"✓ Using existing tokens from {tokens_file}")
        return tokens['access_token']
    else:
        print("⚠ No tokens found. Please complete authorization flow first.")
        return None


# =============================================================================
# User Management Functions
# =============================================================================

def get_user_info(
    member_or_user_id: str,
    access_token: str,
    api_base: str = "https://www.polaraccesslink.com/v3"
) -> Optional[Dict[str, object]]:
    """Fetch user info from Polar API to get polar-user-id.
    
    Args:
        member_or_user_id: Member ID or user ID to fetch info for
        access_token: OAuth access token
        api_base: Polar API base URL
    
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
        print(f"✓ User info retrieved")
        return user_info
    else:
        print(f"⚠ Failed to get user info: {response.status_code}")
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
        print(f"✓ User registered successfully")
        print(f"  Polar User ID: {polar_user_id}")
        
    elif response.status_code == 409:
        # User already registered
        print("ℹ User already registered (409 Conflict)")
        
        # Fetch user info to get polar-user-id
        user_id_to_fetch = member_id if member_id else "self"
        user_info = get_user_info(user_id_to_fetch, access_token, api_base)
        
        if user_info:
            polar_user_id = user_info.get('polar-user-id')
            print(f"✓ Retrieved Polar User ID: {polar_user_id}")
        else:
            print("⚠ Could not retrieve polar-user-id, will attempt to continue")
            
    else:
        print(f"❌ User registration failed: {response.status_code}")
        print(f"   Response: {response.text}")
        raise Exception(f"User registration failed: {response.text}")

    print(f"\n✓ User registration complete. Polar User ID: {polar_user_id or 'Unknown'}")
    return polar_user_id


# =============================================================================
# OAuth Callback Server
# =============================================================================

def create_callback_handler(state_token: str, result_storage: Dict[str, Optional[str]]):
    """Factory function to create OAuth callback handler class.
    
    Args:
        state_token: CSRF protection token
        result_storage: Dictionary to store authorization code or error
    
    Returns:
        CallbackHandler class configured with state token and result storage
    """
    class CallbackHandler(BaseHTTPRequestHandler):
        """HTTP request handler for OAuth callback."""
        
        def log_message(self, format, *args):
            """Suppress default logging."""
            pass
        
        def do_GET(self):
            # Parse query parameters
            parsed_path = urlparse(self.path)
            query_params = parse_qs(parsed_path.query)
            
            # Validate state token
            received_state = query_params.get('state', [None])[0]
            if received_state != state_token:
                result_storage['auth_error'] = "State token mismatch - possible CSRF attack"
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Error: Invalid state token")
                return
            
            # Check for error
            if 'error' in query_params:
                result_storage['auth_error'] = query_params.get('error_description', [query_params['error'][0]])[0]
                self.send_response(400)
                self.end_headers()
                self.wfile.write(f"Error: {result_storage['auth_error']}".encode())
                return
            
            # Extract authorization code
            if 'code' in query_params:
                result_storage['auth_code'] = query_params['code'][0]
                self.send_response(200)
                self.send_header('Content-type', 'text/html')
                self.end_headers()
                html = """
                <html>
                <head><title>Authorization Successful</title></head>
                <body style="font-family: sans-serif; text-align: center; padding: 50px;">
                    <h1 style="color: green;">Authorization Successful!</h1>
                    <p>You can close this window and return to the notebook.</p>
                </body>
                </html>
                """
                self.wfile.write(html.encode())
            else:
                result_storage['auth_error'] = "No authorization code received"
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Error: No authorization code")
    
    return CallbackHandler


def start_callback_server(
    port: int,
    allow_port_fallback: bool = True
) -> Tuple[int, threading.Thread, str, Dict[str, Optional[str]]]:
    """Start local callback server and return the actual port used.
    
    Args:
        port: Desired port number
        allow_port_fallback: Whether to try alternative ports if requested port is busy
    
    Returns:
        Tuple containing:
            - actual_port: Port number the server is running on
            - server_thread: Thread running the server
            - redirect_uri: Full redirect URI for OAuth
            - result_storage: Dictionary to check for auth_code or auth_error
    
    Raises:
        RuntimeError: If no port is available
    """
    result_storage: Dict[str, Optional[str]] = {
        'auth_code': None,
        'auth_error': None
    }
    state_token = secrets.token_urlsafe(32)
    
    CallbackHandler = create_callback_handler(state_token, result_storage)
    
    server = None
    actual_port = port
    
    # Try to bind to requested port
    try:
        server = HTTPServer(('localhost', port), CallbackHandler)
        actual_port = port
    except OSError as e:
        if not allow_port_fallback:
            raise RuntimeError(
                f"Port {port} is not available and ALLOW_PORT_FALLBACK is False. \n"
                f"Please free port {port} or set ALLOW_PORT_FALLBACK=true"
            ) from e
        
        # Try to find an available port
        print(f"⚠ Port {port} is busy, trying to find an available port...")
        for try_port in range(port + 1, port + 100):
            try:
                server = HTTPServer(('localhost', try_port), CallbackHandler)
                actual_port = try_port
                print(f"✓ Using fallback port: {actual_port}")
                break
            except OSError:
                continue
        
        if server is None:
            raise RuntimeError("Could not find an available port")
    
    redirect_uri = f"http://localhost:{actual_port}/callback"
    print(f"✓ Callback server ready at: {redirect_uri}")
    
    # Start server in background thread
    def serve():
        while result_storage['auth_code'] is None and result_storage['auth_error'] is None:
            server.handle_request()
        server.server_close()
    
    server_thread = threading.Thread(target=serve, daemon=True)
    server_thread.start()
    
    result_storage['state_token'] = state_token
    
    return actual_port, server_thread, redirect_uri, result_storage


def run_authorization_flow(
    client_id: str,
    redirect_port: int,
    allow_port_fallback: bool = True,
    auth_url: str = "https://flow.polar.com/oauth2/authorization",
    timeout: int = 300
) -> Tuple[str, str]:
    """Run complete OAuth authorization flow with local callback server.
    
    Args:
        client_id: Polar API client ID
        redirect_port: Port for callback server
        allow_port_fallback: Whether to try alternative ports
        auth_url: Authorization endpoint URL
        timeout: Timeout in seconds (default: 300 = 5 minutes)
    
    Returns:
        Tuple of (auth_code, redirect_uri)
    
    Raises:
        Exception: If authorization fails or times out
    """
    # Start callback server
    print("Starting local callback server...")
    actual_port, server_thread, redirect_uri, result_storage = start_callback_server(
        redirect_port,
        allow_port_fallback
    )

    # Build authorization URL
    auth_params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": result_storage['state_token']
    }
    authorization_url = f"{auth_url}?{urlencode(auth_params)}"

    print("\n" + "="*80)
    print("AUTHORIZATION REQUIRED")
    print("="*80)
    print(f"\n1. Click this URL to authorize:\n\n   {authorization_url}\n")
    print("2. Log in to Polar and authorize this application")
    print("3. You will be redirected back to localhost")
    print("4. Wait for authorization code to be captured...\n")
    print("="*80 + "\n")

    # Wait for authorization code
    server_thread.join(timeout=timeout)

    if result_storage['auth_error']:
        print(f"\n❌ Authorization failed: {result_storage['auth_error']}")
        raise Exception(f"Authorization failed: {result_storage['auth_error']}")
    elif result_storage['auth_code']:
        print(f"\n✓ Authorization code captured: {result_storage['auth_code'][:8]}...")
        print(f"✓ Redirect URI used: {redirect_uri}")
        return result_storage['auth_code'], redirect_uri
    else:
        print("\n❌ Timeout waiting for authorization")
        raise Exception("Authorization timeout")


def complete_token_exchange(
    auth_code: str,
    redirect_uri: str,
    client_id: str,
    client_secret: str,
    token_url: str = "https://polarremote.com/v2/oauth2/token",
    tokens_file: Path = Path("tokens_polar.json")
) -> Dict[str, object]:
    """Exchange authorization code for tokens and save them.
    
    Args:
        auth_code: Authorization code from OAuth callback
        redirect_uri: Redirect URI used in authorization
        client_id: Polar API client ID
        client_secret: Polar API client secret
        token_url: Token exchange endpoint URL
        tokens_file: Path to save tokens
    
    Returns:
        Dictionary containing token response
    
    Raises:
        Exception: If authorization code or redirect URI is missing
    """
    if not auth_code:
        raise Exception("No authorization code available. Please run authorization flow first.")

    if not redirect_uri:
        raise Exception("Redirect URI not set. Please run authorization flow first.")

    # Exchange code for tokens
    token_response = exchange_code_for_token(auth_code, redirect_uri, client_id, client_secret, token_url)

    # Extract tokens
    access_token = token_response.get('access_token')
    refresh_token = token_response.get('refresh_token')
    token_type = token_response.get('token_type', 'Bearer')
    expires_in = token_response.get('expires_in')

    # Save tokens to file
    save_tokens(access_token, refresh_token, token_type, tokens_file)

    # Display masked token info
    print("\n" + "="*80)
    print("TOKEN INFORMATION (masked)")
    print("="*80)
    print(f"Access Token:  {access_token[:8]}... (length: {len(access_token)})")
    if refresh_token:
        print(f"Refresh Token: {refresh_token[:8]}... (length: {len(refresh_token)})")
    print(f"Token Type:    {token_type}")
    if expires_in:
        print(f"Expires In:    {expires_in} seconds ({expires_in//3600} hours)")
    print("="*80 + "\n")
    
    return token_response


# =============================================================================
# TCX to CSV Conversion
# =============================================================================

def convert_tcx_to_csv(
    tcx_path: Path,
    output_csv_path: Path,
    name: str,
    height: float,
    weight: float,
    hr_max: int,
    hr_sit: int,
    vo2max: int
) -> Path:
    """Convert TCX file to Polar-compatible CSV format.
    
    The output CSV has two parts:
    1. Metadata rows (2 rows): workout summary information
    2. Time-series rows: per-second heart rate data with relative timestamps
    
    Args:
        tcx_path: Path to input TCX file
        output_csv_path: Optional path for output CSV (default: same name as TCX with .csv extension)
        name: Athlete name (default: "Anton Antonov ")
        height: Height in cm (default: 175.0)
        weight: Weight in kg (default: 78.0)
        hr_max: Maximum heart rate (default: 188)
        hr_sit: Sitting heart rate (default: None)
        vo2max: VO2max value (default: 58)
    
    Returns:
        Path to the created CSV file
    
    Raises:
        FileNotFoundError: If TCX file doesn't exist
        ValueError: If TCX parsing fails
    """
    import xml.etree.ElementTree as ET
    from datetime import datetime, timedelta
    
    if not tcx_path.exists():
        raise FileNotFoundError(f"TCX file not found: {tcx_path}")
    
    # Parse TCX file
    tree = ET.parse(tcx_path)
    root = tree.getroot()
    
    # Define namespace
    ns = {'tcx': 'http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2'}
    
    # Extract activity data
    activity = root.find('.//tcx:Activity', ns)
    if activity is None:
        raise ValueError("No Activity found in TCX file")
    
    sport = activity.get('Sport', 'Other').upper()
    
    # Extract lap data
    lap = activity.find('.//tcx:Lap', ns)
    if lap is None:
        raise ValueError("No Lap found in TCX file")
    
    # Extract start time and convert to DD-MM-YYYY format and HH:MM:SS
    start_time_str = lap.get('StartTime')  # e.g., "2025-10-19T00:47:34.000Z"
    start_dt = datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))
    date_str = start_dt.strftime('%d-%m-%Y')  # DD-MM-YYYY
    time_str = start_dt.strftime('%H:%M:%S')  # HH:MM:SS
    
    # Extract metadata
    total_time_seconds = float(lap.find('tcx:TotalTimeSeconds', ns).text)
    duration = str(timedelta(seconds=int(total_time_seconds))).split('.')[0]  # HH:MM:SS format
    
    distance_elem = lap.find('tcx:DistanceMeters', ns)
    distance_km = float(distance_elem.text) / 1000 if distance_elem is not None else 0.0
    
    calories_elem = lap.find('tcx:Calories', ns)
    calories = int(calories_elem.text) if calories_elem is not None else None
    
    avg_hr_elem = lap.find('tcx:AverageHeartRateBpm/tcx:Value', ns)
    avg_hr = int(avg_hr_elem.text) if avg_hr_elem is not None else None
    
    max_hr_elem = lap.find('tcx:MaximumHeartRateBpm/tcx:Value', ns)
    max_hr_workout = int(max_hr_elem.text) if max_hr_elem is not None else None
    
    # Extract notes
    notes_elem = activity.find('tcx:Notes', ns)
    notes = notes_elem.text if notes_elem is not None else ""
    
    # Extract workout name from Training/Plan/Name if available
    plan_name_elem = activity.find('.//tcx:Training/tcx:Plan/tcx:Name', ns)
    if plan_name_elem is not None and plan_name_elem.text:
        sport = plan_name_elem.text.upper()
    
    # Build metadata rows
    metadata_row1_headers = [
        'Name', 'Sport', 'Date', 'Start time', 'Duration', 'Total distance (km)',
        'Average heart rate (bpm)', 'Average speed (km/h)', 'Max speed (km/h)',
        'Average pace (min/km)', 'Max pace (min/km)', 'Calories',
        'Fat percentage of calories(%)', 'Average cadence (rpm)', 'Average stride length (cm)',
        'Running index', 'Training load', 'Ascent (m)', 'Descent (m)',
        'Average power (W)', 'Max power (W)', 'Notes', 'Height (cm)', 'Weight (kg)',
        'HR max', 'HR sit', 'VO2max', ''
    ]
    
    metadata_row2_values = [
        name, sport, date_str, time_str,
        duration, f"{distance_km:.2f}",
        str(avg_hr) if avg_hr else '', '', '', '', '',
        str(calories) if calories else '',
        '', '', '', '', '', '', '', '', '',
        notes,  # CSV writer will properly escape newlines and commas
        str(height), str(weight),
        str(max_hr_workout) if max_hr_workout else str(hr_max), str(hr_sit) if hr_sit else '', str(vo2max) if vo2max else '', ''
    ]
    
    # Extract trackpoints for time-series data
    trackpoints = lap.findall('.//tcx:Trackpoint', ns)
    
    if not trackpoints:
        raise ValueError("No trackpoints found in TCX file")
    
    # Get the first trackpoint timestamp as reference
    first_trackpoint = trackpoints[0]
    first_time_elem = first_trackpoint.find('tcx:Time', ns)
    if first_time_elem is None:
        raise ValueError("First trackpoint missing Time element")
    
    reference_time = datetime.fromisoformat(first_time_elem.text.replace('Z', '+00:00'))
    
    # Build time-series data
    timeseries_headers = [
        'Sample rate', 'Time', 'HR (bpm)', 'Speed (km/h)', 'Pace (min/km)',
        'Cadence', 'Altitude (m)', 'Stride length (m)', 'Distances (m)',
        'Temperatures (C)', 'Power (W)', ''
    ]
    
    timeseries_rows = []
    
    # Process each trackpoint
    for i, tp in enumerate(trackpoints):
        time_elem = tp.find('tcx:Time', ns)
        if time_elem is None:
            continue
        
        tp_time = datetime.fromisoformat(time_elem.text.replace('Z', '+00:00'))
        elapsed = int((tp_time - reference_time).total_seconds()) + 1  # +1 because first data row is at 00:00:01
        
        # Format as HH:MM:SS
        hours = elapsed // 3600
        minutes = (elapsed % 3600) // 60
        seconds = elapsed % 60
        time_formatted = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        
        # Extract HR
        hr_elem = tp.find('tcx:HeartRateBpm/tcx:Value', ns)
        hr_value = hr_elem.text if hr_elem is not None else ''
        
        # Build row - first row gets sample rate of 1, rest get empty string
        sample_rate = '1' if i == 0 else ''
        row = [sample_rate, time_formatted, hr_value, '', '', '', '', '', '', '', '', '']
        timeseries_rows.append(row)
    
    # Determine output path
    if output_csv_path is None:
        output_csv_path = tcx_path.with_suffix('.csv')
    
    # Write CSV using csv module for proper escaping
    import csv
    with open(output_csv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
        
        # Write metadata rows
        writer.writerow(metadata_row1_headers)
        writer.writerow(metadata_row2_values)
        
        # Write timeseries header
        writer.writerow(timeseries_headers)
        
        # Write timeseries data
        for row in timeseries_rows:
            writer.writerow(row)
    
    print(f"✓ Converted TCX to CSV: {output_csv_path}")
    print(f"  - Duration: {duration}")
    print(f"  - Trackpoints: {len(trackpoints)}")
    print(f"  - Average HR: {avg_hr if avg_hr else 'N/A'}")
    
    return output_csv_path


# =============================================================================
# Exercise Management Functions
# =============================================================================

def get_physical_info(
    polar_user_id: int,
    access_token: str,
    api_base: str = "https://www.polaraccesslink.com/v3"
) -> Dict[str, object]:
    """Get user's physical information from Polar API.
    
    Returns physical parameters including weight, height, heart rate zones,
    and VO2max. Fetches data from Polar API using transaction-based workflow.
    Falls back to default values if API call fails or data is missing.
    
    Args:
        polar_user_id: Polar user ID
        access_token: OAuth access token
        api_base: Polar API base URL
    
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
    # Default values (fallback)
    defaults = {
        "weight": 78.0,
        "height": 175.0,
        "maximum-heart-rate": 188,
        "resting-heart-rate": 55,
        "aerobic-threshold": None,
        "anaerobic-threshold": None,
        "vo2-max": 58
    }
    
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
            print("ℹ No new physical info data available, using defaults")
            return defaults
        
        if transaction_resp.status_code != 201:
            print(f"⚠ Transaction creation failed: {transaction_resp.status_code}")
            print(f"  Using default values")
            return defaults
        
        transaction_data = transaction_resp.json()
        transaction_id = transaction_data.get('transaction-id')
        
        if not transaction_id:
            print("⚠ No transaction ID received, using defaults")
            return defaults
        
        # Step 2: List physical infos in transaction
        print(f"Listing physical infos in transaction {transaction_id}...")
        list_url = f"{api_base}/users/{polar_user_id}/physical-information-transactions/{transaction_id}"
        list_resp = requests.get(list_url, headers=headers)
        
        if list_resp.status_code != 200:
            print(f"⚠ Could not list physical infos: {list_resp.status_code}")
            # Try to commit transaction before returning
            requests.put(list_url, headers=headers)
            return defaults
        
        list_data = list_resp.json()
        physical_info_urls = list_data.get('physical-informations', [])
        print(f"✓ Found {len(physical_info_urls)} physical info record(s) in transaction")
        
        if not physical_info_urls:
            print("ℹ No physical infos found in transaction")
            # Commit transaction before returning
            requests.put(list_url, headers=headers)
            return defaults
        
        # Step 3: Get the newest physical info (last in list)
        # Physical infos are ordered by creation date, newest last
        newest_info_url = physical_info_urls[-1]
        print(f"Fetching newest physical info...")
        
        info_resp = requests.get(newest_info_url, headers=headers)
        
        if info_resp.status_code != 200:
            print(f"⚠ Could not fetch physical info: {info_resp.status_code}")
            # Commit transaction before returning
            requests.put(list_url, headers=headers)
            return defaults
        
        physical_info = info_resp.json()
        
        # Step 4: Commit transaction
        print("Committing physical info transaction...")
        commit_resp = requests.put(list_url, headers=headers)
        
        if commit_resp.status_code != 200:
            print(f"⚠ Transaction commit failed: {commit_resp.status_code}")
        else:
            print("✓ Transaction committed successfully")
        
        # Extract values from API response, using defaults as fallback
        result = {
            "weight": physical_info.get('weight', defaults['weight']),
            "height": physical_info.get('height', defaults['height']),
            "maximum-heart-rate": physical_info.get('maximum-heart-rate', defaults['maximum-heart-rate']),
            "resting-heart-rate": physical_info.get('resting-heart-rate', defaults['resting-heart-rate']),
            "aerobic-threshold": physical_info.get('aerobic-threshold', defaults['aerobic-threshold']),
            "anaerobic-threshold": physical_info.get('anaerobic-threshold', defaults['anaerobic-threshold']),
            "vo2-max": physical_info.get('vo2-max', defaults['vo2-max'])
        }
        
        print(f"✓ Physical info from API: {result['weight']}kg, {result['height']}cm, HR max: {result['maximum-heart-rate']}")
        return result
        
    except requests.exceptions.RequestException as e:
        print(f"⚠ API request failed: {e}")
        print("  Using default values")
        return defaults
    except Exception as e:
        print(f"⚠ Unexpected error fetching physical info: {e}")
        print("  Using default values")
        return defaults


def get_field(exercise: Dict[str, object], *keys: str) -> Optional[object]:
    """Extract field from exercise dict trying multiple possible key names.
    
    Args:
        exercise: Exercise dictionary
        *keys: Key names to try in order
    
    Returns:
        Value if found, None otherwise
    """
    for key in keys:
        if key in exercise:
            return exercise[key]
    return None


def normalize_start_time(exercise: Dict[str, object]) -> datetime:
    """Normalize exercise start time to datetime object.
    
    Handles various timestamp formats and field names from Polar API.
    
    Args:
        exercise: Exercise dictionary
    
    Returns:
        datetime object, or empty string if parsing fails
    """
    raw = get_field(exercise, 'start_time', 'start-time', 'local_start_time', 'local-start-time')
    if not raw:
        return ''
    # Handle potential trailing Z
    raw_norm = raw.replace('Z', '+00:00') if raw.endswith('Z') else raw
    try:
        return datetime.fromisoformat(raw_norm)
    except ValueError:
        return raw


def list_exercises(
    access_token: str,
    api_base: str = "https://www.polaraccesslink.com/v3"
) -> List[Dict[str, object]]:
    """List user exercises using Polar AccessLink API.
    
    Args:
        access_token: OAuth access token
        api_base: Polar API base URL
    
    Returns:
        List of exercise dictionaries
    
    Raises:
        Exception: If exercise listing fails
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json"
    }
    
    print("Listing exercises via /users/{user}/exercises API...\n")
    exercises_url = f"{api_base}/exercises"
    resp = requests.get(exercises_url, headers=headers)

    exercises = []
    if resp.status_code == 200:
        body = resp.json()
        if isinstance(body, list):
            exercises = body
        elif isinstance(body, dict):
            exercises = body.get('exercises', [])
        else:
            print(f"⚠ Unexpected exercises payload type: {type(body).__name__}")
        print(f"✓ Retrieved {len(exercises)} exercise(s)")
    elif resp.status_code == 204:
        print("ℹ No exercises available (204 No Content)")
    else:
        print(f"❌ Failed to list exercises: {resp.status_code}")
        print(f"   Response: {resp.text}")
        raise Exception("Exercise listing failed")
    
    return exercises


def display_exercises(exercises: List[Dict[str, object]]) -> None:
    """Display exercises in a formatted table.
    
    Args:
        exercises: List of exercise dictionaries
    """
    if not exercises:
        print("ℹ No exercises to display.")
        return
    
    print("\n" + "="*80)
    print("AVAILABLE EXERCISES (new API)")
    print("="*80)

    for i, ex in enumerate(exercises):
        print(f"\n{i+1}. Exercise ID: {get_field(ex, 'id', 'exercise_id')}")
        print(f"   Start Time: {get_field(ex, 'start_time', 'start-time', 'local_start_time')}")
        print(f"   Duration: {ex.get('duration', 'Unknown')}")
        print(f"   Sport: {get_field(ex, 'sport', 'detailed_sport_info')}")
    print("\n" + "="*80)


def select_latest_exercise(exercises: List[Dict[str, object]]) -> Optional[Dict[str, object]]:
    """Select the most recent exercise from a list.
    
    Args:
        exercises: List of exercise dictionaries
    
    Returns:
        Latest exercise dictionary, or None if list is empty
    """
    if not exercises:
        return None
    
    latest = max(exercises, key=normalize_start_time)
    exercise_id = get_field(latest, 'id', 'exercise_id')
    latest_start = get_field(latest, 'start_time', 'start-time', 'local_start_time')
    print(f"\n✓ Selected latest exercise: {exercise_id}")
    print(f"  Start Time: {latest_start}")
    
    return latest


def download_exercise_tcx(
    exercise_id: str,
    polar_user_id: int,
    access_token: str,
    output_dir: Path = Path("hr_data"),
    api_base: str = "https://www.polaraccesslink.com/v3"
) -> Optional[pd.DataFrame]:
    """Download and parse TCX data for an exercise.
    
    Uses convert_tcx_to_csv to convert TCX to Polar-compatible CSV format.
    Fetches user info to obtain parameters like weight, height, and hr_max.
    
    Args:
        exercise_id: Exercise ID to download
        polar_user_id: Polar user ID
        access_token: OAuth access token
        output_dir: Directory to save CSV output
        api_base: Polar API base URL
    
    Returns:
        DataFrame with parsed CSV data, or None if download fails
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json"
    }
    
    # Fetch user info to get parameters for CSV conversion
    print("\nFetching user info for conversion parameters...")
    user_info = get_user_info(polar_user_id, access_token, api_base)
    
    # Extract user name with default
    name = "Anton Antonov "  # Default
    if user_info and 'first-name' in user_info and 'last-name' in user_info:
        name = f"{user_info.get('first-name', '')} {user_info.get('last-name', '')} "
        print(f"✓ User name: {name.strip()}")
    
    # Get physical information using dedicated function
    physical_info = get_physical_info(polar_user_id, access_token, api_base)
    
    # Extract parameters from physical_info
    weight = physical_info.get('weight', 0.0)
    height = physical_info.get('height', 0.0)
    hr_max = physical_info.get('maximum-heart-rate', 0)
    hr_sit = physical_info.get('resting-heart-rate', 0)
    vo2max = physical_info.get('vo2-max', 0)
    
    # Fetch TCX for exercise
    print("\nDownloading TCX for exercise...")
    tcx_url = f"{api_base}/exercises/{exercise_id}/tcx"
    tcx_headers = {**headers, "Accept": "application/vnd.garmin.tcx+xml"}
    tcx_resp = requests.get(tcx_url, headers=tcx_headers)

    if tcx_resp.status_code != 200:
        print(f"❌ Failed to fetch TCX: {tcx_resp.status_code}")
        snippet = tcx_resp.text[:500] if hasattr(tcx_resp, 'text') else b""
        print(f"   Response: {snippet}...")
        return None
    
    print("✓ TCX downloaded")
    
    # Save TCX temporarily
    output_dir.mkdir(exist_ok=True)
    temp_tcx_path = output_dir / f"temp_exercise_{exercise_id}.tcx"
    
    try:
        with open(temp_tcx_path, 'wb') as f:
            f.write(tcx_resp.content)
        
        # Convert TCX to CSV using convert_tcx_to_csv
        csv_path = output_dir / f"polar_latest_exercise_{exercise_id}.csv"
        
        convert_tcx_to_csv(
            tcx_path=temp_tcx_path,
            output_csv_path=csv_path,
            name=name,
            height=height,
            weight=weight,
            hr_max=hr_max,
            hr_sit=hr_sit,
            vo2max=vo2max
        )
        
        # Read the CSV and return as DataFrame
        df_csv = pd.read_csv(csv_path, skiprows=2)  # Skip metadata rows
        print(f"✓ CSV saved: {csv_path}")
        print("\nSample:")
        print(df_csv.head(10))
        
        return df_csv
        
    finally:
        # Clean up temporary TCX file
        if temp_tcx_path.exists():
            temp_tcx_path.unlink()
            print(f"✓ Cleaned up temporary TCX file")


def fetch_and_export_latest_exercise(
    polar_user_id: Optional[int],
    access_token: str,
    output_dir: Path = Path("hr_data"),
    api_base: str = "https://www.polaraccesslink.com/v3"
) -> None:
    """Complete workflow to fetch exercises and export latest TCX data.
    
    Args:
        polar_user_id: Polar user ID (for validation)
        access_token: OAuth access token
        output_dir: Directory to save CSV output
        api_base: Polar API base URL
    
    Raises:
        Exception: If polar_user_id is not available
    """
    if not polar_user_id:
        raise Exception("Polar user ID unavailable. Ensure registration step completed.")

    # List exercises
    exercises = list_exercises(access_token, api_base)
    
    if not exercises:
        print("ℹ No exercises to process; skipping TCX download.")
        return
    
    # Display exercises
    display_exercises(exercises)
    
    # Select latest
    latest = select_latest_exercise(exercises)
    if not latest:
        return
    
    exercise_id = get_field(latest, 'id', 'exercise_id')
    
    # Download TCX
    download_exercise_tcx(exercise_id, polar_user_id, access_token, output_dir, api_base)
    
    print("\n" + "="*80)
    print("EXERCISE FETCH (NEW API) COMPLETE")
    print("="*80)


# =============================================================================
# Validation Functions
# =============================================================================

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


def run_validation_checks(
    tokens_file: Path = Path("tokens_polar.json"),
    required_env_vars: List[str] = None
) -> bool:
    """Run validation checks on tokens file and environment variables.
    
    Args:
        tokens_file: Path to token storage file
        required_env_vars: List of required environment variable names
    
    Returns:
        True if all checks pass, False otherwise
    """
    if required_env_vars is None:
        required_env_vars = ['POLAR_CLIENT_ID', 'POLAR_CLIENT_SECRET']
    
    print("Running validation checks...\n")

    validation_passed = True

    # Check 1: Tokens file exists
    if tokens_file.exists():
        print("✓ Tokens file exists")
        
        # Check 2: Tokens file is valid JSON
        try:
            tokens = load_tokens(tokens_file)
            print("✓ Tokens file is valid JSON")
            
            # Check 3: Required fields present
            required_fields = ['access_token', 'token_type']
            for field in required_fields:
                if field in tokens and tokens[field]:
                    print(f"✓ Field '{field}' present")
                else:
                    print(f"❌ Field '{field}' missing or empty")
                    validation_passed = False
            
            # Check 4: Optional refresh_token
            if 'refresh_token' in tokens and tokens['refresh_token']:
                print(f"✓ Refresh token available")
            else:
                print(f"ℹ Refresh token not available (optional)")
            
            # Check 5: Token format (basic validation)
            if len(tokens['access_token']) > 10:
                print(f"✓ Access token format looks valid")
            else:
                print(f"⚠ Access token seems too short")
                validation_passed = False
                
        except json.JSONDecodeError:
            print("❌ Tokens file is not valid JSON")
            validation_passed = False
    else:
        print("❌ Tokens file does not exist")
        print("   Run authorization flow to obtain tokens")
        validation_passed = False

    # Check 6: Environment variables
    for var in required_env_vars:
        if os.getenv(var):
            print(f"✓ Environment variable {var} is set")
        else:
            print(f"❌ Environment variable {var} is not set")
            validation_passed = False

    # Summary
    print("\n" + "="*80)
    if validation_passed:
        print("✓ ALL VALIDATION CHECKS PASSED")
    else:
        print("⚠ SOME VALIDATION CHECKS FAILED")
    print("="*80)
    
    return validation_passed


# =============================================================================
# Complete Workflow Orchestration
# =============================================================================

def run_polar_workflow(
    output_dir: Path = Path("hr_data"),
    tokens_file: Path = Path("tokens_polar.json"),
    timeout: int = 300
) -> Dict[str, object]:
    """Execute complete Polar AccessLink workflow.
    
    This function orchestrates the entire workflow:
    1. Load configuration from environment variables
    2. Check token validity and run authorization if needed
    3. Register user (idempotent)
    4. List and download latest exercise TCX data
    
    Args:
        output_dir: Directory to save exercise CSV output (default: hr_data)
        tokens_file: Path to token storage file (default: tokens_polar.json)
        timeout: Timeout for authorization flow in seconds (default: 300)
    
    Returns:
        Dictionary containing:
            - config: Configuration dictionary
            - polar_user_id: Polar user ID
            - access_token: OAuth access token
            - exercises: List of exercises
            - latest_exercise: Latest exercise data
            - tcx_dataframe: DataFrame with trackpoint data (if available)
    
    Raises:
        ValueError: If configuration is invalid
        Exception: If any step fails
    """
    print("="*80)
    print("POLAR ACCESSLINK COMPLETE WORKFLOW")
    print("="*80 + "\n")
    
    # Step 1: Load configuration
    print("Step 1: Loading configuration...")
    config = load_configuration()
    print()
    
    # Step 2: Check token validity and authorize if needed
    print("Step 2: Checking token validity...")
    if is_token_valid(tokens_file):
        print("✓ Valid token found")
        tokens = load_tokens(tokens_file)
        access_token = tokens['access_token']
    else:
        print("⚠ No valid token found. Starting authorization flow...")
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
            tokens_file=tokens_file
        )
        
        access_token = token_response.get('access_token')
        
        # Run validation checks
        print("\nRunning validation checks after authorization...")
        validation_passed = run_validation_checks(
            tokens_file=tokens_file,
            required_env_vars=['POLAR_CLIENT_ID', 'POLAR_CLIENT_SECRET']
        )
        
        if not validation_passed:
            raise Exception("Validation checks failed after authorization")
    
    print()
    
    # Step 3: Register user
    print("Step 3: Registering user...")
    polar_user_id = register_user(
        access_token=access_token,
        member_id=config['MEMBER_ID'],
        api_base=config['API_BASE']
    )
    print()
    
    # Step 4: List and download latest exercise
    print("Step 4: Fetching and exporting latest exercise...")
    
    if not polar_user_id:
        raise Exception("Polar user ID unavailable. Cannot proceed with exercise fetch.")
    
    # List exercises
    exercises = list_exercises(
        access_token=access_token,
        api_base=config['API_BASE']
    )
    
    tcx_dataframe = None
    latest_exercise = None
    
    if exercises:
        # Display exercises
        display_exercises(exercises)
        
        # Select latest
        latest_exercise = select_latest_exercise(exercises)
        
        if latest_exercise:
            exercise_id = get_field(latest_exercise, 'id', 'exercise_id')
            
            # Download TCX
            tcx_dataframe = download_exercise_tcx(
                exercise_id=exercise_id,
                polar_user_id=polar_user_id,
                access_token=access_token,
                output_dir=output_dir,
                api_base=config['API_BASE']
            )
    else:
        print("ℹ No exercises available to download.")
    
    print()
    print("="*80)
    print("✓ WORKFLOW COMPLETE")
    print("="*80)
    
    return {
        'config': config,
        'polar_user_id': polar_user_id,
        'access_token': access_token,
        'exercises': exercises,
        'latest_exercise': latest_exercise,
        'tcx_dataframe': tcx_dataframe
    }


__all__ = [
    # Configuration
    'load_configuration',

    # Token management
    'save_tokens',
    'load_tokens',
    'encode_credentials',
    'exchange_code_for_token',
    'refresh_access_token',
    'ensure_token',

    # User management
    'get_user_info',
    'register_user',

    # OAuth flow
    'create_callback_handler',
    'start_callback_server',
    'run_authorization_flow',
    'complete_token_exchange',

    # TCX conversion
    'convert_tcx_to_csv',

    # Exercise management
    'get_physical_info',
    'get_field',
    'normalize_start_time',
    'list_exercises',
    'display_exercises',
    'select_latest_exercise',
    'download_exercise_tcx',
    'fetch_and_export_latest_exercise',

    # Validation
    'is_token_valid',
    'run_validation_checks',

    # Complete workflow
    'run_polar_workflow',
]
