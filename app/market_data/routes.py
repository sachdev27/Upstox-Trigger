"""
Market Data API routes — quotes, candles, instruments.
"""

from fastapi import APIRouter, Depends, Query

from app.auth.service import get_auth_service
from app.market_data.service import MarketDataService

router = APIRouter(prefix="/market", tags=["Market Data"])


def _get_market_service() -> MarketDataService:
    auth = get_auth_service()
    # Market data always requires Live configuration
    config = auth.get_configuration(use_sandbox=False)
    return MarketDataService(config)


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


@router.get("/positions")
async def get_positions():
    """Get current positions."""
    svc = _get_market_service()
    return {"data": svc.get_positions()}


@router.get("/holdings")
async def get_holdings():
    """Get current holdings."""
    svc = _get_market_service()
    return {"data": svc.get_holdings()}


@router.get("/funds")
async def get_funds():
    """Get funds and margin."""
    svc = _get_market_service()
    return {"data": svc.get_funds_and_margin()}


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
        # Fetch indices and a subset of equities
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
async def get_option_chain(
    instrument_key: str = Query(...),
    expiry_date: str = Query(..., description="YYYY-MM-DD"),
):
    """Get put/call option chain."""
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
            
    # Calculate dashboard matrix
    config = StrategyConfig(name="Dynamic Execution", instruments=[instrument_key], timeframe=timeframe)
    
    if strategy_class == "SuperTrendPro":
        strategy = SuperTrendPro(config)
        strategy.params.update(parsed_params)
        metrics = strategy.get_dashboard_state(df)
        
        # Native SuperTrend Extraction
        p_atr = strategy.params.get("atr_period", 10)
        p_mult = strategy.params.get("atr_multiplier", 3.0)
        st_df = supertrend(df, period=p_atr, multiplier=p_mult, use_rma=True)
        
        overlay = []
        for i in range(len(df)):
            c = df.iloc[i]
            s = st_df.iloc[i]
            overlay.append({
                "datetime": c["datetime"],
                "trend": int(s["trend"]),
                "supertrend": None if pd.isna(s["supertrend"]) else float(s["supertrend"]),
                "upper": None if pd.isna(s["upper_band"]) else float(s["upper_band"]),
                "lower": None if pd.isna(s["lower_band"]) else float(s["lower_band"])
            })
            
        return {"status": "success", "instrument_key": instrument_key, "overlay": overlay, "latest_metrics": metrics}
    else:
        return {"status": "error", "message": f"Strategy class {strategy_class} native graphics overlay not yet supported.", "overlay": []}


@router.get("/option-chain", tags=["Market Data"])
async def get_option_chain(
    instrument_key: str = Query(...),
    expiry_date: str | None = Query(None)
):
    """
    Get full option chain matrix with LTP and Greeks for a given index/stock and expiry.
    """
    try:
        # Resolve common index aliases
        if "nifty 50" in instrument_key.lower():
            instrument_key = "NSE_INDEX|Nifty 50"
        elif "bank nifty" in instrument_key.lower() or "nifty bank" in instrument_key.lower():
            instrument_key = "NSE_INDEX|Nifty Bank"
            
        svc = _get_market_service()
        # Use the shared service method
        result = await svc.get_detailed_option_chain(instrument_key, expiry_date)
        return result
        
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Option chain route failed: {e}")
        return {"status": "error", "message": str(e), "chain": [], "available_expiries": []}

