import asyncio
import logging
from app.engine import get_engine
from app.database.connection import get_session, Watchlist

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test_tf")

async def test_custom_timeframes():
    engine = get_engine()
    engine.initialize()
    
    # 1. Clear Watchlist and add a test instrument with custom TFs
    session = get_session()
    session.query(Watchlist).delete()
    
    # Test instrument: NIFTY 50
    test_key = "NSE_INDEX|Nifty 50"
    w1 = Watchlist(
        instrument_key=test_key,
        symbol="NIFTY 50",
        name="Nifty 50",
        timeframes=["5m", "15m", "1H"]
    )
    session.add(w1)
    session.commit()
    session.close()
    
    # 2. Add SuperTrend strategy for the custom watchlist
    engine.load_strategy(
        strategy_class_name="SuperTrendPro",
        name="TestStrategy",
        instruments=["CUSTOM_WATCHLIST"],
        timeframe="15m"  # This should be overridden
    )
    
    # 3. We want to patch MarketDataService to track what gets requested
    original_get = engine._market_service.get_intraday_candles
    
    requested_tfs = []
    def mock_get_candles(instrument_key, interval):
        requested_tfs.append((instrument_key, interval))
        return [] # Return empty to avoid full execution
        
    engine._market_service.get_intraday_candles = mock_get_candles
    
    # Run cycle
    await engine.run_cycle()
    
    logger.info(f"Requested Market Data intervals: {requested_tfs}")
    
    # Check if 5m, 15m, 60m were requested
    intervals = [req[1] for req in requested_tfs]
    if "5minute" in intervals and "15minute" in intervals and "60minute" in intervals:
        logger.info("✅ SUCCESS: Engine respects custom timeframes from watchlist.")
    else:
        logger.error(f"❌ FAILURE: Expected 5m, 15m, 1H, but got {intervals}")

if __name__ == "__main__":
    asyncio.run(test_custom_timeframes())
