"""Centralized proxy behavior for SDK + process HTTP clients."""

from __future__ import annotations

import logging
import os
import ssl

import certifi

logger = logging.getLogger(__name__)


_SOCKS_PATCHED = False


def _set_env_if_value(key: str, value: str):
    if value:
        os.environ[key] = value


def _clear_proxy_env() -> None:
    for key in (
        "HTTP_PROXY", "http_proxy",
        "HTTPS_PROXY", "https_proxy",
        "ALL_PROXY", "all_proxy",
    ):
        os.environ.pop(key, None)


def apply_process_proxy_env(settings) -> None:
    """Set process-level proxy env for requests/websocket libraries that honor env vars."""
    upstox_proxy = (settings.UPSTOX_PROXY_URL or "").strip()
    http_proxy = (settings.REQUESTS_HTTP_PROXY or "").strip() or upstox_proxy
    https_proxy = (settings.REQUESTS_HTTPS_PROXY or "").strip() or upstox_proxy

    _set_env_if_value("HTTP_PROXY", http_proxy)
    _set_env_if_value("http_proxy", http_proxy)
    _set_env_if_value("HTTPS_PROXY", https_proxy)
    _set_env_if_value("https_proxy", https_proxy)

    # Keep a single fallback for libraries that only read ALL_PROXY.
    all_proxy = https_proxy or http_proxy
    _set_env_if_value("ALL_PROXY", all_proxy)
    _set_env_if_value("all_proxy", all_proxy)

    if all_proxy and not os.getenv("NO_PROXY") and not os.getenv("no_proxy"):
        os.environ["NO_PROXY"] = "localhost,127.0.0.1"


def get_requests_proxies(settings) -> dict[str, str]:
    """Build explicit requests-compatible proxy mapping from settings."""
    upstox_proxy = (getattr(settings, "UPSTOX_PROXY_URL", "") or "").strip()
    http_proxy = (getattr(settings, "REQUESTS_HTTP_PROXY", "") or "").strip() or upstox_proxy
    https_proxy = (getattr(settings, "REQUESTS_HTTPS_PROXY", "") or "").strip() or upstox_proxy

    proxies: dict[str, str] = {}
    if http_proxy:
        proxies["http"] = http_proxy
    if https_proxy:
        proxies["https"] = https_proxy
    return proxies


def patch_upstox_sdk_socks_support() -> None:
    """
    Patch Upstox SDK REST client to support SOCKS proxy URLs in config.proxy.

    Upstox SDK currently uses urllib3.ProxyManager for all proxy URLs.
    SOCKS URLs require urllib3.contrib.socks.SOCKSProxyManager.
    """
    global _SOCKS_PATCHED
    if _SOCKS_PATCHED:
        return

    import upstox_client.rest as upstox_rest

    original_init = upstox_rest.RESTClientObject.__init__

    def patched_init(self, configuration, pools_size=4, maxsize=None):
        proxy = str(getattr(configuration, "proxy", "") or "").strip()
        proxy_lc = proxy.lower()
        is_socks = proxy_lc.startswith(("socks5://", "socks5h://", "socks4://", "socks4a://"))

        if is_socks:
            try:
                from urllib3.contrib.socks import SOCKSProxyManager
            except Exception:
                logger.warning(
                    "SOCKS proxy URL detected but SOCKS support is unavailable in this Python environment. "
                    "Install PySocks and restart, or switch UPSTOX_PROXY_URL to an HTTP proxy endpoint."
                )
                return original_init(self, configuration, pools_size, maxsize)

            cert_reqs = ssl.CERT_REQUIRED if configuration.verify_ssl else ssl.CERT_NONE
            ca_certs = configuration.ssl_ca_cert or certifi.where()

            additional_pool_args = {}
            if configuration.assert_hostname is not None:
                additional_pool_args["assert_hostname"] = configuration.assert_hostname

            if maxsize is None:
                if configuration.connection_pool_maxsize is not None:
                    maxsize = configuration.connection_pool_maxsize
                else:
                    maxsize = 4

            self.pool_manager = SOCKSProxyManager(
                proxy_url=proxy,
                num_pools=pools_size,
                maxsize=maxsize,
                cert_reqs=cert_reqs,
                ca_certs=ca_certs,
                cert_file=configuration.cert_file,
                key_file=configuration.key_file,
                **additional_pool_args,
            )
            return

        return original_init(self, configuration, pools_size, maxsize)

    upstox_rest.RESTClientObject.__init__ = patched_init
    _SOCKS_PATCHED = True


def configure_network_proxies(settings) -> None:
    """Configure process and SDK to route outbound traffic through configured proxies."""
    if bool(getattr(settings, "APPLY_PROCESS_PROXY_ENV", False)):
        apply_process_proxy_env(settings)
    else:
        _clear_proxy_env()
        logger.info("Process-level proxy env injection is disabled (APPLY_PROCESS_PROXY_ENV=False).")
    patch_upstox_sdk_socks_support()

    configured = (
        (settings.UPSTOX_PROXY_URL or "").strip()
        or (settings.REQUESTS_HTTPS_PROXY or "").strip()
        or (settings.REQUESTS_HTTP_PROXY or "").strip()
    )
    if configured:
        logger.info("Proxy routing is configured for process and Upstox SDK.")
    else:
        logger.warning("No proxy configured; outbound calls will use direct network path.")
