import urllib.request
from datetime import datetime, timedelta
import json

def test_range(days):
    end = datetime.now()
    start = end - timedelta(days=days)
    url = f"http://localhost:8210/market/candles?instrument_key=NSE_INDEX%7CNifty%2050&interval=1minute&from_date={start.strftime('%Y-%m-%d')}&to_date={end.strftime('%Y-%m-%d')}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read())
            print(f"{days} days -> Extracted {len(data.get('candles', []))} candles")
    except Exception as e:
        print(f"{days} days -> FAILED: {str(e)}")

for days in [25, 28, 30, 31, 35]:
    test_range(days)
