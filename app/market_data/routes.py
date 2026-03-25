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


@router.get("/instruments/search")
async def search_instruments(
    query: str = Query(..., description="Search term, e.g. 'Reliance' or 'NIFTY'"),
    page_size: int = Query(10),
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

