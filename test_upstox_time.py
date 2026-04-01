from app.market_data.service import MarketDataService
from app.config import get_settings
import upstox_client

svc = MarketDataService(upstox_client.Configuration())
cans = svc.get_historical_candles("NSE_EQ|INE002A01018", "15minute")
if cans:
    print(cans[0])
    from datetime import datetime, timezone
    utc_dt = datetime.fromtimestamp(cans[0]['time'], tz=timezone.utc)
    print(f"UTC Time of first candle: {utc_dt}")
