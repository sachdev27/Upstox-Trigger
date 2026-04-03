"""
Auth Service — handles Upstox OAuth2 flow, token management, and auto-refresh.

Tokens are persisted to the database (config_settings table), NOT .env.
"""

import logging
import json
from pathlib import Path

import jwt
from jwt import api_jwt
from datetime import datetime, timezone

import upstox_client

from app.config import get_settings, BASE_DIR
from app.network_proxy import patch_upstox_sdk_socks_support

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
            return self._build_sdk_config(access_token=self.settings.SANDBOX_ACCESS_TOKEN)

        # Live Mode logic (with refresh)
        if self._is_token_expired(self.settings.ACCESS_TOKEN):
            logger.info("Live access token appears expired — attempting refresh...")
            refreshed = self._refresh_token(use_sandbox=False)
            if not refreshed and self.settings.ACCESS_TOKEN:
                logger.info("Proceeding with existing live access token.")

        config = self._build_sdk_config(access_token=self.settings.ACCESS_TOKEN)
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

        token = self._exchange_code_for_token(auth_code, use_sandbox=self.settings.USE_SANDBOX)
        if token:
            key = "SANDBOX_ACCESS_TOKEN" if self.settings.USE_SANDBOX else "ACCESS_TOKEN"
            self.settings.save_to_db(key, token, category="API", is_secret=True)
            if self.settings.USE_SANDBOX:
                self.settings.SANDBOX_ACCESS_TOKEN = token
            else:
                self.settings.ACCESS_TOKEN = token

            # Mark code as used immediately to prevent redundant refresh attempts
            self.settings.save_to_db("AUTH_CODE", "USED", category="API", is_secret=True)
            self.settings.AUTH_CODE = "USED"
            logger.info(f"Successfully obtained and persisted new {key} to DB.")
        return token

    def validate_token(self, use_sandbox: bool | None = None) -> tuple[bool, str | None]:
        """
        Validate token by making a lightweight authenticated API call.

        Returns:
            (is_valid, reason)
        """
        # Keep settings in sync with persisted state before validating.
        self.settings.load_from_db()

        target_sandbox = use_sandbox if use_sandbox is not None else self.settings.USE_SANDBOX
        token = self.settings.SANDBOX_ACCESS_TOKEN if target_sandbox else self.settings.ACCESS_TOKEN

        if self._is_token_expired(token):
            return False, "expired"

        try:
            config = self._build_sdk_config(access_token=token)
            api = upstox_client.UserApi(upstox_client.ApiClient(config))
            api.get_profile(self.settings.API_VERSION)
            return True, None
        except Exception as e:
            if self._is_invalid_token_error(e):
                return False, "invalid"
            return False, f"auth_check_failed: {e}"

    # ── Internal ────────────────────────────────────────────────

    def _is_token_expired(self, token: str) -> bool:
        """Check if a JWT access token is expired."""
        if not token or token == "None":
            return True
        # Upstox token formats may vary by flow/version; if token is not JWT-like,
        # skip local-expiry heuristic and let API calls validate it.
        if token.count(".") != 2:
            return False
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
            return False

    def _refresh_token(self, use_sandbox: bool = False) -> bool:
        """
        Exchange the stored auth code for a new access token.
        NOTE: Auth codes are single-use. We should only attempt this if the code hasn't been used.
        """
        if not self.settings.AUTH_CODE or self.settings.AUTH_CODE == "USED":
            logger.info("Auth code missing or already used; skipping refresh attempt.")
            return False

        token = self._exchange_code_for_token(self.settings.AUTH_CODE, use_sandbox=use_sandbox)
        if token:
            key = "SANDBOX_ACCESS_TOKEN" if use_sandbox else "ACCESS_TOKEN"
            self.settings.save_to_db(key, token, category="API", is_secret=True)

            # Mark code as used to prevent infinite loop on failure
            self.settings.save_to_db("AUTH_CODE", "USED", category="API", is_secret=True)
            self.settings.AUTH_CODE = "USED"

            if use_sandbox:
                self.settings.SANDBOX_ACCESS_TOKEN = token
            else:
                self.settings.ACCESS_TOKEN = token
            logger.info("Successfully swapped single-use code for access token.")
            return True
        else:
            # If exchange failed, the code might still be invalid/used
            logger.error(
                "Failed to exchange auth code. Visit the auth URL to re-authorize:\n"
                f"  {self.get_auth_url()}"
            )
            return False

    def _exchange_code_for_token(self, auth_code: str, use_sandbox: bool = False) -> str | None:
        """Call Upstox token endpoint to exchange auth code for access token."""
        try:
            config = self._build_sdk_config()
            api_instance = upstox_client.LoginApi(
                upstox_client.ApiClient(config)
            )
            client_id = self.settings.SANDBOX_API_KEY if use_sandbox else self.settings.API_KEY
            client_secret = self.settings.SANDBOX_API_SECRET if use_sandbox else self.settings.API_SECRET

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

    def _is_invalid_token_error(self, err: Exception) -> bool:
        """Detect invalid/revoked token responses from Upstox errors."""
        text = str(err) or ""
        if "UDAPI100050" in text or "Invalid token" in text:
            return True

        # Best-effort parse of SDK exception body snippets.
        marker = "HTTP response body:"
        if marker in text:
            try:
                body = text.split(marker, 1)[1].strip()
                if body.startswith("b'") and body.endswith("'"):
                    body = body[2:-1]
                payload = json.loads(body)
                errors = payload.get("errors") or []
                for item in errors:
                    if str(item.get("errorCode") or item.get("error_code") or "") == "UDAPI100050":
                        return True
            except Exception:
                pass
        return False

    def _build_sdk_config(self, access_token: str | None = None) -> upstox_client.Configuration:
        """Construct SDK configuration with optional token and settings-driven proxy behavior."""
        config = upstox_client.Configuration()
        if access_token:
            config.access_token = access_token

        # Backward-compatible proxy resolution:
        # - APPLY_UPSTOX_SDK_PROXY explicitly enables SDK proxying.
        # - REQUIRE_UPSTOX_PROXY also implies SDK proxying for existing deployments.
        # - UPSTOX_PROXY_URL is preferred, but legacy REQUESTS proxies can still backfill it.
        proxy_url = (
            (self.settings.UPSTOX_PROXY_URL or "").strip()
            or (self.settings.REQUESTS_HTTPS_PROXY or "").strip()
            or (self.settings.REQUESTS_HTTP_PROXY or "").strip()
        )
        apply_sdk_proxy = bool(
            getattr(self.settings, "APPLY_UPSTOX_SDK_PROXY", False)
            or getattr(self.settings, "REQUIRE_UPSTOX_PROXY", False)
        )

        if self.settings.REQUIRE_UPSTOX_PROXY and not (apply_sdk_proxy and proxy_url):
            raise RuntimeError(
                "REQUIRE_UPSTOX_PROXY=True but SDK proxy is not enabled/configured. "
                "Set APPLY_UPSTOX_SDK_PROXY=True and UPSTOX_PROXY_URL."
            )

        if apply_sdk_proxy and proxy_url:
            patch_upstox_sdk_socks_support()
            config.proxy = proxy_url
            logger.debug("Upstox SDK proxy is enabled.")

        return config


# Module-level singleton
_auth_service: AuthService | None = None


def get_auth_service() -> AuthService:
    """Get or create the AuthService singleton."""
    global _auth_service
    if _auth_service is None:
        _auth_service = AuthService()
    return _auth_service
