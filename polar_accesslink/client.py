"""
Polar AccessLink API Client with automated token refresh.
"""

import json
import os
import time
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
from urllib.parse import urlencode
import requests


class PolarAccessLinkClient:
    """
    Client for the Polar AccessLink API with automated token refresh.
    
    This client handles OAuth 2.0 authentication, token persistence,
    and automated token refresh based on expiration time.
    
    Example:
        >>> client = PolarAccessLinkClient.from_env()
        >>> url = client.build_authorize_url("http://localhost:8721/callback")
        >>> # ... after user authorization ...
        >>> tokens = client.exchange_code(code, "http://localhost:8721/callback")
        >>> user_info = client.register_user()
    """
    
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        member_id: Optional[str] = None,
        token_store_path: str = "tokens_polar.json",
        base_url: str = "https://www.polaraccesslink.com",
        auth_url: str = "https://flow.polar.com/oauth2/authorization",
        token_url: str = "https://www.polaraccesslink.com/v3/oauth2/token",
        auto_refresh: bool = True,
        refresh_margin_seconds: int = 90,
    ):
        """
        Initialize the Polar AccessLink client.
        
        Args:
            client_id: OAuth client ID from Polar developer portal
            client_secret: OAuth client secret from Polar developer portal
            member_id: Optional Polar member ID for user registration
            token_store_path: Path to JSON file for token persistence
            base_url: Base URL for Polar AccessLink API
            auth_url: OAuth authorization endpoint
            token_url: OAuth token endpoint
            auto_refresh: Automatically refresh tokens when near expiry
            refresh_margin_seconds: Refresh tokens this many seconds before expiry
        """
        self.client_id = client_id
        self.client_secret = client_secret
        self.member_id = member_id
        self.token_store_path = Path(token_store_path)
        self.base_url = base_url.rstrip('/')
        self.auth_url = auth_url
        self.token_url = token_url
        self.auto_refresh = auto_refresh
        self.refresh_margin_seconds = refresh_margin_seconds
        
        # Load existing tokens if available
        self._tokens: Optional[Dict[str, Any]] = None
        if self.token_store_path.exists():
            self._load_tokens()
    
    @classmethod
    def from_env(cls) -> 'PolarAccessLinkClient':
        """
        Create a client instance from environment variables.
        
        Required environment variables:
            - POLAR_CLIENT_ID
            - POLAR_CLIENT_SECRET
        
        Optional environment variables:
            - POLAR_MEMBER_ID
            - POLAR_TOKEN_PATH (default: tokens_polar.json)
            - POLAR_REFRESH_MARGIN (default: 90)
        
        Returns:
            PolarAccessLinkClient instance
        
        Raises:
            RuntimeError: If required environment variables are not set
        """
        client_id = os.getenv('POLAR_CLIENT_ID')
        client_secret = os.getenv('POLAR_CLIENT_SECRET')
        
        if not client_id or not client_secret:
            raise RuntimeError(
                "POLAR_CLIENT_ID and POLAR_CLIENT_SECRET environment variables are required"
            )
        
        return cls(
            client_id=client_id,
            client_secret=client_secret,
            member_id=os.getenv('POLAR_MEMBER_ID'),
            token_store_path=os.getenv('POLAR_TOKEN_PATH', 'tokens_polar.json'),
            refresh_margin_seconds=int(os.getenv('POLAR_REFRESH_MARGIN', '90')),
        )
    
    def _load_tokens(self) -> None:
        """Load tokens from the token store file."""
        try:
            with open(self.token_store_path, 'r') as f:
                self._tokens = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            raise RuntimeError(f"Failed to load tokens from {self.token_store_path}: {e}")
    
    def _save_tokens(self, tokens: Dict[str, Any]) -> None:
        """
        Save tokens to the token store file.
        
        Args:
            tokens: Token dictionary to save
        """
        self._tokens = tokens
        with open(self.token_store_path, 'w') as f:
            json.dump(tokens, f, indent=2)
    
    def __repr__(self) -> str:
        """String representation with masked tokens."""
        token_status = "present" if self._tokens else "absent"
        return f"PolarAccessLinkClient(client_id={self.client_id!r}, tokens={token_status})"
    
    def build_authorize_url(self, redirect_uri: str, state: Optional[str] = None) -> str:
        """
        Build the OAuth authorization URL.
        
        Args:
            redirect_uri: Callback URL after authorization
            state: Optional state parameter for CSRF protection
        
        Returns:
            Authorization URL string
        """
        params = {
            'client_id': self.client_id,
            'redirect_uri': redirect_uri,
            'response_type': 'code',
        }
        
        if state:
            params['state'] = state
        
        return f"{self.auth_url}?{urlencode(params)}"
    
    def exchange_code(self, code: str, redirect_uri: str) -> Dict[str, Any]:
        """
        Exchange authorization code for access and refresh tokens.
        
        Args:
            code: Authorization code from OAuth callback
            redirect_uri: Redirect URI used in authorization (must match exactly)
        
        Returns:
            Token dictionary with access_token, refresh_token, expires_in, etc.
        
        Raises:
            RuntimeError: If token exchange fails
        """
        data = {
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': redirect_uri,
        }
        
        response = requests.post(
            self.token_url,
            data=data,
            auth=(self.client_id, self.client_secret),
            headers={'Content-Type': 'application/x-www-form-urlencoded'}
        )
        
        if response.status_code != 200:
            raise RuntimeError(
                f"Token exchange failed: {response.status_code} - {response.text}"
            )
        
        token_data = response.json()
        
        # Compute received_at and expires_at
        received_at = int(time.time())
        expires_in = token_data.get('expires_in', 0)
        expires_at = received_at + expires_in
        
        # Add tracking fields
        token_data['received_at'] = received_at
        token_data['expires_at'] = expires_at
        
        # Save tokens
        self._save_tokens(token_data)
        
        return token_data
    
    def refresh(self, force: bool = False) -> Dict[str, Any]:
        """
        Refresh the access token using the refresh token.
        
        Args:
            force: Force refresh even if token is not near expiry
        
        Returns:
            New token dictionary
        
        Raises:
            RuntimeError: If refresh fails or no refresh token available
        """
        if not self._tokens:
            raise RuntimeError("No tokens available. Please authenticate first.")
        
        refresh_token = self._tokens.get('refresh_token')
        if not refresh_token:
            raise RuntimeError("No refresh token available. Please re-authenticate.")
        
        # Check if refresh is needed (unless forced)
        if not force and self.auto_refresh:
            now = int(time.time())
            expires_at = self._tokens.get('expires_at', 0)
            if now < (expires_at - self.refresh_margin_seconds):
                # Token still valid, no refresh needed
                return self._tokens
        
        # Perform refresh
        data = {
            'grant_type': 'refresh_token',
            'refresh_token': refresh_token,
        }
        
        response = requests.post(
            self.token_url,
            data=data,
            auth=(self.client_id, self.client_secret),
            headers={'Content-Type': 'application/x-www-form-urlencoded'}
        )
        
        if response.status_code != 200:
            raise RuntimeError(
                f"Token refresh failed: {response.status_code} - {response.text}"
            )
        
        token_data = response.json()
        
        # Compute received_at and expires_at
        received_at = int(time.time())
        expires_in = token_data.get('expires_in', 0)
        expires_at = received_at + expires_in
        
        # Add tracking fields
        token_data['received_at'] = received_at
        token_data['expires_at'] = expires_at
        
        # Save new tokens
        self._save_tokens(token_data)
        
        return token_data
    
    def ensure_token(self) -> str:
        """
        Ensure a valid access token is available, refreshing if necessary.
        
        This method checks if the token is near expiry (based on refresh_margin_seconds)
        and automatically refreshes it if needed. This is the recommended way to get
        a valid token before making API calls.
        
        To simulate token near-expiry for testing, manually edit the tokens file
        and set expires_at to a value in the near future (e.g., current time + 60).
        
        Returns:
            Valid access token string
        
        Raises:
            RuntimeError: If no tokens available or refresh fails
        """
        if not self._tokens:
            raise RuntimeError(
                "No tokens available. Please authenticate first using exchange_code()."
            )
        
        # Check if token needs refresh
        if self.auto_refresh:
            now = int(time.time())
            expires_at = self._tokens.get('expires_at', 0)
            
            if now >= (expires_at - self.refresh_margin_seconds):
                # Token expired or near expiry, refresh it
                self.refresh(force=True)
        
        access_token = self._tokens.get('access_token')
        if not access_token:
            raise RuntimeError("No access token available in token store.")
        
        return access_token
    
    def _request(
        self,
        method: str,
        path: str,
        **kwargs
    ) -> requests.Response:
        """
        Make an authenticated request to the Polar API.
        
        This internal method automatically adds the Authorization header
        and ensures the token is valid before making the request.
        
        Args:
            method: HTTP method (GET, POST, etc.)
            path: API path (e.g., '/v3/users')
            **kwargs: Additional arguments to pass to requests
        
        Returns:
            Response object
        
        Raises:
            RuntimeError: If request fails
        """
        token = self.ensure_token()
        
        # Build full URL
        url = f"{self.base_url}{path}"
        
        # Add authorization header
        headers = kwargs.pop('headers', {})
        headers['Authorization'] = f'Bearer {token}'
        
        # Make request
        response = requests.request(method, url, headers=headers, **kwargs)
        
        return response
    
    def register_user(self, member_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Register a user with Polar AccessLink API.
        
        This method is idempotent - if the user is already registered (409 conflict),
        it will fetch and return the existing user information.
        
        Args:
            member_id: Optional Polar member ID. Uses instance member_id if not provided.
        
        Returns:
            Dictionary containing 'polar-user-id' and other user information
        
        Raises:
            RuntimeError: If registration fails with non-409 error
        """
        mid = member_id or self.member_id
        
        # Prepare registration data
        data = {}
        if mid:
            data['member-id'] = mid
        
        # Attempt registration
        response = self._request(
            'POST',
            '/v3/users',
            json=data,
            headers={'Content-Type': 'application/json', 'Accept': 'application/json'}
        )
        
        if response.status_code == 201:
            # Successfully created
            return response.json()
        elif response.status_code == 409:
            # User already exists
            # For 409, try to fetch user info if we have a numeric member_id
            if mid and mid.isdigit():
                # Try to get user info
                try:
                    user_response = self._request('GET', f'/v3/users/{mid}')
                    if user_response.status_code == 200:
                        return user_response.json()
                except Exception:
                    pass
            
            # Return a basic response indicating user exists
            return {
                'polar-user-id': mid,
                'message': 'User already registered',
                'status': 409
            }
        else:
            raise RuntimeError(
                f"User registration failed: {response.status_code} - {response.text}"
            )
    
    def get_user(self, user_id: str) -> Dict[str, Any]:
        """
        Get user information from Polar AccessLink API.
        
        Args:
            user_id: Polar user ID
        
        Returns:
            Dictionary containing user information
        
        Raises:
            RuntimeError: If request fails
        """
        response = self._request('GET', f'/v3/users/{user_id}')
        
        if response.status_code == 200:
            return response.json()
        else:
            raise RuntimeError(
                f"Get user failed: {response.status_code} - {response.text}"
            )
    
    def create_exercise_transaction(self, user_id: str) -> Tuple[Optional[str], bool]:
        """
        Create an exercise transaction to fetch new exercise data.
        
        Args:
            user_id: Polar user ID
        
        Returns:
            Tuple of (transaction_id, has_exercises)
            - transaction_id: Transaction ID if created, None for 204
            - has_exercises: True if there are exercises available
        
        Raises:
            RuntimeError: If request fails with error status
        """
        response = self._request(
            'POST',
            f'/v3/users/{user_id}/exercise-transactions',
            headers={'Accept': 'application/json'}
        )
        
        if response.status_code == 201:
            # Transaction created with exercises
            data = response.json()
            return data.get('transaction-id'), True
        elif response.status_code == 204:
            # No new exercises available
            return None, False
        else:
            raise RuntimeError(
                f"Create transaction failed: {response.status_code} - {response.text}"
            )
    
    def list_transaction_exercises(
        self,
        user_id: str,
        transaction_id: str
    ) -> List[str]:
        """
        List exercises available in a transaction.
        
        Args:
            user_id: Polar user ID
            transaction_id: Transaction ID from create_exercise_transaction
        
        Returns:
            List of exercise URLs
        
        Raises:
            RuntimeError: If request fails
        """
        response = self._request(
            'GET',
            f'/v3/users/{user_id}/exercise-transactions/{transaction_id}'
        )
        
        if response.status_code == 200:
            data = response.json()
            return data.get('exercises', [])
        else:
            raise RuntimeError(
                f"List exercises failed: {response.status_code} - {response.text}"
            )
    
    def get_exercise_zones(
        self,
        user_id: str,
        transaction_id: str,
        exercise_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get heart rate zones for a specific exercise.
        
        Args:
            user_id: Polar user ID
            transaction_id: Transaction ID
            exercise_id: Exercise ID
        
        Returns:
            Dictionary containing zone information, or None if no zones available
        
        Raises:
            RuntimeError: If request fails with error status
        """
        response = self._request(
            'GET',
            f'/v3/users/{user_id}/exercise-transactions/{transaction_id}/exercises/{exercise_id}/zones'
        )
        
        if response.status_code == 200:
            return response.json()
        elif response.status_code == 204:
            # No zones available
            return None
        else:
            raise RuntimeError(
                f"Get zones failed: {response.status_code} - {response.text}"
            )
    
    def commit_transaction(self, user_id: str, transaction_id: str) -> bool:
        """
        Commit an exercise transaction.
        
        Args:
            user_id: Polar user ID
            transaction_id: Transaction ID to commit
        
        Returns:
            True if transaction committed successfully
        
        Raises:
            RuntimeError: If request fails
        """
        response = self._request(
            'PUT',
            f'/v3/users/{user_id}/exercise-transactions/{transaction_id}'
        )
        
        if response.status_code == 204:
            return True
        else:
            raise RuntimeError(
                f"Commit transaction failed: {response.status_code} - {response.text}"
            )
    
    def get_latest_exercise(
        self,
        user_id: str,
        create_transaction: bool = True
    ) -> Optional[Dict[str, Any]]:
        """
        Get the latest exercise data for a user.
        
        This is a convenience method that creates a transaction, fetches the
        first exercise (if available), gets its zones, and commits the transaction.
        
        Args:
            user_id: Polar user ID
            create_transaction: If True, create and commit transaction automatically
        
        Returns:
            Dictionary with keys: exercise, zones, transaction_id
            Returns None if no exercises available
        
        Raises:
            RuntimeError: If any API call fails
        """
        if not create_transaction:
            raise ValueError("create_transaction must be True for this method")
        
        # Create transaction
        transaction_id, has_exercises = self.create_exercise_transaction(user_id)
        
        if not has_exercises or not transaction_id:
            return None
        
        try:
            # List exercises
            exercise_urls = self.list_transaction_exercises(user_id, transaction_id)
            
            if not exercise_urls:
                # Commit empty transaction
                self.commit_transaction(user_id, transaction_id)
                return None
            
            # Get first exercise
            first_exercise_url = exercise_urls[0]
            
            # Extract exercise ID from URL
            # URL format: /v3/users/{user_id}/exercise-transactions/{transaction_id}/exercises/{exercise_id}
            exercise_id = first_exercise_url.split('/')[-1]
            
            # Fetch exercise details
            exercise_response = self._request('GET', first_exercise_url)
            exercise_data = exercise_response.json() if exercise_response.status_code == 200 else {}
            
            # Get zones
            zones = self.get_exercise_zones(user_id, transaction_id, exercise_id)
            
            # Commit transaction
            self.commit_transaction(user_id, transaction_id)
            
            return {
                'exercise': exercise_data,
                'zones': zones,
                'transaction_id': transaction_id,
                'exercise_id': exercise_id
            }
        except Exception as e:
            # On any error, try to commit transaction to avoid leaving it open
            try:
                self.commit_transaction(user_id, transaction_id)
            except Exception:
                pass
            raise RuntimeError(f"Failed to get latest exercise: {e}")
