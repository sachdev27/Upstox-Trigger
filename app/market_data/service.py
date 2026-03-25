"""
Market Data Service — historical candle fetch, instrument lookup, portfolio data.

Refactored from legacy/Historical/intraday_data.py, legacy/live_data/instrument.py,
and legacy/connections.py (market quote functions).
"""

import logging
import csv
import gzip
import shutil
from pathlib import Path
from datetime import datetime, timedelta

import requests
import upstox_client

from app.config import get_settings, BASE_DIR

logger = logging.getLogger(__name__)


class MarketDataService:
    """Provides market data: candles, quotes, instruments, portfolio."""

    def __init__(self, configuration: upstox_client.Configuration):
        self.config = configuration
        self.settings = get_settings()
        self.api_version = self.settings.API_VERSION

    # ── Historical Candles ──────────────────────────────────────

    def get_historical_candles(
        self,
        instrument_key: str,
        interval: str = "1minute",
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> list[dict]:
        """
        Fetch historical OHLCV candle data and merge with today's intraday data.
        Automatically resamples 1minute data if a custom timeframe (e.g. 15minute, 5minute) forms.
        """
        api = upstox_client.HistoryApi(upstox_client.ApiClient(self.config))
        
        # Map UI timeframes (1m, 5m, 15m, 1H, 1D) to Upstox APIs strict interval modes
        tf_map = {
            "1m": ("1minute", False, None),
            "1minute": ("1minute", False, None),
            "5m": ("1minute", True, "5Min"),
            "15m": ("1minute", True, "15Min"),
            "15minute": ("1minute", True, "15Min"),
            "30m": ("1minute", True, "30Min"), # Resampling from 1m to ensure accurate boundaries
            "30minute": ("30minute", False, None),
            "1H": ("1minute", True, "60Min"),
            "4H": ("1minute", True, "240Min"),
            "1D": ("day", False, None),
            "day": ("day", False, None),
            "1W": ("week", False, None),
        }
        
        fetch_interval, needs_resample, resample_rule = tf_map.get(interval, ("1minute", False, None))

        try:
            candles_dict = {}
            
            # 1. Fetch Historical Data (from_date -> to_date)
            if from_date and to_date:
                try:
                    res1 = api.get_historical_candle_data1(
                        instrument_key, fetch_interval, to_date, from_date, self.api_version
                    )
                    data1 = res1.to_dict()
                    for c in data1.get("data", {}).get("candles", []):
                        candles_dict[c[0]] = c
                except Exception as e:
                    logger.warning(f"Historical fetch failed, proceeding to intraday: {e}")

            # 2. Fetch Intraday Data (Today) 
            # (Historical API often cuts off at yesterday close)
            try:
                res2 = api.get_intra_day_candle_data(
                    instrument_key, fetch_interval, self.api_version
                )
                data2 = res2.to_dict()
                for c in data2.get("data", {}).get("candles", []):
                    candles_dict[c[0]] = c
            except Exception as e:
                logger.warning(f"Intraday fetch failed: {e}")

            if not candles_dict:
                return []

            # Sort chronologically
            sorted_raw = sorted(candles_dict.values(), key=lambda x: x[0])
            
            transformed = []
            for c in sorted_raw:
                transformed.append({
                    "datetime": c[0],
                    "open": c[1],
                    "high": c[2],
                    "low": c[3],
                    "close": c[4],
                    "volume": c[5] if len(c) > 5 else 0,
                    "oi": c[6] if len(c) > 6 else 0,
                })
                
            if not needs_resample:
                return transformed
                
            # --- Dynamic Pandas Resampling ---
            import pandas as pd
            df = pd.DataFrame(transformed)
            df['datetime_dt'] = pd.to_datetime(df['datetime'])
            df.set_index('datetime_dt', inplace=True)
            
            # Resample logic
            resampled = df.resample(resample_rule).agg({
                'open': 'first',
                'high': 'max',
                'low': 'min',
                'close': 'last',
                'volume': 'sum',
                'oi': 'last',
                'datetime': 'first' # retain original timezone localized string
            })
            resampled = resampled.dropna(subset=['open'])
            
            final_candles = []
            for idx, row in resampled.iterrows():
                # Reformat datetime back to Upstox ISO format string or just use Pandas ISO format limit
                final_candles.append({
                    "datetime": idx.strftime('%Y-%m-%dT%H:%M:%S+05:30'),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": int(row["volume"]),
                    "oi": int(row["oi"]),
                })
                
            return final_candles

        except Exception as e:
            logger.error(f"Failed to fetch/merge candles for {instrument_key}: {e}")
            return []

    def get_intraday_candles(
        self, instrument_key: str, interval: str = "1minute"
    ) -> list[dict]:
        """Fetch today's intraday candle data."""
        return self.get_historical_candles(instrument_key, interval)

    # ── Market Quotes ───────────────────────────────────────────

    def get_ltp(self, instrument_key: str) -> float | None:
        """Get last traded price for an instrument."""
        api = upstox_client.MarketQuoteApi(
            upstox_client.ApiClient(self.config)
        )
        try:
            response = api.ltp(instrument_key, self.api_version)
            data = response.to_dict().get("data", {})
            # response keyed by instrument_key
            for key, val in data.items():
                return val.get("last_price")
        except Exception as e:
            logger.error(f"LTP fetch failed for {instrument_key}: {e}")
            return None

    def get_full_quote(self, instrument_key: str) -> dict | None:
        """Get full market quote (OHLC, volume, depth, etc.)."""
        api = upstox_client.MarketQuoteApi(
            upstox_client.ApiClient(self.config)
        )
        try:
            response = api.get_full_market_quote(
                instrument_key, self.api_version
            )
            return response.to_dict().get("data", {})
        except Exception as e:
            logger.error(f"Full quote failed for {instrument_key}: {e}")
            return None

    def get_market_ohlc(
        self, instrument_key: str, interval: str = "1d"
    ) -> dict | None:
        """Get OHLC market quote for a given interval."""
        api = upstox_client.MarketQuoteApi(
            upstox_client.ApiClient(self.config)
        )
        try:
            response = api.get_market_quote_ohlc(
                instrument_key, interval, self.api_version
            )
            return response.to_dict().get("data", {})
        except Exception as e:
            logger.error(f"OHLC quote failed for {instrument_key}: {e}")
            return None

    # ── Portfolio ────────────────────────────────────────────────

    def get_positions(self) -> list:
        """Get current positions."""
        api = upstox_client.PortfolioApi(
            upstox_client.ApiClient(self.config)
        )
        try:
            response = api.get_positions(self.api_version)
            return response.to_dict().get("data", [])
        except Exception as e:
            logger.error(f"Failed to fetch positions: {e}")
            return []

    def get_holdings(self) -> list:
        """Get current holdings."""
        api = upstox_client.PortfolioApi(
            upstox_client.ApiClient(self.config)
        )
        try:
            response = api.get_holdings(self.api_version)
            return response.to_dict().get("data", [])
        except Exception as e:
            logger.error(f"Failed to fetch holdings: {e}")
            return []

    def get_funds_and_margin(self) -> dict | None:
        """Get available funds and margin."""
        api = upstox_client.UserApi(
            upstox_client.ApiClient(self.config)
        )
        try:
            response = api.get_user_fund_margin(self.api_version)
            return response.to_dict().get("data", {})
        except Exception as e:
            logger.error(f"Failed to fetch funds: {e}")
            return None

    def get_profile(self) -> dict | None:
        """Get user profile."""
        api = upstox_client.UserApi(
            upstox_client.ApiClient(self.config)
        )
        try:
            response = api.get_profile(self.api_version)
            return response.to_dict().get("data", {})
        except Exception as e:
            logger.error(f"Failed to fetch profile: {e}")
            return None

    # ── SDK-Native Instrument Search ─────────────────────────────

    def search_instrument_sdk(
        self, query: str, page_size: int = 20
    ) -> list[dict]:
        """
        Search instruments using the SDK's InstrumentsApi.
        No need to download the full exchange CSV.

        Args:
            query: Search term, e.g. "Reliance" or "NIFTY"
            page_size: Number of results to return
        """
        api = upstox_client.InstrumentsApi(
            upstox_client.ApiClient(self.config)
        )
        try:
            response = api.search_instrument(query)
            data = response.to_dict()
            
            if isinstance(data, list):
                instruments = data
            else:
                # Upstox returns {"status": "success", "data": [ {...}, {...} ]}
                instruments = data.get("data", [])
                
                # Failsafe if it actually returned {"data": {"instruments": [...]}}
                if isinstance(instruments, dict):
                    instruments = instruments.get("instruments", [])
                    
            return instruments[:page_size]
        except Exception as e:
            logger.error(f"Instrument search failed for '{query}': {e}")
            return []

    # ── Market Status & Holidays ─────────────────────────────────

    def get_market_status(self, exchange: str = "NSE") -> dict | None:
        """
        Get real-time market status (open/closed) using the SDK.
        Replaces manual market hours checking.
        """
        api = upstox_client.MarketHolidaysAndTimingsApi(
            upstox_client.ApiClient(self.config)
        )
        try:
            response = api.get_market_status(exchange)
            return response.to_dict().get("data", {})
        except Exception as e:
            logger.error(f"Market status check failed: {e}")
            return None

    def get_holidays(self) -> list:
        """Get list of market holidays."""
        api = upstox_client.MarketHolidaysAndTimingsApi(
            upstox_client.ApiClient(self.config)
        )
        try:
            response = api.get_holidays()
            return response.to_dict().get("data", [])
        except Exception as e:
            logger.error(f"Holidays fetch failed: {e}")
            return []

    def get_exchange_timings(self, date: str) -> list:
        """Get exchange timings for a specific date (YYYY-MM-DD)."""
        api = upstox_client.MarketHolidaysAndTimingsApi(
            upstox_client.ApiClient(self.config)
        )
        try:
            response = api.get_exchange_timings(date)
            return response.to_dict().get("data", [])
        except Exception as e:
            logger.error(f"Exchange timings fetch failed: {e}")
            return []

    # ── Options ──────────────────────────────────────────────────

    def get_option_chain(
        self, instrument_key: str, expiry_date: str
    ) -> dict | None:
        """Get put/call option chain for an instrument and expiry."""
        api = upstox_client.OptionsApi(
            upstox_client.ApiClient(self.config)
        )
        try:
            response = api.get_put_call_option_chain(
                instrument_key, expiry_date
            )
            return response.to_dict().get("data", {})
        except Exception as e:
            logger.error(f"Option chain fetch failed: {e}")
            return None

    def get_option_contracts(
        self, instrument_key: str, expiry_date: str | None = None
    ) -> list:
        """Get available option contracts."""
        api = upstox_client.OptionsApi(
            upstox_client.ApiClient(self.config)
        )
        try:
            if expiry_date:
                response = api.get_option_contracts(
                    instrument_key, expiry_date
                )
            else:
                response = api.get_option_contracts(instrument_key)
            return response.to_dict().get("data", [])
        except Exception as e:
            logger.error(f"Option contracts fetch failed: {e}")
            return []

    # ── Charges & Brokerage ──────────────────────────────────────

    def get_brokerage(
        self,
        instrument_token: str,
        quantity: int,
        product: str,
        transaction_type: str,
        price: float,
    ) -> dict | None:
        """Calculate brokerage charges for a trade before execution."""
        api = upstox_client.ChargeApi(
            upstox_client.ApiClient(self.config)
        )
        try:
            response = api.get_brokerage(
                instrument_token, quantity, product,
                transaction_type, price
            )
            return response.to_dict().get("data", {})
        except Exception as e:
            logger.error(f"Brokerage calc failed: {e}")
            return None
