import asyncio
from app.config import get_config
from app.market_data.service import MarketDataService

async def main():
    c = get_config()
    svc = MarketDataService(c)
    candles = svc.get_historical_candles("NSE_INDEX|Nifty 50", "15minute", "2026-03-20", "2026-03-25")
    print(f"Total candles: {len(candles)}")
    for i in range(5):
        if i < len(candles):
            print(candles[i])

asyncio.run(main())
