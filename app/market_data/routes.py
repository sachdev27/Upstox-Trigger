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


@router.get("/option-chain")
async def get_option_chain(
    instrument_key: str = Query(...),
    expiry_date: str | None = Query(None)
):
    """Fetch live option chain (Calls, Puts, Greeks) for an instrument."""
    svc = _get_market_service()
    
    import upstox_client
    from upstox_client.rest import ApiException
    
    try:
        api = upstox_client.OptionsApi(upstox_client.ApiClient(svc.config))
        
        # 1. Resolve Expiry Date if not provided by pulling nearest contract
        if not expiry_date:
            contracts_res = api.get_option_contracts(instrument_key)
            contracts_data = contracts_res.to_dict().get("data", [])
            if not contracts_data:
                return {"status": "error", "message": "No option contracts found for this instrument.", "chain": []}
            expiry_date = contracts_data[0].get("expiry")
            
        if not expiry_date:
            return {"status": "error", "message": "Could not determine expiry date.", "chain": []}
            
        # 2. Fetch Option Chain directly via the SDK
        chain_res = api.get_put_call_option_chain(instrument_key, expiry_date)
        chain_data = chain_res.to_dict().get("data", [])
        
        # 3. Flatten the heavily nested SDK objects into a clean 2D Dict Matrix
        matrix = []
        for strike in chain_data:
            sp = strike.get("strike_price")
            pcr = strike.get("pcr")
            
            ce = strike.get("call_options", {})
            pe = strike.get("put_options", {})
            
            if not ce and not pe:
                continue
                
            def _extract_greeks(opt_data):
                if not opt_data: return {}
                g = opt_data.get("option_greeks", {}) or {}
                m = opt_data.get("market_data", {}) or {}
                return {
                    "instrument_key": opt_data.get("instrument_key"),
                    "ltp": m.get("ltp", 0.0),
                    "volume": m.get("volume", 0),
                    "oi": m.get("oi", 0.0),
                    "iv": g.get("iv", 0.0),
                    "delta": g.get("delta", 0.0),
                    "theta": g.get("theta", 0.0),
                    "gamma": g.get("gamma", 0.0),
                    "vega": g.get("vega", 0.0),
                }

            matrix.append({
                "strike_price": sp,
                "pcr": pcr,
                "ce": _extract_greeks(ce),
                "pe": _extract_greeks(pe)
            })
            
        # Sort by strike price ascending
        matrix.sort(key=lambda x: x["strike_price"])
        
        return {
            "status": "success",
            "instrument_key": instrument_key,
            "expiry": expiry_date,
            "chain": matrix
        }
        
    except ApiException as e:
        import json
        try:
            err_body = json.loads(e.body)
            msg = err_body.get('errors', [{}])[0].get('message', e.body)
        except:
            msg = e.body
        return {"status": "error", "message": f"Upstox API Error {e.status}: {msg}", "chain": []}
    except Exception as e:
        return {"status": "error", "message": str(e), "chain": []}

