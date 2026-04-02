#!/usr/bin/env python3
"""Measure request latency through the configured proxy.

Usage:
  /Users/diviine/Projects/Upstox-Trigger/.venv/bin/python scripts/check_proxy_speed.py
  /Users/diviine/Projects/Upstox-Trigger/.venv/bin/python scripts/check_proxy_speed.py --runs 10
"""

from __future__ import annotations

import argparse
import json
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app.config import get_settings


DEFAULT_TARGETS = [
    ("ipify", "https://api.ipify.org"),
    (
        "upstox-login",
        "https://api.upstox.com/v2/login/authorization/dialog?response_type=code&client_id=dummy&redirect_uri=http://localhost",
    ),
]


def make_session(proxies: dict[str, str] | None) -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    if proxies:
        session.proxies.update(proxies)
    return session


def time_request(session: requests.Session, url: str, timeout: float) -> tuple[bool, float, str]:
    start = time.perf_counter()
    try:
        response = session.get(url, timeout=timeout, allow_redirects=False)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        return True, elapsed_ms, f"HTTP {response.status_code}"
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        return False, elapsed_ms, str(exc)


def summarize(samples: list[float]) -> str:
    if not samples:
        return "n/a"
    avg = statistics.mean(samples)
    low = min(samples)
    high = max(samples)
    med = statistics.median(samples)
    return f"avg={avg:.1f}ms median={med:.1f}ms min={low:.1f}ms max={high:.1f}ms"


def curl_timing(url: str, proxy_url: str | None, timeout: float) -> dict | None:
    if not shutil.which("curl"):
        return None

    fmt = json.dumps(
        {
            "dns_ms": "%{time_namelookup}",
            "connect_ms": "%{time_connect}",
            "tls_ms": "%{time_appconnect}",
            "ttfb_ms": "%{time_starttransfer}",
            "total_ms": "%{time_total}",
            "http_code": "%{http_code}",
        }
    )

    cmd = [
        "curl",
        "-sS",
        "-o",
        "/dev/null",
        "--max-time",
        str(timeout),
        "--write-out",
        fmt,
    ]

    if proxy_url:
        cmd.extend(["--proxy", proxy_url])
    else:
        cmd.extend(["--noproxy", "*"])

    cmd.append(url)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        payload = json.loads(result.stdout)
        return {
            "dns_ms": float(payload["dns_ms"]) * 1000.0,
            "connect_ms": float(payload["connect_ms"]) * 1000.0,
            "tls_ms": float(payload["tls_ms"]) * 1000.0,
            "ttfb_ms": float(payload["ttfb_ms"]) * 1000.0,
            "total_ms": float(payload["total_ms"]) * 1000.0,
            "http_code": payload["http_code"],
        }
    except Exception:
        return None


def run_probe(label: str, session: requests.Session, url: str, runs: int, timeout: float) -> tuple[list[float], list[str]]:
    timings: list[float] = []
    errors: list[str] = []
    for _ in range(runs):
        ok, elapsed_ms, info = time_request(session, url, timeout)
        if ok:
            timings.append(elapsed_ms)
        else:
            errors.append(f"{info} ({elapsed_ms:.1f}ms)")
    return timings, errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure proxy speed and compare against direct requests.")
    parser.add_argument("--runs", type=int, default=5, help="Number of requests per target")
    parser.add_argument("--timeout", type=float, default=15.0, help="Per-request timeout in seconds")
    args = parser.parse_args()

    settings = get_settings()
    settings.load_from_db()

    proxy_url_http = (settings.REQUESTS_HTTP_PROXY or settings.UPSTOX_PROXY_URL or "").strip()
    proxy_url_https = (settings.REQUESTS_HTTPS_PROXY or settings.UPSTOX_PROXY_URL or "").strip()
    proxies = {
        "http": proxy_url_http,
        "https": proxy_url_https,
    }

    print("=" * 72)
    print("Proxy Speed Check")
    print("=" * 72)
    print(f"Runs per target: {args.runs}")
    print(f"HTTP proxy: {'configured' if proxy_url_http else 'missing'}")
    print(f"HTTPS proxy: {'configured' if proxy_url_https else 'missing'}")

    direct_session = make_session(None)
    proxy_session = make_session(proxies if (proxy_url_http or proxy_url_https) else None)

    exit_code = 0
    for name, url in DEFAULT_TARGETS:
        print(f"\nTarget: {name}")
        print(f"URL: {url}")

        direct_timings, direct_errors = run_probe("direct", direct_session, url, args.runs, args.timeout)
        print(f"Direct : {summarize(direct_timings)}")
        if direct_errors:
            print(f"Direct errors: {len(direct_errors)}; first={direct_errors[0]}")

        if proxy_url_http or proxy_url_https:
            proxy_timings, proxy_errors = run_probe("proxy", proxy_session, url, args.runs, args.timeout)
            print(f"Proxy  : {summarize(proxy_timings)}")
            if proxy_errors:
                print(f"Proxy errors: {len(proxy_errors)}; first={proxy_errors[0]}")
                exit_code = 1
            if direct_timings and proxy_timings:
                delta = statistics.mean(proxy_timings) - statistics.mean(direct_timings)
                print(f"Delta  : {delta:+.1f}ms (proxy - direct)")
        else:
            print("Proxy  : skipped (proxy not configured)")
            exit_code = 1

        direct_curl = curl_timing(url, None, args.timeout)
        proxy_curl = curl_timing(url, proxy_url_https or proxy_url_http or None, args.timeout)
        if direct_curl and proxy_curl:
            print(
                "Breakdown direct : "
                f"dns={direct_curl['dns_ms']:.1f}ms connect={direct_curl['connect_ms']:.1f}ms "
                f"tls={direct_curl['tls_ms']:.1f}ms ttfb={direct_curl['ttfb_ms']:.1f}ms total={direct_curl['total_ms']:.1f}ms"
            )
            print(
                "Breakdown proxy  : "
                f"dns={proxy_curl['dns_ms']:.1f}ms connect={proxy_curl['connect_ms']:.1f}ms "
                f"tls={proxy_curl['tls_ms']:.1f}ms ttfb={proxy_curl['ttfb_ms']:.1f}ms total={proxy_curl['total_ms']:.1f}ms"
            )

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())