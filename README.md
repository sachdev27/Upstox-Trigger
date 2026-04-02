# 🚀 Upstox Trading Automation

Automated trading platform powered by the **Upstox API v2** with pluggable strategies, real-time market data, and order management.

## Architecture

```
app/
├── config.py            # Centralized settings (loads .env)
├── main.py              # FastAPI entry point
├── auth/                # OAuth2 login & token management
├── market_data/         # Historical candles, live WebSocket feeds
├── strategies/          # Strategy engine (BaseStrategy + implementations)
│   ├── base.py          # Abstract base class
│   ├── indicators.py    # ATR, ADX, SuperTrend, BB, ROC, etc.
│   └── supertrend_pro.py  # SuperTrend Pro v6.3 (ported from Pine Script)
├── orders/              # Order execution & risk management
├── scheduler/           # Market-hours task scheduler
└── database/            # SQLite/PostgreSQL via SQLAlchemy
```

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env with your Upstox API credentials
```

### 3. Run the Server

```bash
uvicorn app.main:app --reload --port 8000
```

### 4. Authenticate

1. Visit `http://localhost:8000/auth/login` — redirects to Upstox login
2. After login, the callback saves your access token automatically
3. Check status: `GET http://localhost:8000/auth/status`

### 5. API Docs

Open `http://localhost:8000/docs` for the interactive Swagger UI.

## Static IP / Proxy Setup (Upstox Compliance)

If your Upstox app requires API orders from a whitelisted static IP, configure your OCI proxy and set environment variables:

```bash
# Upstox SDK traffic (used by Auth/Orders/MarketData services)
UPSTOX_PROXY_URL="http://user:pass@140.245.243.157:3128"

# Optional: direct requests() traffic (diagnostics/custom calls)
REQUESTS_HTTP_PROXY="socks5h://user:pass@140.245.243.157:1080"
REQUESTS_HTTPS_PROXY="socks5h://user:pass@140.245.243.157:1080"

# Optional strict mode: refuse to initialize live SDK clients unless proxy exists
REQUIRE_UPSTOX_PROXY=True

# Regulatory header for order APIs (sent as X-Algo-Name)
ALGO_NAME="your-approved-algo-name"
```

Verify outbound IP through proxy:

```bash
./venv/bin/python scripts/verify_proxy_ip.py
# Expected: 140.245.243.157
```

## Key Endpoints

| Endpoint | Description |
|---|---|
| `GET /auth/login` | Start Upstox OAuth login |
| `GET /auth/status` | Check token validity |
| `GET /market/ltp?instrument_key=...` | Get last traded price |
| `GET /market/candles?instrument_key=...` | Get historical candles |
| `GET /market/positions` | Get current positions |
| `GET /strategies/` | List all strategies |
| `PUT /strategies/{id}/params` | Update strategy parameters |
| `POST /strategies/{id}/toggle` | Enable/disable a strategy |
| `POST /orders/place` | Place an order |
| `GET /orders/book` | Get today's order book |

## Built-in Strategies

### SuperTrend Pro v6.3

A sophisticated multi-filter SuperTrend strategy with:
- **Hard Gates:** Dual SuperTrend agreement, consecutive bar confirmation, HTF trend filter
- **Soft Scoring:** ADX, volume surge, ATR percentile, ROC, Bollinger Band squeeze
- **Exit Modes:** ATR SL+TP (A), ATR SL + Profit% (B), ATR SL + Trailing (C)
- **Auto TF Adaptation:** Thresholds adjust to your timeframe automatically

### Adding New Strategies

```python
from app.strategies.base import BaseStrategy, StrategyConfig

class MyStrategy(BaseStrategy):
    @staticmethod
    def default_params():
        return {"period": 14, "threshold": 30}

    def on_candle(self, df):
        # Your logic here — return TradeSignal or None
        ...

    def compute_exit(self, entry_price, side, current_price, df):
        return {"stop_loss": ..., "take_profit": ..., "trailing_stop": None}
```

## Legacy Code

The original experimental scripts are preserved in `legacy/` for reference.

## Disclaimer

⚠️ This is for educational purposes. Trading involves risk. Use at your own risk.
