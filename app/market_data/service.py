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
        Fetch historical OHLCV candle data.

        Args:
            instrument_key: e.g. "NSE_EQ|INE848E01016" or "NSE_INDEX|Nifty 50"
            interval: "1minute", "30minute", "day", "week", "month"
            from_date: "YYYY-MM-DD" (optional)
            to_date: "YYYY-MM-DD" (optional, defaults to today)

        Returns:
            List of candle dicts: [{datetime, open, high, low, close, volume, oi}, ...]
        """
        api = upstox_client.HistoryApi(
            upstox_client.ApiClient(self.config)
        )
        try:
            if from_date and to_date:
                response = api.get_historical_candle_data1(
                    instrument_key, interval, to_date, from_date,
                    self.api_version
                )
            elif to_date:
                response = api.get_historical_candle_data(
                    instrument_key, interval, to_date, self.api_version
                )
            else:
                response = api.get_intra_day_candle_data(
                    instrument_key, interval, self.api_version
                )

            data = response.to_dict()
            candles_raw = data.get("data", {}).get("candles", [])

            candles = []
            for c in reversed(candles_raw):  # oldest first
                candles.append({
                    "datetime": c[0],
                    "open": c[1],
                    "high": c[2],
                    "low": c[3],
                    "close": c[4],
                    "volume": c[5] if len(c) > 5 else 0,
                    "oi": c[6] if len(c) > 6 else 0,
                })
            return candles

        except Exception as e:
            logger.error(f"Failed to fetch candles for {instrument_key}: {e}")
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

    # ── Instrument Data ─────────────────────────────────────────

    @staticmethod
    def download_instrument_list(
        output_path: str | None = None,
    ) -> Path:
        """
        Download the latest NSE instrument list from Upstox.
        Returns path to the CSV file.
        """
        url = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.csv.gz"
        data_dir = BASE_DIR / "data"
        data_dir.mkdir(exist_ok=True)

        output = Path(output_path) if output_path else data_dir / "instruments.csv"
        temp_gz = data_dir / "instruments_temp.gz"

        try:
            response = requests.get(url, timeout=30)
            response.raise_for_status()

            with open(temp_gz, "wb") as f:
                f.write(response.content)

            with gzip.open(temp_gz, "rb") as gz:
                with open(output, "wb") as out:
                    shutil.copyfileobj(gz, out)

            temp_gz.unlink(missing_ok=True)
            logger.info(f"Instrument list downloaded: {output}")
            return output

        except Exception as e:
            logger.error(f"Failed to download instruments: {e}")
            return output

    @staticmethod
    def find_instrument(
        name: str,
        exchange: str = "NSE_EQ",
        instrument_type: str = "EQUITY",
        csv_path: str | None = None,
    ) -> dict | None:
        """Find an instrument by name in the instrument list CSV."""
        path = Path(csv_path) if csv_path else BASE_DIR / "data" / "instruments.csv"
        if not path.exists():
            logger.error(f"Instrument file not found: {path}")
            return None

        with open(path, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if (
                    row.get("exchange") == exchange
                    and row.get("instrument_type") == instrument_type
                    and row.get("name") == name
                ):
                    return dict(row)
        return None
