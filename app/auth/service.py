"""
Auth Service — handles Upstox OAuth2 flow, token management, and auto-refresh.

Tokens are persisted to the database (config_settings table), NOT .env.
"""

import logging
from pathlib import Path

import jwt
from jwt import api_jwt
from datetime import datetime, timezone

import upstox_client

from app.config import get_settings, BASE_DIR

logger = logging.getLogger(__name__)


class AuthService:
    """Manages Upstox OAuth2 authentication lifecycle."""

    def __init__(self):
        self.settings = get_settings()
        self._configuration: upstox_client.Configuration | None = None

    # ── Public API ──────────────────────────────────────────────

    def get_configuration(self, use_sandbox: bool | None = None) -> upstox_client.Configuration:
        """
        Return a ready-to-use Upstox SDK Configuration object.
        Automatically refreshes the token if expired for Live mode.
        """
        target_sandbox = use_sandbox if use_sandbox is not None else False

        if target_sandbox:
            logger.debug("Creating Upstox SANDBOX configuration.")
            config = upstox_client.Configuration()
            config.access_token = self.settings.SANDBOX_ACCESS_TOKEN
            return config

        # Live Mode logic (with refresh)
        if self._is_token_expired(self.settings.ACCESS_TOKEN):
            logger.info("Access token expired — refreshing...")
            self._refresh_token()

        config = upstox_client.Configuration()
        config.access_token = self.settings.ACCESS_TOKEN
        self._configuration = config
        return config

    def get_auth_url(self) -> str:
        """Generate the Upstox login URL for the user."""
        client_id = self.settings.SANDBOX_API_KEY if self.settings.USE_SANDBOX else self.settings.API_KEY
        return (
            f"https://api.upstox.com/v2/login/authorization/dialog"
            f"?response_type=code"
            f"&client_id={client_id}"
            f"&redirect_uri={self.settings.REDIRECT_URI}"
        )

    def handle_callback(self, auth_code: str) -> str:
        """
        Handle the OAuth callback — exchange auth code for access token.
        Returns the new access token. Persists to DB.
        """
        # Save auth code to DB
        self.settings.save_to_db("AUTH_CODE", auth_code, category="API", is_secret=True)
        self.settings.AUTH_CODE = auth_code

        token = self._exchange_code_for_token(auth_code)
        if token:
            # Save access token to DB
            self.settings.save_to_db("ACCESS_TOKEN", token, category="API", is_secret=True)
            self.settings.ACCESS_TOKEN = token
            logger.info("Successfully obtained and persisted new access token to DB.")
        return token

    # ── Internal ────────────────────────────────────────────────

    def _is_token_expired(self, token: str) -> bool:
        """Check if a JWT access token is expired."""
        if not token or token == "None":
            return True
        try:
            decoded = api_jwt.decode(
                jwt=token, algorithms=["HS256"],
                options={"verify_signature": False}
            )
            exp_dt = datetime.fromtimestamp(
                decoded["exp"], tz=timezone.utc
            )
            return exp_dt < datetime.now(timezone.utc)
        except (jwt.ExpiredSignatureError, jwt.InvalidTokenError, KeyError):
            return True

    def _refresh_token(self):
        """Exchange the stored auth code for a new access token."""
        token = self._exchange_code_for_token(self.settings.AUTH_CODE)
        if token:
            self.settings.save_to_db("ACCESS_TOKEN", token, category="API", is_secret=True)
            self.settings.ACCESS_TOKEN = token
        else:
            logger.error(
                "Failed to refresh token. Visit the auth URL to re-authorize:\n"
                f"  {self.get_auth_url()}"
            )

    def _exchange_code_for_token(self, auth_code: str) -> str | None:
        """Call Upstox token endpoint to exchange auth code for access token."""
        try:
            config = upstox_client.Configuration()
            api_instance = upstox_client.LoginApi(
                upstox_client.ApiClient(config)
            )
            client_id = self.settings.SANDBOX_API_KEY if self.settings.USE_SANDBOX else self.settings.API_KEY
            client_secret = self.settings.SANDBOX_API_SECRET if self.settings.USE_SANDBOX else self.settings.API_SECRET
            
            response = api_instance.token(
                self.settings.API_VERSION,
                code=auth_code,
                client_id=client_id,
                client_secret=client_secret,
                redirect_uri=self.settings.REDIRECT_URI,
                grant_type="authorization_code",
            )
            return response.access_token
        except Exception as e:
            logger.error(f"Token exchange failed: {e}")
            return None


# Module-level singleton
_auth_service: AuthService | None = None


def get_auth_service() -> AuthService:
    """Get or create the AuthService singleton."""
    global _auth_service
    if _auth_service is None:
        _auth_service = AuthService()
    return _auth_service
