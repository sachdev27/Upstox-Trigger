"""
Smoke test — verify all modules import cleanly.
"""


def test_config_imports():
    from app.config import get_settings, Settings
    assert Settings is not None


def test_auth_imports():
    from app.auth.service import AuthService, get_auth_service
    from app.auth.routes import router
    assert router is not None


def test_market_data_imports():
    from app.market_data.service import MarketDataService
    from app.market_data.streamer import MarketDataStreamer
    from app.market_data.routes import router
    assert router is not None


def test_orders_imports():
    from app.orders.models import OrderRequest, TradeSignal, OrderType, TransactionType
    from app.orders.service import OrderService
    from app.orders.routes import router
    assert router is not None


def test_strategies_imports():
    from app.strategies.base import BaseStrategy, StrategyConfig
    from app.strategies.indicators import (
        atr, supertrend, adx, roc,
        bollinger_bands, bb_squeeze,
        volume_surge, atr_percentile,
        consecutive_confirming_bars,
    )
    from app.strategies.supertrend_pro import SuperTrendPro
    from app.strategies.routes import router
    assert router is not None


def test_scheduler_imports():
    from app.scheduler.service import SchedulerService
    assert SchedulerService is not None


def test_database_imports():
    from app.database.connection import (
        Base, TradeLog, StrategyState, CandleCache,
        Watchlist, ActiveSignal,
        get_engine, get_session, init_db,
    )
    assert Base is not None
    assert ActiveSignal is not None


def test_main_app_imports():
    from app.main import app
    assert app is not None


def test_supertrend_strategy_default_params():
    """Verify SuperTrend Pro has all expected params."""
    from app.strategies.supertrend_pro import SuperTrendPro

    params = SuperTrendPro.default_params()
    expected_keys = [
        "atr_period", "atr_multiplier",
        "use_dual_st", "slow_atr_period",
        "use_consecutive", "exit_mode",
        "sl_multiplier", "risk_per_trade_pct",
    ]
    for key in expected_keys:
        assert key in params, f"Missing param: {key}"


def test_supertrend_strategy_instantiation():
    """Verify SuperTrend Pro can be instantiated."""
    from app.strategies.supertrend_pro import SuperTrendPro
    from app.strategies.base import StrategyConfig

    config = StrategyConfig(
        name="Test ST Pro",
        instruments=["NSE_EQ|INE848E01016"],
        timeframe="15m",
    )
    strategy = SuperTrendPro(config)
    assert strategy.name == "Test ST Pro"
    assert strategy.params["atr_period"] == 10


def test_indicators_supertrend():
    """Verify SuperTrend indicator produces correct output shape."""
    import pandas as pd
    import numpy as np
    from app.strategies.indicators import supertrend

    np.random.seed(42)
    n = 200
    close = 100 + np.cumsum(np.random.randn(n) * 0.5)
    high = close + np.random.rand(n) * 2
    low = close - np.random.rand(n) * 2

    df = pd.DataFrame({"open": close, "high": high, "low": low, "close": close})
    result = supertrend(df, period=10, multiplier=3.0)

    assert "supertrend" in result.columns
    assert "trend" in result.columns
    assert len(result) == n
    assert set(result["trend"].dropna().unique()).issubset({1, -1})


def test_supertrend_dashboard_state_with_dual_st_enabled():
    """Regression: get_dashboard_state must not raise DataFrame truthiness errors."""
    import pandas as pd
    import numpy as np
    from app.strategies.supertrend_pro import SuperTrendPro
    from app.strategies.base import StrategyConfig

    np.random.seed(7)
    n = 220
    close = 22000 + np.cumsum(np.random.randn(n) * 8)
    high = close + np.random.rand(n) * 12
    low = close - np.random.rand(n) * 12
    volume = np.random.randint(1000, 5000, size=n)

    df = pd.DataFrame({
        "time": np.arange(n),
        "open": close,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })

    cfg = StrategyConfig(name="ST Regression", instruments=["NSE_INDEX|Nifty 50"], timeframe="1minute")
    strategy = SuperTrendPro(cfg)
    strategy.params["use_dual_st"] = True

    state = strategy.get_dashboard_state(df)
    assert isinstance(state, dict)
    assert "hard_gates" in state
    assert state["hard_gates"]["dual_st"] in {"AGREE", "DISAGREE"}
