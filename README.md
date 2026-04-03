# 🚀 Upstox Trading Automation

Automated trading platform powered by the **Upstox API v2** — real-time market data streaming, pluggable strategy engines, risk-managed order execution, and a live web dashboard.

Built with **FastAPI**, **SQLAlchemy**, **APScheduler**, and **vanilla JavaScript**.



<img width="2554" height="1034" alt="image" src="https://github.com/user-attachments/assets/b2573336-f2c7-452c-9c8d-04fd7379e0e5" />



## Key Capabilities

- **Real-time WebSocket streaming** — Protobuf-decoded market ticks (ltpc, full, option_greeks modes)
- **Pluggable strategies** — SuperTrend Pro v6.3, ScalpPro v1.0, or bring your own
- **6-stage signal pipeline** — Risk Guard → OC Insight → ATM Resolver → Execution → Alerter → Broadcast
- **Risk management** — Daily loss caps, per-trade risk limits, position limits, forced square-off
- **Option chain analytics** — PCR, Max Pain, OI concentration, IV skew, live Greeks overlay
- **Paper trading** (default) — Safe simulation mode; flip one setting for live
- **Web dashboard** — Interactive charts (TradingView Lightweight Charts), trade log, strategy controls
- **Rate limiting** — Shared limiter enforcing 45/sec, 450/min, 1900/30min API call budgets
- **Static IP proxy** — Full proxy support for Upstox IP-whitelisting compliance

## Architecture

```
app/
├── main.py                    # FastAPI app, lifespan, WebSocket /ws
├── engine.py                  # AutomationEngine — cycle runner, strategy registry
├── engine_pipeline.py         # 6-processor signal pipeline
├── engine_routes.py           # Engine control endpoints
├── config.py                  # Pydantic settings (DB > .env > defaults)
├── settings_routes.py         # Settings REST API
├── rate_limiter.py            # Shared Upstox rate limiter (singleton)
├── network_proxy.py           # Centralized proxy configuration
├── auth/                      # OAuth2 + JWT token lifecycle
├── market_data/               # Candles, quotes, streaming, option chain
│   ├── service.py             # Data fetching with caching (55s TTL)
│   ├── streamer.py            # MarketDataStreamerV3 WebSocket handler
│   ├── option_analysis.py     # Pure-compute: PCR, Max Pain, IV Skew, OI
│   └── routes.py              # /market/* endpoints
├── orders/                    # Order execution + risk guards
├── strategies/                # Pluggable strategy engine
│   ├── base.py                # BaseStrategy abstract interface
│   ├── indicators.py          # ATR, ADX, SuperTrend, BB, ROC, VWAP, etc.
│   ├── supertrend_pro.py      # SuperTrend Pro v6.3 (Pine Script port)
│   └── scalp_pro.py           # ScalpPro v1.0 (EMA + VWAP + RSI scalping)
├── scheduler/                 # IST-aligned market-hours scheduling
├── database/                  # SQLAlchemy ORM (SQLite dev / PostgreSQL prod)
├── startup/                   # Modular startup pipeline
│   ├── database.py            # DB init, auto-seed, load settings
│   ├── engine.py              # Engine init, scheduler wiring
│   ├── streams.py             # Market/portfolio streamer wiring
│   └── background.py          # Heartbeat loop, LTP fallback ticks
├── notifications/             # Multi-channel alerts (email + extensible)
└── monitoring/                # Health, proxy status, watchlist, signals

frontend/
├── index.html                 # Dashboard layout
├── styles.css                 # Responsive styling
├── lightweight-charts.js      # TradingView charting library
└── js/
    ├── app.js                 # App state & orchestration
    ├── api.js                 # REST API client
    ├── chart.js               # Chart init & updates
    ├── ws.js                  # WebSocket client (market ticks)
    ├── ui.js                  # Toast notifications, formatting
    └── state.js               # Client-side state management
```

## Quick Start

### 1. Install Dependencies

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env with your Upstox API credentials
```

### 3. Run the Server

```bash
# Recommended — uses port 8210
bash start_terminal.sh

# Or manually
uvicorn app.main:app --reload --port 8210
```

### 4. Authenticate

1. Visit `http://localhost:8210/auth/login` — redirects to Upstox OAuth
2. After login, the callback stores your access token in the database
3. Check status: `GET /auth/status`

### 5. Dashboard & API Docs

- **Dashboard:** http://localhost:8210/dashboard
- **Swagger UI:** http://localhost:8210/docs

## Startup Sequence

When the app starts (via lifespan context manager):

1. **Configure Proxy** — Apply network proxy settings for SDK/HTTP clients
2. **Database Init** — Create tables, auto-seed config from `.env` on first run, load settings
3. **Engine Init** — Initialize `AutomationEngine`, wire scheduler (candle checks, market close square-off)
4. **Streams Init** — Start market data & portfolio WebSocket streamers
5. **Background Tasks** — Heartbeat loop (10s), LTP fallback when streamer is stale

## Signal Pipeline

Every trade signal flows through 6 processors in sequence:

```
Signal → RiskGuard → OC Insight → ATM Resolver → Execution → Alerter → Broadcast
           │             │             │              │           │          │
     Side/loss       OI/PCR/IV    Index→ATM        Place      Email/    WebSocket
      checks         analysis     option map       order     Telegram    clients
```

| # | Processor | Purpose |
|---|-----------|---------|
| 1 | **RiskGuardProcessor** | Trading side, daily loss limit, concurrent position checks |
| 2 | **OptionChainInsightProcessor** | Enriches signals with OI/PCR/IV/Max-Pain (30s cache, INDEX only) |
| 3 | **ATMResolverProcessor** | Maps index signals to ATM option contracts with liquidity scoring |
| 4 | **ExecutionProcessor** | Places orders via Upstox API or paper-trades |
| 5 | **AlerterProcessor** | Formats & sends notifications (email, Telegram) |
| 6 | **BroadcastProcessor** | Pushes updates to all connected WebSocket clients |

## Static IP / Proxy Setup

For Upstox IP-whitelisting compliance:

```bash
UPSTOX_PROXY_URL="http://user:pass@your-proxy:3128"
REQUIRE_UPSTOX_PROXY=True
ALGO_NAME="your-approved-algo-name"
```

Verify outbound IP:

```bash
./venv/bin/python scripts/verify_proxy_ip.py
```

## API Reference

### Auth

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/auth/login` | GET | Redirect to Upstox OAuth |
| `/auth/callback` | GET | OAuth2 callback, store token |
| `/auth/status` | GET | Token validity & user info |

### Market Data

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/market/ltp` | GET | Last traded price |
| `/market/quote` | GET | Full market quote |
| `/market/candles` | GET | Historical candles (cached 55s) |
| `/market/status` | GET | Market open/closed status |
| `/market/option-chain` | GET | Full option chain with live Greeks overlay |
| `/market/option-chain/analysis` | GET | PCR, Max Pain, OI, IV Skew analytics |
| `/market/strategy-overlay` | GET | Indicator arrays for chart overlay |
| `/market/instruments/search` | GET | Search instruments |
| `/ws` | WebSocket | Real-time tick stream |

### Orders

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/orders/place` | POST | Place new order |
| `/orders/book` | GET | Today's order book |
| `/orders/trades` | GET | Trade history |
| `/orders/trades/paper` | GET | Paper trade log |
| `/orders/positions` | GET | Open positions |
| `/orders/holdings` | GET | Equity holdings |
| `/orders/funds` | GET | Account funds/margin |
| `/orders/{order_id}` | DELETE | Cancel order |

### Engine Control

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/engine/initialize` | POST | Init engine |
| `/engine/load-strategy` | POST | Load strategy with params & instruments |
| `/engine/run-cycle` | POST | Trigger one evaluation cycle |
| `/engine/square-off` | POST | Force-exit all positions |
| `/engine/auto-mode` | POST | Toggle autonomous trading |
| `/engine/config` | POST | Update engine config (risk, capital, GTT) |
| `/engine/test-signal` | POST | Trigger manual test signal |
| `/engine/status` | GET | Engine status |
| `/engine/signals` | GET | Today's signals |
| `/engine/rejections` | GET | Signal rejections |

### Strategies

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/strategies/` | GET | List all strategies |
| `/strategies/schema` | GET | Parameter types/defaults |
| `/strategies/{id}/params` | PUT | Update parameters |
| `/strategies/{id}/toggle` | POST | Enable/disable |
| `/strategies/{id}/instruments` | PUT | Set instruments |
| `/strategies/{id}/dashboard` | POST | Real-time dashboard state |

### Settings & Monitoring

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/settings/` | GET | All settings (secrets masked) |
| `/settings/` | POST | Update settings (persisted to DB) |
| `/monitoring/network/proxy-status` | GET | Proxy diagnostics |
| `/monitoring/streamer/status` | GET | Streamer diagnostics |
| `/monitoring/watchlist` | GET/POST | Manage watchlist |
| `/monitoring/active-signals` | GET | Persisted strategy signals |

## Built-in Strategies

### SuperTrend Pro v6.3

Faithful 1:1 port from Pine Script (590 lines). Multi-confirm strategy with gating and soft scoring.

- **Hard Gates:** Dual SuperTrend agreement, consecutive bar confirmation, higher-timeframe filter
- **Soft Scoring:** ADX strength, volume surge, ATR percentile, ROC momentum, BB squeeze
- **Exit Modes:** ATR SL+TP (A), ATR SL + Profit% (B), ATR SL + Trailing (C)
- **Auto TF Adaptation:** Thresholds adjust to your selected timeframe automatically

### ScalpPro v1.0

High-speed scalping on 1-minute timeframe using Fast/Slow EMA crossover, anchored VWAP trend filter, and RSI momentum confirmation.

- **Entry:** EMA(9)/EMA(21) crossover + VWAP filter + RSI confirmation + ADX gate
- **Partial Profit Booking:** 3-tier scaled exits (TP1 40%, TP2 40%, remainder 20%) with breakeven SL shift
- **Trailing Stop:** ATR-based trailing with configurable multiplier
- **Swarm Entry:** 1–5 parallel lots per signal
- **Option Controls:** Liquidity filter, spread limits, quality-regime adaptive relaxation

### Adding a New Strategy

```python
from app.strategies.base import BaseStrategy
from app.orders.models import TradeSignal

class MyStrategy(BaseStrategy):
    @staticmethod
    def default_params():
        return {"period": 14, "threshold": 30, "enabled": False}

    def on_candle(self, df):
        # Your logic — return TradeSignal.BUY, SELL, or NONE
        ...

    def compute_exit(self, entry_price, side, current_price, df):
        return {"stop_loss": ..., "take_profit": ..., "trailing_stop": None}
```

Register in `app/engine.py`:
```python
STRATEGY_CLASSES = {"SuperTrendPro": SuperTrendPro, "ScalpPro": ScalpPro, "MyStrategy": MyStrategy}
```

## Configuration

Settings follow priority: **Database → .env → Python defaults**

### Key Settings

| Category | Settings |
|----------|----------|
| **API** | `API_KEY`, `API_SECRET`, `REDIRECT_URI`, `AUTH_CODE`, `ALGO_NAME`, `ORDER_API_VERSION` (v3.0) |
| **Trading** | `TRADING_CAPITAL` (100k), `PAPER_TRADING` (true), `TRADING_SIDE` (BOTH), `MAX_OPEN_TRADES` (3) |
| **Risk** | `MAX_RISK_PER_TRADE_PCT` (1%), `MAX_DAILY_LOSS_PCT` (3%), `MAX_CONCURRENT_POSITIONS` (5), `SQUARE_OFF_TIME` (15:15) |
| **Engine** | `ACTIVE_STRATEGY_CLASS`, `CANDLE_CHECK_SECONDS` (5), `FAST_EXECUTION_MODE`, `FAST_SKIP_OC_INSIGHT` |
| **Sandbox** | `USE_SANDBOX`, `SANDBOX_API_KEY`, `SANDBOX_API_SECRET`, `SANDBOX_ACCESS_TOKEN` |
| **GTT** | `GTT_PRODUCT_TYPE`, `GTT_TRAILING_SL`, `GTT_TRAILING_GAP_MODE/VALUE`, `GTT_MARKET_PROTECTION` |
| **Notifications** | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `SMTP_*`, `EMAIL_RECIPIENT` |
| **Network** | `UPSTOX_PROXY_URL`, `REQUIRE_UPSTOX_PROXY`, `REQUESTS_HTTP_PROXY` |

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Backend | FastAPI + Uvicorn |
| Database | SQLAlchemy 2.0 (SQLite dev / PostgreSQL prod) |
| Scheduler | APScheduler (AsyncIOScheduler, IST-aligned) |
| Market Data | Upstox SDK v2.21.0 + MarketDataStreamerV3 (Protobuf) |
| Frontend | Vanilla JS + TradingView Lightweight Charts |
| Auth | OAuth2 + JWT with auto-refresh |
| Rate Limiting | Async token-bucket (45/s, 450/m, 1900/30m) |

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/backtest_scalpro.py` | Backtest ScalpPro (API/offline sweep + walk-forward) |
| `scripts/check_api.py` | Verify API connectivity |
| `scripts/check_auth.py` | Test OAuth2 flow |
| `scripts/check_db.py` | Database integrity checks |
| `scripts/check_strategies.py` | Strategy validation |
| `scripts/verify_proxy_ip.py` | Verify proxy egress IP |
| `scripts/audit_config.py` | Audit configuration |

## Disclaimer

⚠️ This is for educational purposes. Trading involves significant risk of loss. Use at your own risk. Paper trading mode is enabled by default — switch to live trading only with full understanding of the risks involved.
