#!/usr/bin/env python3
"""
Polar AccessLink CLI tool.

Command-line interface for interacting with the Polar AccessLink API.
Supports OAuth authentication, token management, and exercise data retrieval.
"""

import argparse
import json
import sys
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs
from pathlib import Path

# Add parent directory to path to import polar_accesslink
sys.path.insert(0, str(Path(__file__).parent.parent))

from polar_accesslink import PolarAccessLinkClient


def mask_token(token: str, visible_chars: int = 4) -> str:
    """
    Mask a token string for safe display.
    
    Args:
        token: Token to mask
        visible_chars: Number of characters to show at end
    
    Returns:
        Masked token string
    """
    if not token:
        return ""
    if len(token) <= visible_chars:
        return "*" * len(token)
    return "*" * (len(token) - visible_chars) + token[-visible_chars:]


def mask_tokens_in_dict(data: dict, allow_secrets: bool = False) -> dict:
    """
    Mask sensitive tokens in a dictionary.
    
    Args:
        data: Dictionary that may contain tokens
        allow_secrets: If True, don't mask tokens
    
    Returns:
        Dictionary with masked tokens
    """
    if allow_secrets:
        return data
    
    result = data.copy()
    sensitive_keys = ['access_token', 'refresh_token', 'token', 'secret']
    
    for key in result:
        if any(sensitive in key.lower() for sensitive in sensitive_keys):
            if isinstance(result[key], str):
                result[key] = mask_token(result[key])
    
    return result


def format_output(data: any, raw: bool = False, allow_secrets: bool = False) -> str:
    """
    Format output data as JSON.
    
    Args:
        data: Data to format
        raw: If True, use compact JSON format
        allow_secrets: If True, don't mask sensitive data
    
    Returns:
        JSON string
    """
    if isinstance(data, dict):
        data = mask_tokens_in_dict(data, allow_secrets)
    
    if raw:
        return json.dumps(data)
    else:
        return json.dumps(data, indent=2)


def cmd_auth_url(args, client: PolarAccessLinkClient) -> int:
    """Generate authorization URL."""
    url = client.build_authorize_url(args.redirect_uri, args.state)
    print(url)
    return 0


def cmd_start_auth_server(args, client: PolarAccessLinkClient) -> int:
    """Start local HTTP server to capture authorization code."""
    
    auth_code = None
    auth_error = None
    
    class CallbackHandler(BaseHTTPRequestHandler):
        """HTTP request handler for OAuth callback."""
        
        def do_GET(self):
            nonlocal auth_code, auth_error
            
            # Parse query parameters
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            
            # Check for authorization code
            if 'code' in params:
                auth_code = params['code'][0]
                self.send_response(200)
                self.send_header('Content-type', 'text/html')
                self.end_headers()
                self.wfile.write(b'''
                    <html>
                    <body>
                        <h1>Authorization Successful!</h1>
                        <p>You can close this window and return to the terminal.</p>
                    </body>
                    </html>
                ''')
            elif 'error' in params:
                auth_error = params['error'][0]
                self.send_response(400)
                self.send_header('Content-type', 'text/html')
                self.end_headers()
                self.wfile.write(f'''
                    <html>
                    <body>
                        <h1>Authorization Failed</h1>
                        <p>Error: {auth_error}</p>
                    </body>
                    </html>
                '''.encode())
            else:
                self.send_response(400)
                self.send_header('Content-type', 'text/html')
                self.end_headers()
                self.wfile.write(b'Invalid request')
        
        def log_message(self, format, *args):
            # Suppress default logging
            pass
    
    # Start server
    server = HTTPServer((args.host, args.redirect_port), CallbackHandler)
    redirect_uri = f"http://{args.host}:{args.redirect_port}/callback"
    
    print(f"Starting callback server on {args.host}:{args.redirect_port}", file=sys.stderr)
    print(f"Redirect URI: {redirect_uri}", file=sys.stderr)
    print(f"\nAuthorization URL:", file=sys.stderr)
    print(client.build_authorize_url(redirect_uri), file=sys.stderr)
    print(f"\nWaiting for authorization callback...", file=sys.stderr)
    
    # Handle one request
    while not auth_code and not auth_error:
        server.handle_request()
    
    server.server_close()
    
    if auth_error:
        print(json.dumps({"error": auth_error}))
        return 1
    
    if auth_code:
        print(json.dumps({"code": auth_code, "redirect_uri": redirect_uri}))
        return 0
    
    return 1


def cmd_exchange(args, client: PolarAccessLinkClient) -> int:
    """Exchange authorization code for tokens."""
    try:
        tokens = client.exchange_code(args.code, args.redirect_uri)
        print(format_output(tokens, args.raw, args.allow_secrets))
        return 0
    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        return 1


def cmd_refresh(args, client: PolarAccessLinkClient) -> int:
    """Refresh access token."""
    try:
        tokens = client.refresh(force=args.force)
        print(format_output(tokens, args.raw, args.allow_secrets))
        return 0
    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        return 1


def cmd_register(args, client: PolarAccessLinkClient) -> int:
    """Register user with Polar AccessLink."""
    try:
        result = client.register_user(args.member_id)
        print(format_output(result, args.raw, args.allow_secrets))
        return 0
    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        return 1


def cmd_latest_workout(args, client: PolarAccessLinkClient) -> int:
    """Get latest workout/exercise."""
    try:
        result = client.get_latest_exercise(args.user_id)
        
        if result is None:
            print(json.dumps({"message": "No new exercises available"}))
            return 0
        
        # Format summary
        if not args.raw:
            exercise = result.get('exercise', {})
            zones = result.get('zones')
            
            summary = {
                "transaction_id": result.get('transaction_id'),
                "exercise_id": result.get('exercise_id'),
                "start_time": exercise.get('start_time'),
                "duration": exercise.get('duration'),
                "distance": exercise.get('distance'),
                "has_zones": zones is not None,
            }
            
            if zones:
                summary["zones"] = zones
            
            print(format_output(summary, args.raw, args.allow_secrets))
        else:
            print(format_output(result, args.raw, args.allow_secrets))
        
        return 0
    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        return 1


def cmd_zones(args, client: PolarAccessLinkClient) -> int:
    """Get heart rate zones for an exercise."""
    try:
        zones = client.get_exercise_zones(
            args.user_id,
            args.transaction_id,
            args.exercise_id
        )
        
        if zones is None:
            print(json.dumps({"message": "No zones available for this exercise"}))
            return 0
        
        print(format_output(zones, args.raw, args.allow_secrets))
        return 0
    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        return 1


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Polar AccessLink CLI tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Environment Variables:
  POLAR_CLIENT_ID       OAuth client ID (required)
  POLAR_CLIENT_SECRET   OAuth client secret (required)
  POLAR_MEMBER_ID       Polar member ID (optional)
  POLAR_TOKEN_PATH      Path to token store (default: tokens_polar.json)
  POLAR_REFRESH_MARGIN  Token refresh margin in seconds (default: 90)

Examples:
  # Generate authorization URL
  %(prog)s auth-url --redirect-uri http://localhost:8721/callback
  
  # Start local server to capture auth code
  %(prog)s start-auth-server --redirect-port 8721
  
  # Exchange code for tokens
  %(prog)s exchange --code ABC123 --redirect-uri http://localhost:8721/callback
  
  # Refresh tokens
  %(prog)s refresh
  
  # Register user
  %(prog)s register --member-id 12345678
  
  # Get latest workout
  %(prog)s latest-workout --user-id 12345678
        """
    )
    
    parser.add_argument('--raw', action='store_true', help='Output raw JSON (compact)')
    parser.add_argument('--allow-secrets', action='store_true', 
                        help='Show full tokens (use with --raw)')
    
    subparsers = parser.add_subparsers(dest='command', help='Command to execute')
    subparsers.required = True
    
    # auth-url command
    auth_url_parser = subparsers.add_parser('auth-url', help='Generate authorization URL')
    auth_url_parser.add_argument('--redirect-uri', required=True, help='OAuth redirect URI')
    auth_url_parser.add_argument('--state', help='Optional state parameter for CSRF protection')
    auth_url_parser.set_defaults(func=cmd_auth_url)
    
    # start-auth-server command
    server_parser = subparsers.add_parser('start-auth-server', 
                                          help='Start local server to capture auth code')
    server_parser.add_argument('--redirect-port', type=int, default=8721,
                              help='Port for callback server (default: 8721)')
    server_parser.add_argument('--host', default='127.0.0.1',
                              help='Host for callback server (default: 127.0.0.1)')
    server_parser.set_defaults(func=cmd_start_auth_server)
    
    # exchange command
    exchange_parser = subparsers.add_parser('exchange', help='Exchange auth code for tokens')
    exchange_parser.add_argument('--code', required=True, help='Authorization code')
    exchange_parser.add_argument('--redirect-uri', required=True, help='Redirect URI used in auth')
    exchange_parser.set_defaults(func=cmd_exchange)
    
    # refresh command
    refresh_parser = subparsers.add_parser('refresh', help='Refresh access token')
    refresh_parser.add_argument('--force', action='store_true', 
                               help='Force refresh even if not expired')
    refresh_parser.set_defaults(func=cmd_refresh)
    
    # register command
    register_parser = subparsers.add_parser('register', help='Register user')
    register_parser.add_argument('--member-id', help='Polar member ID')
    register_parser.set_defaults(func=cmd_register)
    
    # latest-workout command
    latest_parser = subparsers.add_parser('latest-workout', help='Get latest workout')
    latest_parser.add_argument('--user-id', required=True, help='Polar user ID')
    latest_parser.set_defaults(func=cmd_latest_workout)
    
    # zones command
    zones_parser = subparsers.add_parser('zones', help='Get exercise heart rate zones')
    zones_parser.add_argument('--user-id', required=True, help='Polar user ID')
    zones_parser.add_argument('--transaction-id', required=True, help='Transaction ID')
    zones_parser.add_argument('--exercise-id', required=True, help='Exercise ID')
    zones_parser.set_defaults(func=cmd_zones)
    
    args = parser.parse_args()
    
    # Create client
    try:
        client = PolarAccessLinkClient.from_env()
    except Exception as e:
        print(json.dumps({"error": f"Failed to create client: {e}"}), file=sys.stderr)
        return 1
    
    # Execute command
    try:
        return args.func(args, client)
    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
