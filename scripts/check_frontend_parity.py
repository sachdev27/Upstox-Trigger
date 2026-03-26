#!/usr/bin/env python3
"""
Check Frontend Parity — ensures every api.js endpoint exists in the backend.
Run: python -m scripts.check_frontend_parity  (from project root)

Parses api.js to extract all fetch URLs, then checks them against FastAPI routes.
"""

import sys
import os
import re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path


def check_frontend_parity():
    results = {"pass": [], "fail": [], "warn": []}

    # 1. Parse api.js for all fetch URLs
    api_js = Path(__file__).parent.parent / "frontend" / "js" / "api.js"
    if not api_js.exists():
        results["fail"].append(f"❌ api.js not found at {api_js}")
        _print_results(results)
        return False

    content = api_js.read_text()
    
    # Extract all fetch URL patterns
    fetch_urls = re.findall(r'fetch\(`\$\{API_BASE\}([^`\$]*?)[\?`]', content)
    fetch_urls = list(set(fetch_urls))  # dedupe

    # 2. Get all registered FastAPI routes
    from app.main import app

    registered_routes = set()
    for route in app.routes:
        if hasattr(route, "path"):
            registered_routes.add(route.path)

    # 3. Compare
    for url in sorted(fetch_urls):
        # Strip query params
        clean = url.split("?")[0].rstrip("/")
        if not clean:
            continue

        if clean in registered_routes:
            results["pass"].append(f"✅ {clean}")
        else:
            # Check if it's a parameterized route
            found = False
            for route in registered_routes:
                if "{" in route:
                    pattern = re.sub(r'\{[^}]+\}', '[^/]+', route)
                    if re.match(pattern, clean):
                        found = True
                        break
            if found:
                results["pass"].append(f"✅ {clean} (matched parameterized)")
            else:
                results["fail"].append(f"❌ {clean} — NOT FOUND in backend routes")

    # 4. Show registered routes not used by frontend
    used_paths = set()
    for url in fetch_urls:
        clean = url.split("?")[0].rstrip("/")
        if clean:
            used_paths.add(clean)

    unused = registered_routes - used_paths - {"/", "/docs", "/openapi.json", "/redoc", "/ws", "/dashboard", "/callback", "/callback/", "/health", "/static"}
    if unused:
        results["warn"].append(f"\n⚠️ Backend routes not used by frontend ({len(unused)}):")
        for route in sorted(unused):
            if not route.startswith("/static"):
                results["warn"].append(f"   - {route}")

    _print_results(results)
    return len(results["fail"]) == 0


def _print_results(results):
    print("\n" + "=" * 60)
    print("🔗 FRONTEND-BACKEND PARITY CHECK")
    print("=" * 60)
    for msg in results["pass"]:
        print(msg)
    for msg in results["warn"]:
        print(msg)
    for msg in results["fail"]:
        print(msg)
    print(f"\n📊 {len(results['pass'])} matched, {len(results['warn'])} warnings, {len(results['fail'])} missing")
    print("=" * 60)


if __name__ == "__main__":
    success = check_frontend_parity()
    sys.exit(0 if success else 1)
