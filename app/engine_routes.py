"""
Engine API routes — control the automation engine from the dashboard.
"""

from fastapi import APIRouter

from app.engine import get_engine

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
async def update_risk_config(
    trading_capital: float = 100000.0,
    risk_per_trade_pct: float = 1.0,
    max_daily_loss_pct: float = 3.0,
    max_open_trades: int = 3,
):
    """Update engine risk and capital settings."""
    engine = get_engine()
    engine.trading_capital = trading_capital
    engine.risk_per_trade_pct = risk_per_trade_pct
    engine.max_daily_loss_pct = max_daily_loss_pct
    engine.max_open_trades = max_open_trades
    return {"status": "success", "config": engine.get_status()["risk_controls"]}


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
