"""OAuth callback server and authorization flow for Polar AccessLink.

This module provides utilities for OAuth authentication including:
- Local callback server for capturing authorization codes
- Complete authorization flow with CSRF protection
- Token exchange after authorization
"""
from __future__ import annotations

import secrets
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Dict, Optional, Tuple
from urllib.parse import parse_qs, urlencode, urlparse

from polar.api.tokens import (
    save_tokens,
    exchange_code_for_token,
)


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
        print(f"⚠️  Port {port} is busy, trying to find an available port...")
        for try_port in range(port + 1, port + 100):
            try:
                server = HTTPServer(('localhost', try_port), CallbackHandler)
                actual_port = try_port
                print(f"✅ Using fallback port: {actual_port}")
                break
            except OSError:
                continue
        
        if server is None:
            raise RuntimeError("Could not find an available port")
    
    redirect_uri = f"http://localhost:{actual_port}/callback"
    print(f"✅ Callback server ready at: {redirect_uri}")
    
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
        print(f"\n✅ Authorization code captured: {result_storage['auth_code'][:8]}...")
        print(f"✅ Redirect URI used: {redirect_uri}")
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


__all__ = [
    'create_callback_handler',
    'start_callback_server',
    'run_authorization_flow',
    'complete_token_exchange',
]
