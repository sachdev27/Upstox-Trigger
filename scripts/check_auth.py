#!/usr/bin/env python3
"""
Check Auth — validates token expiry, API key presence, and OAuth flow readiness.
Run: python -m scripts.check_auth  (from project root)
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import get_settings
from app.auth.service import AuthService

def check_auth():
    settings = get_settings()
    settings.load_from_db()
    auth = AuthService()
    
    results = {"pass": [], "fail": [], "warn": []}

    # 1. API Key
    if settings.API_KEY and len(settings.API_KEY) > 10:
        results["pass"].append(f"✅ API_KEY present: {settings.API_KEY[:8]}...")
    else:
        results["fail"].append("❌ API_KEY missing or too short")

    # 2. API Secret
    if settings.API_SECRET and len(settings.API_SECRET) > 4:
        results["pass"].append("✅ API_SECRET present")
    else:
        results["fail"].append("❌ API_SECRET missing or too short")

    # 3. Redirect URI
    if settings.REDIRECT_URI and "callback" in settings.REDIRECT_URI:
        results["pass"].append(f"✅ REDIRECT_URI: {settings.REDIRECT_URI}")
    else:
        results["warn"].append(f"⚠️ REDIRECT_URI looks unusual: {settings.REDIRECT_URI}")

    # 4. Access Token
    if settings.ACCESS_TOKEN and len(settings.ACCESS_TOKEN) > 20:
        expired = auth._is_token_expired(settings.ACCESS_TOKEN)
        if expired:
            results["fail"].append("❌ ACCESS_TOKEN is EXPIRED — re-authorize required")
            results["warn"].append(f"   Auth URL: {auth.get_auth_url()}")
        else:
            results["pass"].append("✅ ACCESS_TOKEN is valid (not expired)")
    else:
        results["fail"].append("❌ ACCESS_TOKEN missing — login required")
        results["warn"].append(f"   Auth URL: {auth.get_auth_url()}")

    # 5. Sandbox Config
    if settings.USE_SANDBOX:
        if settings.SANDBOX_ACCESS_TOKEN:
            results["pass"].append("✅ Sandbox mode: token present")
        else:
            results["fail"].append("❌ Sandbox mode enabled but SANDBOX_ACCESS_TOKEN is empty")
    else:
        results["pass"].append("✅ Running in LIVE mode")

    # Print
    print("\n" + "=" * 50)
    print("🔐 AUTH CHECK RESULTS")
    print("=" * 50)
    for msg in results["pass"]:
        print(msg)
    for msg in results["warn"]:
        print(msg)
    for msg in results["fail"]:
        print(msg)
    print(f"\n📊 {len(results['pass'])} passed, {len(results['warn'])} warnings, {len(results['fail'])} failed")
    print("=" * 50)
    
    return len(results["fail"]) == 0


if __name__ == "__main__":
    success = check_auth()
    sys.exit(0 if success else 1)
