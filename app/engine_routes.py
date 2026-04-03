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
    params: str = "{}",
    replace_existing: bool = True,
):
    """Load a strategy into the engine."""
    import json

    engine = get_engine()
    instrument_list = [i.strip() for i in instruments.split(",")]
    try:
        parsed_params = json.loads(params) if params else {}
    except Exception:
        parsed_params = {}

    # Persist selection to settings
    settings = get_settings()
    settings.save_to_db("ACTIVE_STRATEGY_CLASS", strategy_class, category="STRATEGY")
    settings.save_to_db("ACTIVE_STRATEGY_NAME", name, category="STRATEGY")

    # Persist strategy params so they survive page refresh
    settings.save_to_db("ACTIVE_STRATEGY_PARAMS", json.dumps(parsed_params), category="STRATEGY")
    settings.save_to_db("ACTIVE_STRATEGY_INSTRUMENTS", instruments, category="STRATEGY")
    settings.save_to_db("ACTIVE_STRATEGY_TIMEFRAME", timeframe, category="STRATEGY")
    settings.save_to_db("ACTIVE_STRATEGY_PAPER", str(paper_trading), category="STRATEGY")

    engine.load_strategy(
        strategy_class_name=strategy_class,
        name=name,
        instruments=instrument_list,
        timeframe=timeframe,
        params=parsed_params,
        paper_trading=paper_trading,
        replace_existing=replace_existing,
    )
    return {
        "status": "loaded",
        "strategy": name,
        "instruments": instrument_list,
        "params_applied": parsed_params,
        "replace_existing": replace_existing,
    }


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
        "use_sandbox": ("USE_SANDBOX", "ENGINE"),
        "auto_mode": (None, None),  # In-memory only
        # GTT Execution settings
        "gtt_product_type": ("GTT_PRODUCT_TYPE", "GTT"),
        "gtt_trailing_sl": ("GTT_TRAILING_SL", "GTT"),
        "gtt_trailing_gap_mode": ("GTT_TRAILING_GAP_MODE", "GTT"),
        "gtt_trailing_gap_value": ("GTT_TRAILING_GAP_VALUE", "GTT"),
        "gtt_market_protection": ("GTT_MARKET_PROTECTION", "GTT"),
        "gtt_entry_trigger_type": ("GTT_ENTRY_TRIGGER_TYPE", "GTT"),
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
    action = str(payload.get("action") or "BUY").upper()
    force_live = bool(payload.get("force_live", False))
    if not instrument_key:
        return {"status": "error", "message": "Missing instrument_key"}
    if action not in {"BUY", "SELL"}:
        return {"status": "error", "message": "action must be BUY or SELL"}

    res = await engine.trigger_test_signal(instrument_key, action=action, force_live=force_live)
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


@router.get("/rejections")
async def get_rejections(limit: int = 50):
    """Get recent signal rejections for diagnostics."""
    engine = get_engine()
    return {"rejections": engine.get_recent_rejections(limit)}


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
