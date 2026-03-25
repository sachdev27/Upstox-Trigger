"""
Market Data API routes — quotes, candles, instruments.
"""

from fastapi import APIRouter, Depends, Query

from app.auth.service import get_auth_service
from app.market_data.service import MarketDataService

router = APIRouter(prefix="/market", tags=["Market Data"])


def _get_market_service() -> MarketDataService:
    auth = get_auth_service()
    config = auth.get_configuration()
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


@router.post("/instruments/download")
async def download_instruments():
    """Download latest instrument list from Upstox."""
    path = MarketDataService.download_instrument_list()
    return {"status": "success", "path": str(path)}


@router.get("/instruments/featured")
async def get_featured_instruments():
    """Return Nifty 50 instruments from local CSV for the UI default Watchlist."""
    import csv
    from app.config import BASE_DIR
    
    csv_path = BASE_DIR / "ind_nifty50list.csv"
    instruments = []
    
    # Add major indices manually
    instruments.append({"name": "Nifty 50", "instrument_key": "NSE_INDEX|Nifty 50", "segment": "NSE_INDEX"})
    instruments.append({"name": "Nifty Bank", "instrument_key": "NSE_INDEX|Nifty Bank", "segment": "NSE_INDEX"})

    if csv_path.exists():
        with open(csv_path, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                symbol = row.get("Symbol")
                isin = row.get("ISIN Code")
                if symbol and isin:
                    instruments.append({
                        "name": symbol,
                        "instrument_key": f"NSE_EQ|{isin}",
                        "segment": "NSE_EQ"
                    })
    
    return {"status": "success", "count": len(instruments), "instruments": instruments}


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
    atr_period: int = Query(10),
    multiplier: float = Query(3.0)
):
    """
    Compute SuperTrend indicator arrays for the frontend chart.
    Returns a sequence of {time, upper, lower, trend} mapped to the candles.
    """
    svc = _get_market_service()
    candles = svc.get_historical_candles(instrument_key, timeframe, from_date, to_date)
    
    if not candles:
        return {"status": "error", "message": "No candle data available.", "overlay": []}
    
    import pandas as pd
    from app.strategies.indicators import supertrend
    from app.strategies.supertrend_pro import SuperTrendPro
    from app.strategies.base import StrategyConfig
    
    df = pd.DataFrame(candles)
    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
            
    # Calculate dashboard matrix
    config = StrategyConfig(name="SuperTrend Pro v6.3", instruments=[instrument_key], timeframe=timeframe)
    strategy = SuperTrendPro(config)
    strategy.params["atr_period"] = atr_period
    strategy.params["atr_multiplier"] = multiplier
    metrics = strategy.get_dashboard_state(df)
    
    st_df = supertrend(df, period=atr_period, multiplier=multiplier, use_rma=True)
    
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

