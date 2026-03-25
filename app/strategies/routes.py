"""
Strategy management API routes.
"""

from fastapi import APIRouter

from app.strategies.supertrend_pro import SuperTrendPro
from app.strategies.base import StrategyConfig

router = APIRouter(prefix="/strategies", tags=["Strategies"])

# ── In-memory strategy registry (will move to DB in Phase 2) ──
_strategies: dict[str, dict] = {}


def _register_defaults():
    """Register the built-in SuperTrend Pro strategy."""
    if "supertrend_pro" not in _strategies:
        _strategies["supertrend_pro"] = {
            "name": "SuperTrend Pro v6.3",
            "class": "SuperTrendPro",
            "enabled": False,
            "instruments": [],
            "timeframe": "15m",
            "params": SuperTrendPro.default_params(),
        }


_register_defaults()


@router.get("/")
async def list_strategies():
    """List all registered strategies."""
    return {
        "strategies": [
            {
                "id": sid,
                "name": s["name"],
                "enabled": s["enabled"],
                "instruments": s["instruments"],
                "timeframe": s["timeframe"],
            }
            for sid, s in _strategies.items()
        ]
    }


@router.get("/{strategy_id}")
async def get_strategy(strategy_id: str):
    """Get strategy details including parameters."""
    s = _strategies.get(strategy_id)
    if not s:
        return {"error": "Strategy not found"}
    return {"data": s}


@router.put("/{strategy_id}/params")
async def update_params(strategy_id: str, params: dict):
    """Update strategy parameters."""
    s = _strategies.get(strategy_id)
    if not s:
        return {"error": "Strategy not found"}
    s["params"].update(params)
    return {"status": "updated", "params": s["params"]}


@router.post("/{strategy_id}/toggle")
async def toggle_strategy(strategy_id: str, enabled: bool):
    """Enable or disable a strategy."""
    s = _strategies.get(strategy_id)
    if not s:
        return {"error": "Strategy not found"}
    s["enabled"] = enabled
    return {"status": "toggled", "enabled": s["enabled"]}


@router.put("/{strategy_id}/instruments")
async def set_instruments(strategy_id: str, instruments: list[str]):
    """Set instruments for a strategy."""
    s = _strategies.get(strategy_id)
    if not s:
        return {"error": "Strategy not found"}
    s["instruments"] = instruments
    return {"status": "updated", "instruments": s["instruments"]}


@router.post("/{strategy_id}/dashboard")
async def get_dashboard(strategy_id: str, instrument_key: str):
    """
    Get real-time dashboard state for a strategy.
    Fetches latest candles and computes indicator values.
    """
    s = _strategies.get(strategy_id)
    if not s:
        return {"error": "Strategy not found"}

    # Build the strategy instance
    config = StrategyConfig(
        name=s["name"],
        instruments=[instrument_key],
        timeframe=s["timeframe"],
        params=s["params"],
    )

    if s["class"] == "SuperTrendPro":
        strategy = SuperTrendPro(config)
    else:
        return {"error": "Unknown strategy class"}

    # In production, fetch live candle data here
    # For now, return the param summary
    return {
        "strategy": s["name"],
        "instrument": instrument_key,
        "timeframe": s["timeframe"],
        "params": s["params"],
        "message": "Dashboard will show live data once market data service is connected.",
    }
