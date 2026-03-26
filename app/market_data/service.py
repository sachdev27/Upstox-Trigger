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
import pandas as pd

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
        
        # Map UI timeframes to Upstox API interval modes
        # Upstox supports: 1minute, 30minute, day, week, month
        tf_map = {
            "1m": ("1minute", False, None),
            "1minute": ("1minute", False, None),
            "5m": ("1minute", True, "5Min"),
            "5minute": ("1minute", True, "5Min"),
            "15m": ("1minute", True, "15Min"),
            "15minute": ("1minute", True, "15Min"),
            "30m": ("30minute", False, None),
            "30minute": ("30minute", False, None),
            "1H": ("30minute", True, "60Min"),
            "1hour": ("30minute", True, "60Min"),
            "4H": ("30minute", True, "240Min"),
            "1D": ("day", False, None),
            "day": ("day", False, None),
            "1W": ("week", False, None),
            "week": ("week", False, None),
            "1M": ("month", False, None),
            "month": ("month", False, None),
        }
        
        fetch_interval, needs_resample, resample_rule = tf_map.get(interval, ("1minute", False, None))

        try:
            candles_dict = {}
            
            # 1. Fetch Historical Data (from_date -> to_date)
            # Upstox API limits (approx):
            # - 1minute: 1 month (safe: 25 days)
            # - 30minute: 1 year (safe: 90-180 days often)
            # - day: 1 year (safe: 365 days)
            # - week/month: 10 years
            
            if not from_date:
                if interval in ["day", "1D"]:
                    days = 365
                elif interval in ["week", "1W", "month"]:
                    days = 365 * 10
                elif interval in ["30m", "30minute", "1H", "1hour", "4H"]:
                    days = 180 # Upstox 30m can be picky about full 365 days
                else:
                    days = 25 # Safe for 1m
                from_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
            
            if not to_date:
                to_date = datetime.now().strftime('%Y-%m-%d')

            # --- Resilient Fetch with Fallback for Date Range ---
            max_retries = 3
            current_days = days if 'days' in locals() else 30
            
            for attempt in range(max_retries):
                try:
                    res1 = api.get_historical_candle_data1(
                        instrument_key, fetch_interval, to_date, from_date, self.api_version
                    )
                    
                    if res1 and hasattr(res1, 'data') and res1.data.candles:
                        # Success!
                        for candle in res1.data.candles:
                            # candle[0] can be datetime, str (ISO), or int
                            raw_t = candle[0]
                            try:
                                if isinstance(raw_t, datetime):
                                    ts = int(raw_t.timestamp())
                                elif isinstance(raw_t, str):
                                    # Handle "2026-03-25T15:29:00+05:30"
                                    ts = int(pd.to_datetime(raw_t).timestamp())
                                else:
                                    ts = int(raw_t)
                                    if ts > 10**11: ts //= 1000 # Convert ms to sec
                            except:
                                continue
                            
                            if not ts: continue
                            
                            candles_dict[ts] = {
                                "time": ts,
                                "open": float(candle[1]),
                                "high": float(candle[2]),
                                "low": float(candle[3]),
                                "close": float(candle[4]),
                                "volume": int(candle[5]) if len(candle) > 5 else 0
                            }
                        break
                    else:
                        break # Empty result, no point retrying unless it was an error
                        
                except Exception as e:
                    # Check for "Invalid date range" (UDAPI1148)
                    error_str = str(e)
                    if "UDAPI1148" in error_str and attempt < max_retries - 1:
                        # Reduce days and try again
                        current_days = int(current_days * 0.7)
                        from_date = (datetime.now() - timedelta(days=current_days)).strftime('%Y-%m-%d')
                        logger.info(f"UDAPI1148 for {interval}, retrying with {current_days} days...")
                        continue
                    else:
                        logger.error(f"Historical fetch failed for {instrument_key}: {e}")
                        break
            # The original line `logger.warning(f"Historical fetch failed for {instrument_key}: {e}")`
            # was part of the old try-except block. The new resilient fetch handles logging internally.

            # 2. Fetch Intraday Data (Today) 
            # Only for intraday intervals (day/week/month don't support get_intra_day_candle_data)
            if fetch_interval not in ["day", "week", "month"]:
                try:
                    res2 = api.get_intra_day_candle_data(
                        instrument_key, fetch_interval, self.api_version
                    )
                    if res2 and hasattr(res2, 'data') and res2.data.candles:
                        for candle in res2.data.candles:
                            if isinstance(candle[0], datetime):
                                ts = int(candle[0].timestamp())
                            else:
                                try:
                                    ts = int(candle[0])
                                    if ts > 10**11: ts //= 1000
                                except: continue
                                
                            candles_dict[ts] = {
                                "time": ts,
                                "open": float(candle[1]),
                                "high": float(candle[2]),
                                "low": float(candle[3]),
                                "close": float(candle[4]),
                                "volume": int(candle[5]) if len(candle) > 5 else 0
                            }
                except Exception as e:
                    logger.warning(f"Intraday fetch failed for {instrument_key}: {e}")

            if not candles_dict:
                return []

            # Sort chronologically by timestamp
            sorted_times = sorted(candles_dict.keys())
            transformed = [candles_dict[t] for t in sorted_times]
            
            if not needs_resample:
                return transformed
                
            # --- Dynamic Pandas Resampling ---
            df = pd.DataFrame(transformed)
            # Upstox timestamps are in seconds; convert to datetime for resampling
            df['datetime_dt'] = pd.to_datetime(df['time'], unit='s')
            df.set_index('datetime_dt', inplace=True)
            
            # Resample logic
            resampled = df.resample(resample_rule).agg({
                'open': 'first',
                'high': 'max',
                'low': 'min',
                'close': 'last',
                'volume': 'sum'
            })
            # Remove any bins with no data
            resampled = resampled.dropna(subset=['open', 'high', 'low', 'close'])
            
            final_candles = []
            for idx, row in resampled.iterrows():
                final_candles.append({
                    "time": int(idx.timestamp()),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": int(row["volume"]),
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
    async def get_detailed_option_chain(
        self, instrument_key: str, expiry_date: str | None = None
    ) -> dict:
        """
        High-level helper to fetch a full option chain with LTP and Greeks.
        Can be used by both the API routes and the Automation Engine.
        """
        try:
            # 1. Get available contracts
            contracts = self.get_option_contracts(instrument_key)
            if not contracts:
                return {"status": "error", "message": "No contracts found", "chain": []}
            
            # Get unique expiries (sorted strings)
            def _fmt_expiry(e):
                if hasattr(e, "strftime"): return e.strftime("%Y-%m-%d")
                return str(e)
                
            all_expiries = sorted(list(set(_fmt_expiry(c.get("expiry")) for c in contracts if c.get("expiry"))))
            if not all_expiries:
                return {"status": "error", "message": "No expiries found", "chain": []}
            
            # Select expiry
            target_expiry = expiry_date or all_expiries[0]
            
            # 2. Get native option chain for the target expiry
            # This contains Greeks and Market Data (LTP, Volume, OI) correctly mapped by Upstox
            chain_data = self.get_option_chain(instrument_key, target_expiry) or []
            
            # 3. Format into strikes
            final_chain = []
            spot_price = 0.0
            
            # Handle both list or dict responses based on SDK behavior
            strikes_list = chain_data if isinstance(chain_data, list) else chain_data.get('chain', [])
            
            for item in strikes_list:
                strike_price = float(item.get("strike_price") or 0.0)
                if spot_price == 0.0:
                    spot_price = float(item.get("underlying_spot_price") or 0.0)
                
                # Format CE/PE
                def _fmt_option(opt):
                    if not opt: return None
                    m = opt.get("market_data", {})
                    # Upstox uses 'option_greeks' for the Greeks sub-object in some SDK versions
                    g = opt.get("option_greeks") or opt.get("greeks") or {}
                    
                    return {
                        "instrument_key": opt.get("instrument_key"),
                        "ltp": float(m.get("ltp") or 0.0),
                        "volume": int(m.get("volume") or 0),
                        "oi": float(m.get("oi") or 0.0),
                        "iv": float(g.get("iv") or 0.0),
                        "delta": float(g.get("delta") or 0.0),
                        "theta": float(g.get("theta") or 0.0),
                        "gamma": float(g.get("gamma") or 0.0),
                        "vega": float(g.get("vega") or 0.0),
                    }

                final_chain.append({
                    "strike_price": strike_price,
                    "ce": _fmt_option(item.get("call_options")),
                    "pe": _fmt_option(item.get("put_options")),
                })

            # Sort by strike price
            final_chain.sort(key=lambda x: x["strike_price"])

            return {
                "status": "success",
                "instrument_key": instrument_key,
                "spot_price": spot_price,
                "expiry_date": target_expiry,
                "available_expiries": all_expiries,
                "chain": final_chain
            }
            
        except Exception as e:
            logger.error(f"Detailed option chain failed: {e}")
            return {"status": "error", "message": str(e), "chain": []}
