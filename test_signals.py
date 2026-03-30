import asyncio
import pandas as pd
from app.config import get_settings
from app.auth.service import get_auth_service
from app.market_data.service import MarketDataService
from app.strategies.scalp_pro import ScalpPro
from app.strategies.base import StrategyConfig

async def main():
    settings = get_settings()
    settings.load_from_db()  # Ensure db loaded
    auth = get_auth_service()
    
    live_config = auth.get_configuration(use_sandbox=False)
    if not live_config.access_token:
        print("No live access token found! Cannot fetch market data.")
        return
        
    mds = MarketDataService(live_config)
    
    print("Fetching 1m candles for Nifty 50...")
    candles = await asyncio.to_thread(mds.get_intraday_candles, "NSE_INDEX|Nifty 50", "1minute")
    
    if not candles:
        print("No candles returned.")
        return
        
    df = pd.DataFrame(candles)
    if "datetime" in df.columns:
        df["datetime"] = pd.to_datetime(df["datetime"])
        time_key = "datetime"
    elif "time" in df.columns:
        df["time"] = pd.to_datetime(df["time"])
        time_key = "time"
    else:
        print("Columns: ", df.columns)
        return
        
    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
            
    # Fix Upstox Unix timestamp if it is numerical (e.g. 177...)
    # Some Upstox formats return timezone-aware ISO strings.
    # If the column has numbers, treat as unix seconds:
    try:
        df[time_key] = pd.to_datetime(df[time_key], errors='coerce')
        # If year is 1970, it means it parsed a numeric unix timestamp (seconds) natively as ns
        if df[time_key].dt.year.iloc[0] == 1970 and df[time_key].astype(int).iloc[0] < 2e9:
            df[time_key] = pd.to_datetime(df[time_key].astype(int), unit="s")
    except Exception:
        pass

    # Restrict to last 1000 candles (approx 2.5 days on 1m chart) to avoid excessive calculation times
    df = df.tail(1000).reset_index(drop=True)

    print(f"Testing on last {len(df)} 1m candles. First: {df[time_key].iloc[0]}, Last: {df[time_key].iloc[-1]}")
    
    # Configure strategy for 1m
    params = ScalpPro.default_params()
    config = StrategyConfig(
        name="Scalp test", enabled=True, instruments=["NSE_INDEX|Nifty 50"], 
        timeframe="1minute", params=params, paper_trading=True
    )
    strategy = ScalpPro(config)
    
    signals = []
    print("Analyzing candles...")
    for i in range(100, len(df) + 1):
        window = df.iloc[:i]
        try:
            signal = strategy.on_candle(window, htf_df=None)
            if signal:
                signals.append({
                    "time": window[time_key].iloc[-1],
                    "action": signal.action.value,
                    "price": signal.price,
                    "score": signal.confidence_score
                })
        except Exception as e:
            print(f"Error at {i}: {e}")
            
    if not signals:
        print("No signals generated in this period.")
    else:
        print(f"Found {len(signals)} signals!")
        for s in signals:
            print(f"{s['time']} - {s['action']} @ {s['price']} (Score: {s['score']})")

if __name__ == "__main__":
    asyncio.run(main())
