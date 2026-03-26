"""
Engine API routes — control the automation engine from the dashboard.

Config changes are persisted to the database.
"""

from fastapi import APIRouter

from app.engine import get_engine
from app.config import get_settings

router = APIRouter(prefix="/engine", tags=["Automation Engine"])


@router.post("/initialize")
async def initialize_engine():
    """Initialize the automation engine (auth + services)."""
    engine = get_engine()
    engine.initialize()
    return {"status": "initialized" if engine._is_initialized else "failed"}


@router.post("/load-strategy")
async def load_strategy(
    strategy_class: str = "SuperTrendPro",
    name: str = "SuperTrend Pro v6.3",
    instruments: str = "NSE_INDEX|Nifty 50",
    timeframe: str = "15m",
    paper_trading: bool = True,
):
    """Load a strategy into the engine."""
    engine = get_engine()
    instrument_list = [i.strip() for i in instruments.split(",")]
    engine.load_strategy(
        strategy_class_name=strategy_class,
        name=name,
        instruments=instrument_list,
        timeframe=timeframe,
        paper_trading=paper_trading,
    )
    return {"status": "loaded", "strategy": name, "instruments": instrument_list}


@router.post("/run-cycle")
async def run_cycle():
    """Manually trigger one strategy evaluation cycle."""
    engine = get_engine()
    await engine.run_cycle()
    return {
        "status": "cycle_complete",
        "signals": engine.get_signals_log()[-5:],
        "trades": engine.get_trades_log()[-5:],
    }


@router.post("/auto-mode")
async def toggle_auto_mode(enabled: bool):
    """Enable or disable autonomous trading loop."""
    engine = get_engine()
    engine.auto_mode = enabled
    return {"status": "success", "auto_mode": engine.auto_mode}


@router.post("/config")
async def update_engine_config(config: dict):
    """Update engine risk, capital, and trading mode settings. Persisted to DB."""
    settings = get_settings()
    engine = get_engine()

    # Map of config keys → (settings key, category)
    key_map = {
        "trading_capital": ("TRADING_CAPITAL", "ENGINE"),
        "risk_per_trade_pct": ("MAX_RISK_PER_TRADE_PCT", "RISK"),
        "max_daily_loss_pct": ("MAX_DAILY_LOSS_PCT", "RISK"),
        "max_open_trades": ("MAX_OPEN_TRADES", "ENGINE"),
        "paper_trading": ("PAPER_TRADING", "ENGINE"),
        "trading_side": ("TRADING_SIDE", "ENGINE"),
        "auto_mode": (None, None),  # In-memory only
    }

    for key, value in config.items():
        mapping = key_map.get(key)
        if mapping and mapping[0]:
            settings.save_to_db(mapping[0], str(value), category=mapping[1])
        if key == "auto_mode":
            engine.auto_mode = value

    # Refresh engine from DB
    engine.sync_from_settings()

    return {"status": "success", "config": engine.get_status()}


@router.post("/test-signal")
async def trigger_test_signal(payload: dict):
    """Manually trigger a test signal for an instrument."""
    engine = get_engine()
    instrument_key = payload.get("instrument_key")
    if not instrument_key:
        return {"status": "error", "message": "Missing instrument_key"}
        
    res = await engine.trigger_test_signal(instrument_key)
    return {"status": "success", "result": res}


@router.get("/status")
async def engine_status():
    """Get current engine status."""
    engine = get_engine()
    return engine.get_status()


@router.get("/signals")
async def get_signals():
    """Get all signals generated today."""
    engine = get_engine()
    return {"signals": engine.get_signals_log()}


@router.get("/trades")
async def get_trades():
    """Get all trades executed today."""
    engine = get_engine()
    return {"trades": engine.get_trades_log()}


@router.post("/reset")
async def reset_daily():
    """Reset daily counters."""
    engine = get_engine()
    engine.reset_daily()
    return {"status": "reset"}
