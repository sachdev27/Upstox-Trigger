"""Verify outbound public IP via configured proxy.

Usage:
  REQUESTS_HTTP_PROXY='socks5h://user:pass@host:1080' \
  REQUESTS_HTTPS_PROXY='socks5h://user:pass@host:1080' \
  ./venv/bin/python scripts/verify_proxy_ip.py
"""

from __future__ import annotations

import os
import sys
import requests


def main() -> int:
    http_proxy = (os.getenv("REQUESTS_HTTP_PROXY") or "").strip()
    https_proxy = (os.getenv("REQUESTS_HTTPS_PROXY") or "").strip()

    if not https_proxy:
        print("REQUESTS_HTTPS_PROXY is not set.")
        return 1

    proxies = {
        "http": http_proxy or https_proxy,
        "https": https_proxy,
    }

    try:
        resp = requests.get("https://api.ipify.org", proxies=proxies, timeout=20)
        resp.raise_for_status()
        print(resp.text.strip())
        return 0
    except Exception as exc:
        print(f"Proxy verification failed: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
