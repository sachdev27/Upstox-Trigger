"""
Auth API routes — handles login flow via the browser.
"""

from fastapi import APIRouter, Query
from fastapi.responses import RedirectResponse

from app.auth.service import get_auth_service

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.get("/login")
async def login():
    """Return an HTTP redirect to the Upstox login URL."""
    auth = get_auth_service()
    return RedirectResponse(url=auth.get_auth_url())


@router.get("/callback")
async def callback(code: str = Query(...)):
    """
    OAuth2 callback — Upstox redirects here after login.
    Exchanges the auth code for an access token.
    """
    auth = get_auth_service()
    token = auth.handle_callback(code)
    # Redirect user back to the dashboard after authentication
    return RedirectResponse(url="/dashboard")


@router.get("/status")
async def auth_status():
    """Check if the current access token is valid."""
    auth = get_auth_service()
    is_expired = auth._is_token_expired(auth.settings.ACCESS_TOKEN)
    return {
        "authenticated": not is_expired,
        "auth_url": auth.get_auth_url() if is_expired else None,
    }
