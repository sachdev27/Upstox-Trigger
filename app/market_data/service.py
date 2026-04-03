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

    # Class-level cache for lot sizes: instrument_key -> lot_size
    _lot_size_cache: dict[str, int] = {}

    def __init__(self, configuration: upstox_client.Configuration):
        self.config = configuration
        self.settings = get_settings()
        self.api_version = self.settings.API_VERSION

        # Reuse API Clients
        self.api_client = upstox_client.ApiClient(self.config)
        self.history_api = upstox_client.HistoryApi(self.api_client)
        self.quote_api = upstox_client.MarketQuoteApi(self.api_client)
        self.portfolio_api = upstox_client.PortfolioApi(self.api_client)
        self.user_api = upstox_client.UserApi(self.api_client)
        self.instruments_api = upstox_client.InstrumentsApi(self.api_client)
        self.timing_api = upstox_client.MarketHolidaysAndTimingsApi(self.api_client)
        self.options_api = upstox_client.OptionsApi(self.api_client)
        self.charge_api = upstox_client.ChargeApi(self.api_client)

        # Candle Cache: (instrument_key, interval) -> {"expiry": datetime, "candles": list[dict]}
        self._candle_cache = {}
        self._cache_ttl = 55 # seconds (just under 1 min)

    def _next_cache_expiry(self, interval: str) -> datetime:
        """
        Compute cache expiry aligned to the next candle boundary for the interval.

        This keeps fast timeframes fresh right after candle close while preventing
        unnecessary refetches between boundaries for larger timeframes.
        """
        now = datetime.now()
        tf_sec = {
            "1m": 60,
            "1minute": 60,
            "5m": 300,
            "5minute": 300,
            "15m": 900,
            "15minute": 900,
            "30m": 1800,
            "30minute": 1800,
            "1H": 3600,
            "1hour": 3600,
            "60minute": 3600,
            "4H": 14400,
            "day": 86400,
            "1D": 86400,
            "week": 604800,
            "1W": 604800,
        }

        period = tf_sec.get(interval)
        if not period:
            return now + timedelta(seconds=self._cache_ttl)

        now_epoch = int(now.timestamp())
        next_boundary = ((now_epoch // period) + 1) * period

        # Small guard to let upstream APIs publish the new bar.
        return datetime.fromtimestamp(next_boundary) + timedelta(seconds=2)

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

        # Map UI timeframes (1m, 5m, 15m, 1H, 1D) to Upstox APIs strict interval modes
        tf_map = {
            "1m": ("1minute", False, None),
            "1minute": ("1minute", False, None),
            "5m": ("1minute", True, "5Min"),
            "5minute": ("1minute", True, "5Min"),
            "15m": ("1minute", True, "15Min"),
            "15minute": ("1minute", True, "15Min"),
            "30m": ("1minute", True, "30Min"),
            "30minute": ("1minute", True, "30Min"),
            "1H": ("1minute", True, "60Min"),
            "1hour": ("1minute", True, "60Min"),
            "4H": ("1minute", True, "240Min"),
            "1D": ("day", False, None),
            "day": ("day", False, None),
            "1W": ("week", False, None),
        }

        fetch_interval, needs_resample, resample_rule = tf_map.get(interval, ("1minute", False, None))

        # Check Cache
        cache_key = (instrument_key, interval)
        now = datetime.now()
        if cache_key in self._candle_cache:
            entry = self._candle_cache[cache_key]
            if now < entry["expiry"]:
                logger.debug(f"💎 Cache Hit: {instrument_key} ({interval})")
                return entry["candles"]

        try:
            candles_dict = {}

            # 1. Fetch Historical Data (from_date -> to_date)
            # Upstox API limit: ~30 days for intraday, but often 20-25 days is safer to avoid UDAPI1148
            if not from_date:
                days = 30 if interval == "day" or interval == "1D" else 20
                from_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
            if not to_date:
                to_date = datetime.now().strftime('%Y-%m-%d')

            try:
                res1 = self.history_api.get_historical_candle_data1(
                    instrument_key, fetch_interval, to_date, from_date, self.api_version
                )
                data1 = res1.to_dict()
                for c in data1.get("data", {}).get("candles", []):
                    candles_dict[c[0]] = c
            except Exception as e:
                logger.warning(f"Historical fetch failed for {instrument_key}: {e}")

            # 2. Fetch Intraday Data (Today)
            # Only for intraday intervals (day/week don't support get_intra_day_candle_data)
            if fetch_interval not in ["day", "week"]:
                try:
                    res2 = self.history_api.get_intra_day_candle_data(
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
                try:
                    # Ensure all OHLC values are valid floats and not None
                    if any(v is None for v in c[1:5]):
                        continue
                    transformed.append({
                        "time": c[0],
                        "open": float(c[1]),
                        "high": float(c[2]),
                        "low": float(c[3]),
                        "close": float(c[4]),
                        "volume": int(c[5]) if len(c) > 5 else 0,
                    })
                except (ValueError, TypeError, IndexError):
                    continue

            if not needs_resample:
                for c in transformed:
                    try:
                        c["time"] = int(pd.to_datetime(c["time"]).timestamp())
                    except: pass

                # Update Cache for non-resampled
                self._candle_cache[cache_key] = {
                    "expiry": self._next_cache_expiry(interval),
                    "candles": transformed
                }
                return transformed

            # --- Dynamic Pandas Resampling ---
            df = pd.DataFrame(transformed)
            df['datetime_dt'] = pd.to_datetime(df['time'])
            df.set_index('datetime_dt', inplace=True)

            # Resample logic
            resampled = df.resample(resample_rule).agg({
                'open': 'first',
                'high': 'max',
                'low': 'min',
                'close': 'last',
                'volume': 'sum'
            })
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

            # Update Cache
            self._candle_cache[cache_key] = {
                "expiry": self._next_cache_expiry(interval),
                "candles": final_candles
            }
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
        """Get last traded price."""
        try:
            response = self.quote_api.ltp(instrument_key, self.api_version)
            data = response.to_dict().get("data", {})
            # response keyed by instrument_key
            for key, val in data.items():
                return val.get("last_price")
        except Exception as e:
            logger.error(f"LTP fetch failed for {instrument_key}: {e}")
            return None

    def get_full_quote(self, instrument_key: str) -> dict | None:
        """Get full market quote (OHLC, volume, depth, etc.)."""
        try:
            response = self.quote_api.get_full_market_quote(
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
        try:
            response = self.quote_api.get_market_quote_ohlc(
                instrument_key, interval, self.api_version
            )
            return response.to_dict().get("data", {})
        except Exception as e:
            logger.error(f"OHLC quote failed for {instrument_key}: {e}")
            return None

    # ── Portfolio ────────────────────────────────────────────────

    def get_positions(self) -> list:
        """Get current positions."""
        try:
            response = self.portfolio_api.get_positions(self.api_version)
            return response.to_dict().get("data", [])
        except Exception as e:
            logger.error(f"Failed to fetch positions: {e}")
            return []

    def get_holdings(self) -> list:
        """Get current holdings."""
        try:
            response = self.portfolio_api.get_holdings(self.api_version)
            return response.to_dict().get("data", [])
        except Exception as e:
            logger.error(f"Failed to fetch holdings: {e}")
            return []

    def get_funds_and_margin(self) -> dict | None:
        """Get available funds and margin."""
        try:
            response = self.user_api.get_user_fund_margin(self.api_version)
            return response.to_dict().get("data", {})
        except Exception as e:
            logger.error(f"Failed to fetch funds: {e}")
            return None

    def get_profile(self) -> dict | None:
        """Get user profile."""
        try:
            response = self.user_api.get_profile(self.api_version)
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
        try:
            response = self.instruments_api.search_instrument(query)
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

    # Common underlying display names → Upstox search symbol
    _UNDERLYING_SYMBOL_MAP = {
        "Nifty 50": "NIFTY",
        "Nifty Bank": "BANKNIFTY",
        "Nifty Fin Service": "FINNIFTY",
        "NIFTY MID SELECT": "MIDCPNIFTY",
        "Nifty Next 50": "NIFTYNXT50",
    }

    def get_lot_size(self, instrument_key: str, underlying_key: str | None = None) -> int:
        """
        Look up the lot size for an F&O instrument.

        Args:
            instrument_key: The option/future instrument key (e.g. 'NSE_FO|40772')
            underlying_key: Optional underlying key (e.g. 'NSE_INDEX|Nifty 50')
                            Used to derive the search name for the API.

        Returns lot size (e.g. 65 for NIFTY options), or 1 if lookup fails.
        """
        # Check class-level cache
        if instrument_key in self._lot_size_cache:
            return self._lot_size_cache[instrument_key]

        # Try local DB first
        try:
            from app.database.connection import get_session, Instrument
            session = get_session()
            try:
                inst = session.query(Instrument).filter_by(instrument_key=instrument_key).first()
                if inst and int(inst.lot_size or 1) > 1:
                    lot = int(inst.lot_size)
                    self._lot_size_cache[instrument_key] = lot
                    return lot
            finally:
                session.close()
        except Exception:
            pass

        # Fallback: Upstox REST Instrument Search API
        try:
            import requests as _requests
            token = self.config.access_token
            if not token:
                logger.warning("No access token for instrument search API")
                return 1

            # Derive the search query from the underlying key
            search_name = None
            if underlying_key:
                # "NSE_INDEX|Nifty 50" → "Nifty 50" → look up in map → "NIFTY"
                uname = underlying_key.split("|")[1] if "|" in underlying_key else underlying_key
                search_name = self._UNDERLYING_SYMBOL_MAP.get(uname, uname)

            if not search_name:
                # No underlying provided — can't derive a search name from numeric exchange_token
                logger.debug(f"No underlying_key for lot size lookup of {instrument_key}")
                return 1

            parts = instrument_key.split("|")
            exchange = parts[0].split("_")[0] if "_" in parts[0] else "NSE"

            resp = _requests.get(
                "https://api.upstox.com/v2/instruments/search",
                params={
                    "query": search_name,
                    "exchanges": exchange,
                    "segments": "FO",
                    "records": 5,
                },
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {token}",
                },
                timeout=5,
            )
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("data", [])

                # Try exact match first
                for item in items:
                    if item.get("instrument_key") == instrument_key:
                        lot = int(item.get("lot_size", 1))
                        if lot > 0:
                            self._lot_size_cache[instrument_key] = lot
                            logger.info(f"Lot size for {instrument_key}: {lot} (exact match)")
                            return lot

                # All derivatives of the same underlying share the same lot size,
                # so take the lot_size from any result matching our search name.
                for item in items:
                    if item.get("name", "").upper() == search_name.upper():
                        lot = int(item.get("lot_size", 1))
                        if lot > 0:
                            self._lot_size_cache[instrument_key] = lot
                            logger.info(f"Lot size for {instrument_key}: {lot} (from {item.get('trading_symbol')})")
                            return lot
            else:
                logger.warning(f"Instrument search API returned {resp.status_code}")
        except Exception as e:
            logger.debug(f"Instrument search API fallback failed for {instrument_key}: {e}")

        return 1

    # ── Market Status & Holidays ─────────────────────────────────

    def get_market_status(self, exchange: str = "NSE") -> dict | None:
        """
        Get real-time market status (open/closed) using the SDK.
        Replaces manual market hours checking.
        """
        try:
            response = self.timing_api.get_market_status(exchange)
            return response.to_dict().get("data", {})
        except Exception as e:
            logger.error(f"Market status check failed: {e}")
            return None

    def get_holidays(self) -> list:
        """Get list of market holidays."""
        try:
            response = self.timing_api.get_holidays()
            return response.to_dict().get("data", [])
        except Exception as e:
            logger.error(f"Holidays fetch failed: {e}")
            return []

    def get_exchange_timings(self, date: str) -> list:
        """Get exchange timings for a specific date (YYYY-MM-DD)."""
        try:
            response = self.timing_api.get_exchange_timings(date)
            return response.to_dict().get("data", [])
        except Exception as e:
            logger.error(f"Exchange timings fetch failed: {e}")
            return []

    # ── Options ──────────────────────────────────────────────────

    def get_option_chain(
        self, instrument_key: str, expiry_date: str
    ) -> dict | None:
        """Get put/call option chain for an instrument and expiry."""
        try:
            response = self.options_api.get_put_call_option_chain(
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
        try:
            if expiry_date:
                response = self.options_api.get_option_contracts(
                    instrument_key, expiry_date
                )
            else:
                response = self.options_api.get_option_contracts(instrument_key)
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
        try:
            response = self.charge_api.get_brokerage(
                instrument_token, quantity, product,
                transaction_type, price
            )
            return response.to_dict().get("data", {})
        except Exception as e:
            logger.error(f"Brokerage calc failed: {e}")
            return None
    async def get_detailed_option_chain(
        self, instrument_key: str, expiry_date: str | None = None, greeks_cache: dict | None = None
    ) -> dict:
        """
        High-level helper to fetch a full option chain with LTP and Greeks.
        Can be used by both the API routes and the Automation Engine.

        Args:
            instrument_key: The index/stock key (e.g., "NSE_INDEX|Nifty 50")
            expiry_date: Target expiry (format: "YYYY-MM-DD"). If None, uses first expiry.
            greeks_cache: Dict of {instrument_key: {delta, theta, iv, ...}} from streamer for live Greeks.
        """
        greeks_cache = greeks_cache or {}

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

            # Select expiry; if requested expiry is unavailable, gracefully fall back.
            target_expiry = expiry_date or all_expiries[0]
            requested_expiry = expiry_date
            expiry_fallback_used = False

            if requested_expiry and requested_expiry not in all_expiries:
                # Prefer the nearest future expiry; otherwise use the first available.
                future = [e for e in all_expiries if e >= requested_expiry]
                target_expiry = future[0] if future else all_expiries[0]
                expiry_fallback_used = True

            # Filter contracts for this expiry (compare as strings)
            expiry_contracts = [c for c in contracts if _fmt_expiry(c.get("expiry")) == target_expiry]

            if not expiry_contracts:
                return {
                    "status": "success",
                    "instrument_key": instrument_key,
                    "spot_price": 0.0,
                    "expiry_date": target_expiry,
                    "requested_expiry": requested_expiry,
                    "expiry_fallback_used": expiry_fallback_used,
                    "available_expiries": all_expiries,
                    "chain": [],
                    "message": "No option contracts found for selected expiry.",
                }

            # 2. Extract spot price from one contract (or fetch separately if needed)
            # Upstox usually includes underlying_key in the contract
            spot_price = 0.0
            if expiry_contracts:
                spot_price = self.get_ltp(instrument_key) or 0.0

            # 3. Fetch Full Market Quote for all contracts to get LTP and Greeks
            # We batch the keys for efficiency
            instr_keys = [c["instrument_key"] for c in expiry_contracts]

            # Upstox LTP API supports up to 500 instruments in one call
            # For full quote (Greeks), we might need to batch more carefully
            quote_data = {}
            for i in range(0, len(instr_keys), 50):
                batch = ",".join(instr_keys[i:i+50])
                res = self.get_full_quote(batch)
                if res:
                    # RE-MAP: Upstox quote data keys can be the symbol name or instrument_token
                    # We need to map them back to the original instrument_token/key
                    for k, val in res.items():
                        # Option 1: Try k directly
                        if k in instr_keys:
                            quote_data[k] = val

                        # Option 2: Try instrument_token inside val
                        token = val.get("instrument_token")
                        if token and token in instr_keys:
                            quote_data[token] = val

                        # Option 3: Try to find which instrument_key matches this token
                        # Some keys are NSE_FO|54479 but token is 54479
                        if token:
                            for ik in instr_keys:
                                if "|" in ik and ik.split("|")[-1] == str(token):
                                    quote_data[ik] = val
                                    break

                        # Option 4: Fallback for symbol matching
                        if ":" in k:
                            sym = k.split(":")[-1]
                            for ik in instr_keys:
                                if ik.endswith(sym):
                                    quote_data[ik] = val
                                    break

            # 4. Group by strike
            strikes = {}
            for c in expiry_contracts:
                sp = float(str(c["strike_price"]))
                # Normalize strike (paise check)
                if sp > 1000000: sp /= 100.0

                if sp not in strikes:
                    strikes[sp] = {"strike_price": sp, "ce": None, "pe": None}

                q = quote_data.get(c["instrument_key"], {})
                ltp = q.get("last_price", 0.0)

                # Upstox full quote puts oi, volume, etc. at top level
                # Note: Greeks (delta, theta, iv) are not available from REST API.
                # They require full-mode WebSocket streaming. For now, REST provides LTP/OI/Volume.
                data = {
                    "instrument_key": c["instrument_key"],
                    "ltp": float(ltp or 0),
                    "volume": int(q.get("volume") or 0),
                    "oi": float(q.get("oi") or 0.0),
                    "iv": float(q.get("iv") or 0.0),
                    "delta": float(q.get("delta") or 0.0),
                    "theta": float(q.get("theta") or 0.0),
                    "gamma": float(q.get("gamma") or 0.0),
                    "vega": float(q.get("vega") or 0.0),
                    # Fallback to streamer cache for Greeks if available
                    **(greeks_cache.get(c["instrument_key"], {}) if greeks_cache else {}),
                }

                # Classification based on instrument_type (Upstose SDK field)
                opt_type = c.get("instrument_type", "").lower()
                if opt_type == "ce":
                    strikes[sp]["ce"] = data
                elif opt_type == "pe":
                    strikes[sp]["pe"] = data

            # 5. Sort and return
            matrix = sorted(strikes.values(), key=lambda x: x["strike_price"])

            return {
                "status": "success",
                "instrument_key": instrument_key,
                "spot_price": spot_price,
                "expiry_date": target_expiry,
                "requested_expiry": requested_expiry,
                "expiry_fallback_used": expiry_fallback_used,
                "available_expiries": all_expiries,
                "chain": matrix
            }

        except Exception as e:
            logger.error(f"Detailed option chain failed: {e}")
            return {"status": "error", "message": str(e), "chain": []}
