"""
Market Data API routes — quotes, candles, instruments, option chain.

NOTE: Positions, holdings, and funds routes live in orders/routes.py.
"""

import time

from fastapi import APIRouter, Query, Request

from app.auth.service import get_auth_service
from app.market_data.service import MarketDataService

router = APIRouter(prefix="/market", tags=["Market Data"])

_strategy_overlay_cache: dict[tuple, tuple[float, dict]] = {}
_strategy_overlay_cache_ttl_sec = 10.0
_strategy_overlay_cache_max_entries = 256
_market_service_singleton: MarketDataService | None = None
_market_service_token: str | None = None


def _get_market_service() -> MarketDataService:
    global _market_service_singleton, _market_service_token

    auth = get_auth_service()
    config = auth.get_configuration(use_sandbox=False)
    token = getattr(config, "access_token", None)

    if _market_service_singleton is None or token != _market_service_token:
        _market_service_singleton = MarketDataService(config)
        _market_service_token = token

    return _market_service_singleton


@router.get("/ltp")
async def get_ltp(instrument_key: str = Query(...)):
    """Get last traded price."""
    svc = _get_market_service()
    ltp = svc.get_ltp(instrument_key)
    return {"instrument_key": instrument_key, "ltp": ltp}


@router.get("/quote")
async def get_quote(instrument_key: str = Query(...)):
    """Get full market quote."""
    svc = _get_market_service()
    quote = svc.get_full_quote(instrument_key)
    return {"instrument_key": instrument_key, "data": quote}


@router.get("/candles")
async def get_candles(
    instrument_key: str = Query(...),
    interval: str = Query("1minute"),
    from_date: str | None = Query(None),
    to_date: str | None = Query(None),
):
    """Get historical candle data."""
    svc = _get_market_service()
    candles = svc.get_historical_candles(
        instrument_key, interval, from_date, to_date
    )
    return {"instrument_key": instrument_key, "count": len(candles), "candles": candles}


# ── REMOVED: /market/positions, /market/holdings, /market/funds ────────────
# These are now ONLY in orders/routes.py to avoid duplicate routes.


@router.get("/profile")
async def get_profile():
    """Get user profile."""
    svc = _get_market_service()
    return {"data": svc.get_profile()}


@router.get("/instruments/featured")
async def get_featured_instruments():
    """Return featured instruments from the database for the UI default Watchlist."""
    from app.database.connection import get_session, Instrument

    session = get_session()
    try:
        db_insts = session.query(Instrument).filter(
            (Instrument.instrument_type == 'INDEX') |
            (Instrument.instrument_type == 'EQUITY')
        ).limit(100).all()

        instruments = []
        for inst in db_insts:
            instruments.append({
                "name": inst.name,
                "instrument_key": inst.instrument_key,
                "segment": inst.exchange,
                "symbol": inst.symbol
            })

        return {"status": "success", "count": len(instruments), "instruments": instruments}
    except Exception as e:
        return {"status": "error", "message": f"Failed to fetch from DB: {e}"}
    finally:
        session.close()


@router.get("/instruments/search")
async def search_instruments(
    query: str = Query(..., description="Search term, e.g. 'Reliance' or 'NIFTY'"),
    page_size: int = Query(20),
):
    """Search instruments using the SDK (no CSV download needed)."""
    svc = _get_market_service()
    results = svc.search_instrument_sdk(query, page_size)
    return {"query": query, "count": len(results), "instruments": results}


@router.get("/status")
async def get_market_status(exchange: str = Query("NSE")):
    """Get real-time market status (open/closed)."""
    svc = _get_market_service()
    return {"data": svc.get_market_status(exchange)}


@router.get("/holidays")
async def get_holidays():
    """Get list of market holidays."""
    svc = _get_market_service()
    return {"data": svc.get_holidays()}


@router.get("/exchange-timings")
async def get_exchange_timings(date: str = Query(..., description="YYYY-MM-DD")):
    """Get exchange timings for a specific date."""
    svc = _get_market_service()
    return {"data": svc.get_exchange_timings(date)}


@router.get("/options/chain")
async def get_option_chain_simple(
    instrument_key: str = Query(...),
    expiry_date: str = Query(..., description="YYYY-MM-DD"),
):
    """Get put/call option chain (simple view)."""
    svc = _get_market_service()
    return {"data": svc.get_option_chain(instrument_key, expiry_date)}


@router.get("/options/contracts")
async def get_option_contracts(
    instrument_key: str = Query(...),
    expiry_date: str | None = Query(None),
):
    """Get available option contracts."""
    svc = _get_market_service()
    return {"data": svc.get_option_contracts(instrument_key, expiry_date)}


@router.get("/brokerage")
async def get_brokerage(
    instrument_token: str = Query(...),
    quantity: int = Query(...),
    product: str = Query("I"),
    transaction_type: str = Query("BUY"),
    price: float = Query(...),
):
    """Calculate brokerage charges before placing a trade."""
    svc = _get_market_service()
    return {"data": svc.get_brokerage(
        instrument_token, quantity, product, transaction_type, price
    )}


@router.get("/strategy-overlay")
async def get_strategy_overlay(
    instrument_key: str = Query(...),
    timeframe: str = Query("1minute"),
    from_date: str | None = Query(None),
    to_date: str | None = Query(None),
    strategy_class: str = Query("SuperTrendPro"),
    params: str = Query("{}")
):
    """
    Compute algorithmic indicator arrays for the frontend chart.
    Accepts arbitrary JSON parameter payloads to adapt to dynamic Web UI forms.
    """
    import json

    cache_key = (instrument_key, timeframe, from_date, to_date, strategy_class, params)
    now_mono = time.monotonic()
    cached = _strategy_overlay_cache.get(cache_key)
    if cached:
        ts, payload = cached
        if (now_mono - ts) <= _strategy_overlay_cache_ttl_sec:
            return payload

    svc = _get_market_service()
    candles = svc.get_historical_candles(instrument_key, timeframe, from_date, to_date)

    if not candles:
        return {"status": "error", "message": "No candle data available.", "overlay": []}

    import pandas as pd
    from app.strategies.indicators import supertrend
    from app.strategies.supertrend_pro import SuperTrendPro
    from app.strategies.base import StrategyConfig

    try:
        parsed_params = json.loads(params)
    except Exception:
        parsed_params = {}

    df = pd.DataFrame(candles)
    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    config = StrategyConfig(name="Dynamic Execution", instruments=[instrument_key], timeframe=timeframe)

    if strategy_class == "SuperTrendPro":
        strategy = SuperTrendPro(config)
        strategy.params.update(parsed_params)

        htf_df = None
        if strategy.params.get("use_htf_filter"):
            htf_tf = strategy.params.get("htf_timeframe", "1D")
            tf_map = {
                "1D": "day", "D": "day", "W": "week", "1W": "week",
                "1H": "60minute", "60m": "60minute", "60minute": "60minute",
                "30m": "30minute", "30minute": "30minute",
                "15m": "15minute", "15minute": "15minute",
                "5m": "5minute", "5minute": "5minute",
                "1m": "1minute", "1minute": "1minute",
            }
            htf_interval = tf_map.get(str(htf_tf), "day")
            htf_candles = svc.get_historical_candles(instrument_key, htf_interval, from_date, to_date)
            if htf_candles:
                htf_df = pd.DataFrame(htf_candles)
                for col in ["open", "high", "low", "close", "volume"]:
                    if col in htf_df.columns:
                        htf_df[col] = pd.to_numeric(htf_df[col], errors="coerce")

        metrics = strategy.get_dashboard_state(df, htf_df=htf_df)

        p_atr = strategy.params.get("atr_period", 10)
        p_mult = strategy.params.get("atr_multiplier", 3.0)
        st_df = supertrend(df, period=p_atr, multiplier=p_mult, use_rma=True)
        trend = st_df["trend"]

        # Only evaluate full strategy logic at points where the primary trend flips!
        # This keeps the API response blazing fast (O(1) in the number of flips).
        import numpy as np
        flips = (trend != trend.shift(1)) & (trend.shift(1).notna())
        flip_indices = np.where(flips)[0]

        valid_signals = {}
        for idx in flip_indices:
            if idx < 100: continue
            window = df.iloc[:idx+1]
            try:
                sig = strategy.on_candle(window, htf_df=htf_df)
                if sig:
                    valid_signals[df.iloc[idx]["time"]] = sig.action.value
            except Exception:
                pass

        overlay = []
        for i in range(len(df)):
            c = df.iloc[i]
            s = st_df.iloc[i]
            overlay.append({
                "time": c["time"],
                "trend": int(s["trend"]) if pd.notna(s["trend"]) else 1,
                "supertrend": None if pd.isna(s["supertrend"]) else float(s["supertrend"]),
                "upper": None if pd.isna(s["upper_band"]) else float(s["upper_band"]),
                "lower": None if pd.isna(s["lower_band"]) else float(s["lower_band"]),
                "signal": valid_signals.get(c["time"], None)
            })

        payload = {"status": "success", "instrument_key": instrument_key, "overlay": overlay, "latest_metrics": metrics}
        if len(_strategy_overlay_cache) >= _strategy_overlay_cache_max_entries:
            _strategy_overlay_cache.clear()
        _strategy_overlay_cache[cache_key] = (now_mono, payload)
        return payload

    elif strategy_class == "ScalpPro":
        from app.strategies.scalp_pro import ScalpPro
        from app.strategies.indicators import ema
        import numpy as np

        strategy = ScalpPro(config)
        strategy.params.update(parsed_params)
        metrics = strategy.get_dashboard_state(df)

        fast_line = ema(df["close"], strategy.params.get("fast_ema", 9))
        slow_line = ema(df["close"], strategy.params.get("slow_ema", 21))

        trend = np.where(fast_line > slow_line, 1, -1)
        trend = pd.Series(trend, index=df.index)

        flips = (trend != trend.shift(1)) & (trend.shift(1).notna())
        flip_indices = np.where(flips)[0]

        valid_signals = {}
        for idx in flip_indices:
            if idx < 100: continue
            window = df.iloc[:idx+1]
            try:
                sig = strategy.on_candle(window, htf_df=None)
                if sig:
                    valid_signals[df.iloc[idx]["time"]] = sig.action.value
            except Exception:
                pass

        overlay = []
        for i in range(len(df)):
            c = df.iloc[i]
            overlay.append({
                "time": c["time"],
                "trend": int(trend.iloc[i]),
                "supertrend": float(fast_line.iloc[i]) if pd.notna(fast_line.iloc[i]) else None,
                "upper": float(slow_line.iloc[i]) if pd.notna(slow_line.iloc[i]) else None,
                "lower": None,
                "signal": valid_signals.get(c["time"], None)
            })

        payload = {"status": "success", "instrument_key": instrument_key, "overlay": overlay, "latest_metrics": metrics}
        if len(_strategy_overlay_cache) >= _strategy_overlay_cache_max_entries:
            _strategy_overlay_cache.clear()
        _strategy_overlay_cache[cache_key] = (now_mono, payload)
        return payload

    else:
        return {"status": "error", "message": f"Strategy class {strategy_class} native graphics overlay not yet supported.", "overlay": []}


@router.get("/option-chain")
async def get_detailed_option_chain(
    request: Request,
    instrument_key: str = Query(...),
    expiry_date: str | None = Query(None),
):
    """
    Get full option chain matrix with LTP and Greeks for a given index/stock and expiry.
    Uses Greeks from the WebSocket streamer cache for accuracy.
    """
    try:
        # Resolve common index aliases
        if "nifty 50" in instrument_key.lower():
            instrument_key = "NSE_INDEX|Nifty 50"
        elif "bank nifty" in instrument_key.lower() or "nifty bank" in instrument_key.lower():
            instrument_key = "NSE_INDEX|Nifty Bank"

        svc = _get_market_service()
        # Greeks cache is optional fallback; REST API provides LTP/OI/Volume
        greeks_cache = getattr(request.app.state, "greeks_cache", {})
        result = await svc.get_detailed_option_chain(instrument_key, expiry_date, greeks_cache)
        return result

    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Option chain route failed: {e}")
        return {"status": "error", "message": str(e), "chain": [], "available_expiries": []}


@router.get("/option-chain/analysis")
async def get_option_chain_analysis(
    request: Request,
    instrument_key: str = Query(..., description="e.g. NSE_INDEX|Nifty 50"),
    expiry_date: str | None = Query(None),
):
    """
    Real-time option chain analytics: PCR, OI concentration, Max-Pain, IV Skew.

    Returns sentiment, directional score (-100 to +100), support/resistance levels,
    and human-readable signal explanations.
    """
    try:
        from app.market_data.option_analysis import analyze_option_chain

        if "nifty 50" in instrument_key.lower():
            instrument_key = "NSE_INDEX|Nifty 50"
        elif "bank nifty" in instrument_key.lower() or "nifty bank" in instrument_key.lower():
            instrument_key = "NSE_INDEX|Nifty Bank"

        svc = _get_market_service()
        greeks_cache = getattr(request.app.state, "greeks_cache", {})
        chain_data = await svc.get_detailed_option_chain(instrument_key, expiry_date, greeks_cache)

        if chain_data.get("status") != "success" or not chain_data.get("chain"):
            return {"status": "error", "message": "No chain data available"}

        analysis = analyze_option_chain(
            chain_data["chain"],
            float(chain_data.get("spot_price") or 0),
        )
        return {
            "status": "success",
            "instrument_key": instrument_key,
            "expiry_date": chain_data.get("expiry_date"),
            "analysis": analysis,
        }

    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Option chain analysis failed: {e}")
        return {"status": "error", "message": str(e)}
