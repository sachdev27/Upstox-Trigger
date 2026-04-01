# Upstox-Trigger: Copilot Navigation Guide

> **Automated Trading Platform** integrated with Upstox API v2
> Built with FastAPI, SQLAlchemy, APScheduler, and vanilla JavaScript

---

## 📋 Table of Contents

1. [Project Overview](#project-overview)
2. [Quick Start & Startup Flow](#quick-start--startup-flow)
3. [Repository Structure](#repository-structure)
4. [Core Modules Deep Dive](#core-modules-deep-dive)
5. [Features & Capabilities](#features--capabilities)
6. [Built-in Strategies](#built-in-strategies)
7. [Data Flow Architecture](#data-flow-architecture)
8. [Configuration System](#configuration-system)
9. [Frontend Architecture](#frontend-architecture)
10. [API Quick Reference](#api-quick-reference)
11. [Key Architecture Patterns](#key-architecture-patterns)
12. [Common How-Tos & Recipes](#common-how-tos--recipes)
13. [External Dependencies & Rate Limits](#external-dependencies--rate-limits)

---

## Project Overview

### What is Upstox-Trigger?

**Upstox-Trigger** is an intelligent, automated trading platform that:

- 🔄 **Streams real-time market data** via WebSocket from Upstox API v2
- 🧠 **Evaluates trading signals** using pluggable strategy engines (SuperTrendPro, ScalpPro)
- 📊 **Executes trades automatically** with sophisticated risk management
- 💾 **Logs all activity** to a persistent database
- 📧 **Sends notifications** via email (and extensible to Telegram, Slack)
- 🎛️ **Provides a web dashboard** for real-time monitoring and configuration

### Tech Stack

| Component | Technology |
|-----------|-----------|
| **Backend Framework** | FastAPI + Uvicorn |
| **Async Job Scheduler** | APScheduler (AsyncIOScheduler) |
| **Database ORM** | SQLAlchemy (SQLite dev, PostgreSQL prod) |
| **Market Data SDK** | Upstox SDK v2.21.0 with WebSocket (MarketDataStreamerV3) |
| **WebSocket Protocol** | Protobuf-encoded messages |
| **Frontend** | Vanilla JavaScript, Lightweight-Charts library |
| **Authentication** | OAuth2 + JWT with auto-refresh |
| **Notifications** | SMTP (email), pluggable providers |

### Key Modes

- **Paper Trading** (default, safe): Simulates trades without real money
- **Sandbox Mode**: Test environment with separate Upstox credentials
- **Live Mode**: Real trading (requires actual API keys and auth code)

---

## Quick Start & Startup Flow

### Starting the Application

```bash
# Option 1: Using provided shell script (recommended)
bash start_terminal.sh
# → Kills any process on port 8210
# → Activates Python virtual environment
# → Runs: uvicorn app.main:app --reload --port 8210

# Option 2: Manual startup
source venv/bin/activate
uvicorn app.main:app --reload --port 8210
```

### Python Interpreter (Recommended for Scripts)

Use the repository interpreter explicitly when running scripts/tests from automation tools:

```bash
./venv/bin/python scripts/backtest_scalpro.py --mode api --max-instruments 30
./venv/bin/python -m pytest tests -q
```

If both `venv` and `.venv` exist, prefer `./venv/bin/python` for this repository to avoid package mismatch across environments.

**Server Ready:** http://localhost:8210

### Startup Sequence (Detailed)

When the app starts, here's what happens in order:

1. **Database Initialization** → Creates tables if they don't exist
2. **Auto-Seeding (First Run Only)**
   - `seed_settings()` → Writes `.env` values to `config_settings` table
   - `seed_watchlist_nifty50()` → Populates watchlist with Nifty 50 instruments
3. **Load Dynamic Settings from DB** → Overrides `.env` defaults
4. **Initialize AutomationEngine**
   - Instantiate `AuthService` (OAuth2 token manager)
   - Instantiate `MarketDataService` (data fetcher, caching layer)
   - Instantiate `OrderService` (order executor, risk guards)
5. **Start APScheduler** → Market-hours task hooks registered
6. **Launch MarketDataStreamer** → WebSocket feeds for indices (Nifty 50, BankNifty)
7. **Server Ready** → Accepts HTTP/WebSocket connections

### Key Startup Files

| File | Purpose |
|------|---------|
| [app/main.py](app/main.py) | FastAPI lifespan events (startup/shutdown) |
| [app/engine.py](app/engine.py) | Engine initialization, cycle runner, rate limiting |
| [app/engine_pipeline.py](app/engine_pipeline.py) | Signal processing pipeline (risk → execution → alerts) |
| [app/database/seed.py](app/database/seed.py) | Auto-seeding logic for fresh installations |
| [app/config.py](app/config.py) | Pydantic settings with DB-first priority |

---

## Repository Structure

### Folder Organization

```
Upstox-Trigger/
├── app/                          # Main application code
│   ├── __init__.py
│   ├── main.py                   # FastAPI app, startup/shutdown
│   ├── engine.py                 # Trading engine core
│   ├── engine_pipeline.py        # Signal pipeline (risk → execution → alerts)
│   ├── engine_routes.py          # Engine REST endpoints
│   ├── config.py                 # Pydantic settings (DB-first priority)
│   ├── settings_routes.py        # Settings REST API
│   │
│   ├── auth/                     # OAuth2 & token management
│   │   ├── service.py            # Token lifecycle, OAuth2 flow
│   │   └── routes.py             # /auth/login, /auth/callback
│   │
│   ├── market_data/              # Real-time data fetching & streaming
│   │   ├── service.py            # Candles, quotes, portfolio, caching
│   │   ├── streamer.py           # WebSocket handler (MarketDataStreamerV3)
│   │   └── routes.py             # /market/*, /ws endpoints
│   │
│   ├── orders/                   # Order execution & risk controls
│   │   ├── service.py            # Place/modify/cancel orders, risk checks
│   │   ├── models.py             # OrderRequest, TradeSignal, enums
│   │   └── routes.py             # /orders/* endpoints
│   │
│   ├── strategies/               # Strategy engine (pluggable)
│   │   ├── base.py               # BaseStrategy abstract interface
│   │   ├── indicators.py         # Technical indicators (Supertrend, ATK, ADX, etc.)
│   │   ├── supertrend_pro.py     # SuperTrendPro v6.3 (primary strategy)
│   │   ├── scalp_pro.py          # ScalpPro v1.0 (scalping framework)
│   │   └── routes.py             # /strategies/* endpoints
│   │
│   ├── scheduler/                # Market-hours task scheduling
│   │   └── service.py            # APScheduler async hooks (IST-aligned)
│   │
│   ├── database/                 # Persistence layer
│   │   ├── connection.py         # SQLAlchemy setup, session factory
│   │   ├── seed.py               # Auto-seed on first run
│   │   └── models.py             # ORM models (TradeLog, Strategy, Candle, etc.)
│   │
│   ├── notifications/            # Alert system (email + extensible)
│   │   ├── base.py               # NotificationProvider abstract class
│   │   ├── manager.py            # Unified notification entry point
│   │   ├── email.py              # SMTP email sender
│   │   └── routes.py             # Notification endpoints
│   │
│   └── monitoring/               # Health checks & diagnostics
│       └── routes.py             # /health, /status endpoints
│
├── frontend/                     # Web dashboard
│   ├── index.html                # Main UI layout
│   ├── styles.css                # CSS styling
│   ├── lightweight-charts.js     # Charting library (TradingView)
│   └── js/
│       ├── app.js                # Main app state & logic
│       ├── api.js                # REST API client wrapper
│       ├── chart.js              # Chart initialization & updates
│       ├── ws.js                 # WebSocket client (market ticks)
│       ├── ui.js                 # UI utilities (toasts, formatting, DOM)
│       └── modules/              # (Future modular components)
│
├── scripts/                      # Utility & testing scripts
│   ├── check_api.py              # Verify API connectivity
│   ├── check_auth.py             # Test OAuth2 flow
│   ├── check_db.py               # Database integrity checks
│   ├── check_strategies.py       # Strategy validation
│   ├── check_data_limits.py      # API rate limit monitoring
│   ├── check_frontend_parity.py  # Frontend≈Backend sync check
│   └── repopulate_watchlist_nifty500.py  # Bulk watchlist update
│
├── tests/                        # Test suite
│   └── test_smoke.py             # Smoke tests for critical paths
│
├── data/                         # Runtime data
│   └── candle_cache.json         # In-memory candle cache (55s TTL)
│
├── .github/
│   └── copilot-instructions.md   # This file!
│
├── .env                          # Environment variables (not in Git)
├── .env.example                  # Template for .env
├── requirements.txt              # Python dependencies
├── README.md                     # Project documentation
└── start_terminal.sh             # Shell script to start the app
```

---

## Core Modules Deep Dive

### 🔐 `app/auth/` — OAuth2 & Token Management

**Purpose:** Manage Upstox OAuth2 authentication and JWT token lifecycle.

**Key Concepts:**
- Tokens stored in **database** (not .env) for security
- Auto-refresh when near expiry
- Separate credentials for **Live** vs **Sandbox** modes

**Files:**
- **[auth/service.py](app/auth/service.py)**
  - `AuthService.exchange_code_for_token(code)` → Upstox OAuth → JWT in DB
  - `AuthService.auto_refresh_token()` → Refreshes if expiry < 5 min
  - `AuthService.get_valid_token()` → Returns fresh token (auto-refreshes if needed)
  - `AuthService.validate_token()` → Checks token validity

- **[auth/routes.py](app/auth/routes.py)**
  - `GET /auth/login` → Redirects to Upstox login page
  - `GET /auth/callback?code=...` → Handles OAuth callback
  - `GET /auth/status` → Returns token validity & user info

**Integration Points:**
- Called by [app/main.py](app/main.py) during startup to validate tokens
- Used by all other services when making API calls

---

### 📊 `app/market_data/` — Real-Time Data Fetching & Streaming

**Purpose:** Fetch historical candles, quotes, and stream real-time market ticks via WebSocket.

**Key Concepts:**
- **Candle Caching** (55s TTL): Reduces API calls significantly
- **Rate Limiting**: Enforced across all endpoints (50 req/sec, 500 req/min, 2000 req/30min)
- **WebSocket Streaming**: MarketDataStreamerV3 with protobuf decoding
- **Smart Subscription**: Subscribe/unsubscribe dynamically based on active strategies

**Files:**
- **[market_data/service.py](app/market_data/service.py)**
  - `MarketDataService.get_candles(instrument_key, interval)` → Cached or fresh
  - `MarketDataService.get_quotes(instrument_keys)` → Last traded prices
  - `MarketDataService.get_portfolio()` → Holdings + open positions
  - `MarketDataService.get_instruments()` → Instrument master list
  - Implements rate limiting via `UpstoxRateLimiter` in [app/engine.py](app/engine.py)

- **[market_data/streamer.py](app/market_data/streamer.py)**
  - `MarketDataStreamer` class manages WebSocket connection
  - Methods: `connect()`, `subscribe(keys)`, `unsubscribe(keys)`, `disconnect()`
  - Callbacks: `on_tick(data)`, `on_open()`, `on_close()`, `on_error(error)`
  - Protobuf auto-decoding to plain objects

- **[market_data/routes.py](app/market_data/routes.py)**
  - `GET /market/ltp?instrument_key=...` → Last traded price
  - `GET /market/candles?instrument_key=...&interval=...` → Historical data
  - `GET /market/positions` → Open positions
  - `GET /market/holdings` → Delivery holdings
  - `WebSocket /ws` → Connect for real-time ticks

**Cache Strategy:**
```python
# Candles cached for 55 seconds to batch requests
# When candle closes (at :59 of current minute), cache is invalidated
# Reduces API calls from ~1000/day to ~100/day
```

---

### 📝 `app/orders/` — Order Execution & Risk Controls

**Purpose:** Execute trades (place, modify, cancel orders) with risk guards.

**Key Concepts:**
- **Risk Guards**: Daily loss cap, position limits, per-trade risk limits
- **Paper Trading**: All orders on "paper" account (no real money) unless `PAPER_TRADING=False`
- **Order Logging**: Every execution logged to `TradeLog` table
- **Side Restrictions**: Can be LONG_ONLY, SHORT_ONLY, or BOTH

**Files:**
- **[orders/service.py](app/orders/service.py)**
  - `OrderService.pre_trade_checks(signal)` → Applies risk guards
    - Check daily P&L vs `MAX_DAILY_LOSS_PCT`
    - Check open positions vs `MAX_CONCURRENT_POSITIONS`
    - Check per-trade risk vs `MAX_RISK_PER_TRADE_PCT`
    - Check trading side (LONG_ONLY/SHORT_ONLY/BOTH)
  - `OrderService.place_order(order_request)` → Execute via Upstox API
  - `OrderService.modify_order(order_id, new_params)` → Update order
  - `OrderService.cancel_order(order_id)` → Cancel order

- **[orders/models.py](app/orders/models.py)**
  - `OrderRequest` dataclass: symbol, quantity, side, order_type, price, etc.
  - `TradeSignal` enum: BUY, SELL, EXIT, NONE
  - `OrderType` enum: MARKET, LIMIT, STOP, STOP_LIMIT
  - `TradingSide` enum: LONG_ONLY, SHORT_ONLY, BOTH

- **[orders/routes.py](app/orders/routes.py)**
  - `POST /orders/place` → Place order with signal
  - `GET /orders/book` → Today's order book
  - `GET /orders/positions` → Current open positions
  - `PUT /orders/{order_id}` → Modify order
  - `DELETE /orders/{order_id}` → Cancel order

**Risk Guard Example:**
```python
# Daily Loss Guard
current_pnl = sum(all_trades_today)
if abs(current_pnl) > portfolio_value * MAX_DAILY_LOSS_PCT:
    reject_signal("Daily loss limit exceeded")

# Concurrent Positions Guard
if len(open_positions) >= MAX_CONCURRENT_POSITIONS:
    reject_signal("Max concurrent positions reached")
```

---

### 🧠 `app/strategies/` — Pluggable Strategy Engine

**Purpose:** Evaluate market conditions, generate buy/sell signals using technical analysis.

**Key Concepts:**
- **Pluggable Interface**: All strategies inherit from `BaseStrategy`
- **On-Candle Evaluation**: Called each minute (or custom interval)
- **Exit Computation**: Separate logic for stop-loss, take-profit, trailing stops
- **Parameterizable**: Each strategy has `default_params()` configurable at runtime

**Files:**
- **[strategies/base.py](app/strategies/base.py)**
  - Abstract `BaseStrategy` class
  - Methods:
    - `on_candle(df: DataFrame) -> TradeSignal` — Evaluate latest candle
    - `compute_exit(entry_price, side, current_price, df) -> {stop_loss, take_profit, trailing_stop}` — Exit levels
    - `default_params() -> dict` — Configurable parameters
  - Subclasses must implement all three

- **[strategies/indicators.py](app/strategies/indicators.py)**
  - Technical indicator library: SuperTrend, ATR, ADX, Bollinger Bands, ROC, Volume indicators
  - Used by all strategies for signal generation

- **[strategies/supertrend_pro.py](app/strategies/supertrend_pro.py)** ⭐ Primary Strategy
  - See [Built-in Strategies](#built-in-strategies) section below

- **[strategies/scalp_pro.py](app/strategies/scalp_pro.py)**
  - See [Built-in Strategies](#built-in-strategies) section below

- **[strategies/routes.py](app/strategies/routes.py)**
  - `GET /strategies/` → List all available strategies
  - `GET /strategies/{strategy_name}/params` → Get current parameters
  - `PUT /strategies/{strategy_name}/params` → Update parameters (persisted to DB)
  - `POST /strategies/{strategy_name}/toggle` → Enable/disable strategy

**Creating a New Strategy:**
```python
# 1. Add new_strategy.py in app/strategies/
# 2. Import BaseStrategy
# 3. Implement class NewStrategy(BaseStrategy):

class MyStrategy(BaseStrategy):
    def on_candle(self, df):
        """df = latest 100+ candles with OHLCV"""
        latest = df.iloc[-1]
        prev = df.iloc[-2]

        if latest['close'] > prev['high'] and latest['volume'] > avg_vol:
            return TradeSignal.BUY
        return TradeSignal.NONE

    def compute_exit(self, entry_price, side, current_price, df):
        """Return risk management levels"""
        atr = compute_atr(df, 14)
        return {
            'stop_loss': entry_price - (2 * atr),
            'take_profit': entry_price + (3 * atr),
            'trailing_stop': None
        }

    @staticmethod
    def default_params():
        return {
            'atr_period': 14,
            'volume_period': 20,
            'enabled': False
        }

# 4. Register in app/engine.py:
#    STRATEGY_MAP = {..., 'MyStrategy': MyStrategy}
# 5. Update ACTIVE_STRATEGY_CLASS in .env or DB settings
```

---

### ⏰ `app/scheduler/` — Market-Hours IST-Aligned Scheduling

**Purpose:** Schedule trading activities aligned to Indian Standard Time (IST) market hours.

**Market Hours:** 9:15 AM – 3:30 PM IST (Monday–Friday)

**Files:**
- **[scheduler/service.py](app/scheduler/service.py)**
  - Uses `AsyncIOScheduler` from APScheduler
  - All times in IST; auto-handles daylight saving

**Scheduled Hooks:**

| Time | Event | Purpose |
|------|-------|---------|
| 8:45 AM IST | `pre_market_hook()` | Validate OAuth token, download fresh instrument list |
| 9:15 AM IST | `market_open_hook()` | Start WebSocket feeds for indices, activate strategies |
| Every minute (9:15-3:30) | `candle_check_hook()` | Run `engine.run_cycle()` → evaluate strategies → execute trades |
| 3:30 PM IST | `market_close_hook()` | Square-off all intraday positions, close WebSocket |
| 3:45 PM IST | `post_market_hook()` | Generate daily report, email summary, archive logs |
| Nightly | `maintenance_hook()` | Clean up old cache entries, database vacuum |

**Key Code:**
```python
# In app/main.py lifespan event:
scheduler = AsyncIOScheduler(timezone='Asia/Kolkata')
scheduler.add_job(pre_market_hook, 'cron', hour=8, minute=45, timezone='Asia/Kolkata')
scheduler.add_job(candle_check_hook, 'cron', minute='*/1', start_date='09:15', end_date='15:30', timezone='Asia/Kolkata')
scheduler.start()
```

---

### 💾 `app/database/` — Persistence Layer & ORM

**Purpose:** Store all application state (trades, settings, candles, strategy configs).

**Database:** SQLite (dev), PostgreSQL (prod)

**Files:**
- **[database/connection.py](app/database/connection.py)**
  - SQLAlchemy engine & session factory setup
  - Provides `get_db()` dependency for FastAPI routes

- **[database/seed.py](app/database/seed.py)**
  - `seed_settings()` → Load .env into `ConfigSetting` table on first run
  - `seed_watchlist_nifty50()` → Populate Nifty 50 instruments into `Watchlist` table

**Core Models:**
- **`TradeLog`** — Every executed trade
  - Fields: symbol, entry_price, exit_price, quantity, pnl, trade_date, strategy, status
  - Used for: trade history, P&L tracking, backtesting

- **`StrategyState`** — Saved strategy configuration
  - Fields: strategy_name, enabled, instruments, parameters (JSON), last_signal_time
  - Used for: persisting strategy configs across restarts

- **`CandleCache`** — Cached historical candles (55s TTL)
  - Fields: instrument_key, interval, timestamp, ohlcv_data (JSON), cache_time
  - Used for: fast candle lookups without hitting Upstox API

- **`ConfigSetting`** — Application settings
  - Fields: key, value, updated_at
  - Priority: DB value > .env > Python default
  - Examples: API_KEY, TRADING_CAPITAL, MAX_DAILY_LOSS_PCT, ACTIVE_STRATEGY_CLASS

- **`Watchlist`** — Tracked instruments
  - Fields: symbol, instrument_key, instrument_type (EQUITY/OPTION/INDEX), enabled

- **`MarketTick`** — Real-time quotes snapshot
  - Fields: instrument_key, ltp, volume, open_interest, timestamp

**Accessing Database:**
```python
# In FastAPI routes:
from app.database.connection import get_db

@app.get("/data")
async def get_data(db: Session = Depends(get_db)):
    trades = db.query(TradeLog).all()
    return trades
```

---

### 📧 `app/notifications/` — Multi-Channel Alert System

**Purpose:** Send alerts (trades, errors, reports) via multiple channels (Email, extensible to Telegram, Slack).

**Files:**
- **[notifications/base.py](app/notifications/base.py)**
  - Abstract `NotificationProvider` class
  - Methods: `send(message, alert_type)`, `validate_config()`

- **[notifications/manager.py](app/notifications/manager.py)**
  - `NotificationManager` — Unified entry point
  - `register_provider(provider_name, provider_instance)` — Add new channel
  - `send_alert(message, alert_type, channels=[])` — Broadcast to registered channels
  - Active channels configured via `NOTIFICATION_CHANNELS` setting

- **[notifications/email.py](app/notifications/email.py)**
  - `EmailProvider` implementation
  - Uses SMTP (Gmail, custom server)
  - Config: `SMTP_SERVER`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_RECIPIENT`
  - Sends: Trade alerts, daily reports, error notifications

- **[notifications/routes.py](app/notifications/routes.py)**
  - `POST /notifications/test` → Send test alert
  - `GET /notifications/config` → Current provider config

**Adding a New Provider:**
```python
# 1. Create app/notifications/telegram.py
from app.notifications.base import NotificationProvider

class TelegramProvider(NotificationProvider):
    def __init__(self, bot_token, chat_id):
        self.bot_token = bot_token
        self.chat_id = chat_id

    async def send(self, message, alert_type='INFO'):
        # Use requests to call Telegram Bot API
        await self._send_to_telegram(self.chat_id, message)

    def validate_config(self):
        return self.bot_token and self.chat_id

# 2. In app/main.py startup:
telegram = TelegramProvider(
    bot_token=settings.TELEGRAM_BOT_TOKEN,
    chat_id=settings.TELEGRAM_CHAT_ID
)
notification_manager.register_provider('telegram', telegram)
```

---

## Features & Capabilities

### ✅ Trading Features

| Feature | Status | Details |
|---------|--------|---------|
| **Real-Time Market Data** | ✅ | WebSocket feeds (Nifty 50, BankNifty, custom) |
| **Automated Signal Generation** | ✅ | SuperTrendPro, ScalpPro, custom strategies |
| **Order Execution** | ✅ | Place, modify, cancel via Upstox API |
| **Signal Selectivity Engine** | ✅ | Confidence scoring + top-N filtering per cycle |
| **Paper Trading** | ✅ | Safe simulation mode (default) |
| **Live Trading** | ✅ | Real trades (PAPER_TRADING=False) |
| **Swarm Entry Execution** | ✅ | Multiple parallel lots per signal via `swarm_count` |
| **Partial Profit Booking** | ✅ | TP1/TP2/TP3 staged exits with breakeven SL shift |
| **Position Tracking** | ✅ | Open positions, P&L calculation |
| **Trade Logging** | ✅ | All trades stored in database |
| **Strategy Backtesting** | ✅ | `scripts/backtest_scalpro.py` supports API/offline sweep + walk-forward |

### ✅ Risk Management

| Guard | Status | Details |
|-------|--------|---------|
| **Daily Loss Limit** | ✅ | Stops trading if loss > MAX_DAILY_LOSS_PCT |
| **Per-Trade Risk Limit** | ✅ | Each trade risk < MAX_RISK_PER_TRADE_PCT |
| **Max Concurrent Positions** | ✅ | Limit open positions via MAX_CONCURRENT_POSITIONS |
| **Trading Side Restriction** | ✅ | LONG_ONLY, SHORT_ONLY, or BOTH |
| **Forced Square-Off Time** | ✅ | Auto square-off at SQUARE_OFF_TIME (3:15 PM IST) |
| **Candle Timeframe Limit** | ✅ | Max candle interval per strategy |

### ✅ Notifications

| Channel | Status | Details |
|---------|--------|---------|
| **Email** | ✅ | SMTP-based (Gmail, custom) |
| **Telegram** | ⏳ | Framework ready, provider pending |
| **Slack** | ⏳ | Framework ready, provider pending |

### ✅ Configuration

| Feature | Status | Details |
|---------|--------|---------|
| **Dynamic Settings** | ✅ | Persisted to database (DB > .env > defaults) |
| **Sandbox Mode** | ✅ | Test environment with separate credentials |
| **Live Mode** | ✅ | Production trading |
| **Auto-Seeding** | ✅ | First-run initialization from .env |
| **REST API** | ✅ | GET/PUT endpoints to read/modify settings |

---

## Built-in Strategies

### SuperTrendPro v6.3 ⭐ (Primary)

**File:** [app/strategies/supertrend_pro.py](app/strategies/supertrend_pro.py)

**Purpose:** Sophisticated multi-confirm strategy with gating and soft scoring.

**Signal Pipeline (6 Steps):**

```
┌─────────────────────────────────────────┐
│ 1. PRIMARY SUPERTREND                   │
│    atr_period=10, multiplier=3.0        │
│    → Direct trend signal (BUY/SELL)     │
└────────────────┬────────────────────────┘
                 ↓ (PASS) → Continue to Gate H1
┌─────────────────────────────────────────┐
│ 2. HARD GATE H1 — Dual SuperTrend       │
│    Slow SuperTrend (period=20, mult=5.0)│
│    Must AGREE with primary              │
│    → Confirms primary trend direction   │
└────────────────┬────────────────────────┘
                 ↓ (PASS) → Continue to Gate H2
┌─────────────────────────────────────────┐
│ 3. HARD GATE H2 — Consecutive Bars      │
│    Require 2+ consecutive bars trending │
│    same direction as primary signal     │
│    → Avoids whipsaws                    │
└────────────────┬────────────────────────┘
                 ↓ (PASS) → Continue to Gate H3
┌─────────────────────────────────────────┐
│ 4. HARD GATE H3 — HTF (Higher TF) Filter│
│    Higher timeframe trend agreement     │
│    (e.g., if trading 1m, check 5m)      │
│    → Aligns with larger trend           │
└────────────────┬────────────────────────┘
                 ↓ (PASS) → Continue to Soft Scoring
┌─────────────────────────────────────────┐
│ 5. SOFT SCORING (Quality Signal)        │
│    • ADX strength (trend strength)      │
│    • Volume surge (breakout volume)     │
│    • ATR percentile (volatility)        │
│    • Rate of Change (momentum)          │
│    • Bollinger Band squeeze (breakout)  │
│    → Assigns confidence score 0-100     │
└────────────────┬────────────────────────┘
                 ↓ Score > score_threshold
┌─────────────────────────────────────────┐
│ 6. FINAL SIGNAL                         │
│    BUY / SELL (with confidence score)   │
└─────────────────────────────────────────┘
```

**Configurable Parameters:**

```python
{
    'enabled': True,
    'atr_period': 10,                      # Primary SuperTrend ATR period
    'atr_multiplier': 3.0,                 # Primary SuperTrend ATR multiplier
    'slow_atr_period': 20,                 # Gate H1 slow SuperTrend ATR period
    'slow_atr_multiplier': 5.0,            # Gate H1 slow SuperTrend multiplier
    'consecutive_bars_confirm': 2,         # Gate H2: bars required
    'htf_timeframe': '5min',               # Gate H3: higher timeframe
    'adx_period': 14,                      # Soft: ADX strength check
    'adx_threshold': 25,                   # Soft: ADX minimumstrength
    'volume_ma_period': 20,                # Soft: volume moving average
    'volume_multiplier': 1.5,              # Soft: volume threshold multiplier
    'roc_period': 12,                      # Soft: momentum check period
    'bb_period': 20,                       # Soft: Bollinger Band period
    'bb_stddev': 2.0,                      # Soft: Bollinger Band std dev
    'score_threshold': 50,                 # Confidence score minimum (0-100)
    'trailing_stop': False,                # Exit mode: use trailing stop?
    'trailing_percent': 2.0,               # Exit: trailing stop percent
}
```

**Exit Modes:**
- **Mode A:** ATR Stop-Loss + ATR Take-Profit
- **Mode B:** ATR Stop-Loss + Profit% Take-Profit
- **Mode C:** ATR Stop-Loss + Trailing Stop

**Adaptive Thresholds:**
SuperTrendPro auto-adjusts thresholds based on selected timeframe (1m, 5m, 15m, 1H, 4H, 1D). Tighter thresholds for faster timeframes, relaxed for slower.

---

### ScalpPro v1.0

**File:** [app/strategies/scalp_pro.py](app/strategies/scalp_pro.py)

**Purpose:** High-frequency scalping on 1-minute timeframe.

**Status:** Framework implemented, signal logic pending.

**Design:** Intended for quick entry/exit within 1-5 minute windows with tight stops.

**Configurable Parameters:**
```python
{
    'enabled': False,
    'min_profit_pts': 5,                   # Min profit in points to take
    'max_loss_pts': 10,                    # Max loss in points to stop
    'ma_period': 9,                        # Moving average period
    'rsi_period': 14,                      # RSI period
    'rsi_overbought': 70,                  # RSI overbought level
    'rsi_oversold': 30,                    # RSI oversold level
}
```

---

## Data Flow Architecture

### Signal Processing Pipeline

```
┌──────────────────────────────────────────────────────────────┐
│ 1. MARKET DATA STREAM (WebSocket)                            │
│    Every tick: Nifty 50, BankNifty, watched instruments     │
│    Ticks stored in memory, aggregated into candles           │
└─────────────────────────┬──────────────────────────────────┘
                           ↓
┌──────────────────────────────────────────────────────────────┐
│ 2. SCHEDULER (Every Minute: 9:15 AM - 3:30 PM IST)          │
│    Trigger: run_cycle()                                      │
│    Get latest candle (OHLCV) for all watched instruments     │
└─────────────────────────┬──────────────────────────────────┘
                           ↓
┌──────────────────────────────────────────────────────────────┐
│ 3. STRATEGY EVALUATION                                       │
│    Call: Strategy.on_candle(df) → TradeSignal               │
│    Example: SuperTrendPro processes signal pipeline          │
│    Output: BUY / SELL / EXIT / NONE                         │
└─────────────────────────┬──────────────────────────────────┘
                           ↓
┌──────────────────────────────────────────────────────────────┐
│ 4. ENGINE PIPELINE [app/engine_pipeline.py]                 │
│                                                              │
│    Processor 1: RiskGuardProcessor                          │
│    ├─ Check daily loss vs MAX_DAILY_LOSS_PCT               │
│    ├─ Check max concurrent positions                        │
│    ├─ Check per-trade risk vs MAX_RISK_PER_TRADE_PCT       │
│    └─ Check trading side (LONG_ONLY/SHORT_ONLY/BOTH)       │
│       ↓ (Pass all) → Continue                              │
│                                                              │
│    Processor 2: ATMResolverProcessor (Optional)            │
│    ├─ If signal is for Index → map to ATM call/put option │
│    └─ Resolve strike & expiry                             │
│       ↓ → Continue                                         │
│                                                              │
│    Processor 3: ExecutionProcessor                          │
│    ├─ Call: OrderService.place_order(signal)              │
│    ├─ Order placed to Upstox API (or paper trade)         │
│    └─ Log execution details                               │
│       ↓ → Continue                                         │
│                                                              │
│    Processor 4: AlerterProcessor                            │
│    ├─ Format message with trade details                    │
│    ├─ Send via NotificationManager                         │
│    └─ Email + other channels                              │
│       ↓ → Continue                                         │
│                                                              │
│    Processor 5: BroadcastProcessor                          │
│    ├─ Create broadcast message (JSON)                      │
│    └─ Send to all connected WebSocket clients (frontend)   │
│       ↓ → DONE                                            │
│                                                              │
└──────────────────────────────────────────────────────────────┘
                           ↓
┌──────────────────────────────────────────────────────────────┐
│ 5. DATABASE PERSISTENCE [app/database/]                     │
│    ├─ TradeLog entry (order ID, entry price, side, etc.)   │
│    ├─ MarketTick update (latest quotes)                     │
│    └─ StrategyState update (last signal time)               │
└──────────────────────────────────────────────────────────────┘
                           ↓
┌──────────────────────────────────────────────────────────────┐
│ 6. FRONTEND UI UPDATE [frontend/js/ws.js]                   │
│    ├─ Receive broadcast via WebSocket                       │
│    ├─ Update trade log table                                │
│    ├─ Refresh quotes & position display                     │
│    └─ Show toast notification                              │
└──────────────────────────────────────────────────────────────┘
```

### Data Models Relationships

```
ConfigSetting (app settings)
    ↓
    ├─→ AuthService (uses API keys from settings)
    │       ├─→ gets AccessToken → stored in DB
    │       └─→ MarketDataService + OrderService
    │
    ├─→ MarketDataService
    │       ├─→ fetches candles → CandleCache
    │       ├─→ streams ticks → MarketTick
    │       └─→ queried by Strategy.on_candle()
    │
    ├─→ StrategyState
    │       ├─→ stores strategy params
    │       └─→ loaded by engine to init strategy instance
    │
    └─→ OrderService
            ├─→ pre_trade_checks → Reads open positions
            ├─→ place_order → logs TradeLog
            └─→ modifies/cancels via Upstox API
```

---

## Configuration System

### Settings Priority

1. **Database (Highest)** — `ConfigSetting` table
   - Persisted values set via REST API or UI
   - Survives restarts

2. **Environment File** — `.env` file (local)
   - Default values on fresh install
   - Auto-seeded to DB on first run
   - Not recommended for runtime changes

3. **Python Defaults (Lowest)** — Hardcoded in [app/config.py](app/config.py)
   - Fallback if DB & .env unavailable
   - Ensures app always has valid values

**Resolution Logic:**
```python
# In app/config.py (Pydantic Settings)
value = config_setting_table.get(key) or os.getenv(key) or DEFAULT
```

### Key Settings

**Upstox API:**
```
API_KEY                 # Live API key
API_SECRET              # Live API secret
REDIRECT_URI            # OAuth2 redirect URL
ACCESS_TOKEN            # JWT token (in DB)
AUTH_CODE               # One-time OAuth code

SANDBOX_API_KEY         # Sandbox API key
SANDBOX_API_SECRET      # Sandbox API secret
SANDBOX_ACCESS_TOKEN    # Sandbox JWT
USE_SANDBOX             # bool: toggle live ↔ sandbox
```

**Trading:**
```
TRADING_CAPITAL         # float: account balance (e.g., 100000.0)
PAPER_TRADING           # bool: simulate trades when True (SAFE DEFAULT)
TRADING_SIDE            # str: "LONG_ONLY" | "SHORT_ONLY" | "BOTH"
ACTIVE_STRATEGY_CLASS   # str: "SuperTrendPro" | "ScalpPro" | custom
ACTIVE_STRATEGY_NAME    # str: display name (e.g., "SuperTrend Pro v6.3")
```

**Risk Management:**
```
MAX_RISK_PER_TRADE_PCT          # float: max risk per trade (% of capital)
MAX_DAILY_LOSS_PCT              # float: max daily loss (% of capital)
MAX_CONCURRENT_POSITIONS        # int: max open positions
SQUARE_OFF_TIME                 # str: forced square-off time (HH:MM IST)
CANDLE_INTERVAL                 # str: "1minute" | "5minute" | "15minute" | etc.
```

**Notifications:**
```
NOTIFICATION_CHANNELS           # str: comma-separated ("EMAIL", "TELEGRAM")
SMTP_SERVER                     # str: SMTP host
SMTP_PORT                       # int: SMTP port
SMTP_USER                       # str: email sender username
SMTP_PASSWORD                   # str: email sender password
EMAIL_RECIPIENT                 # str: recipient email address
TELEGRAM_BOT_TOKEN              # str: Telegram bot token
TELEGRAM_CHAT_ID                # str: Telegram chat ID
```

### REST API for Settings

**GET /settings/**
```bash
curl http://localhost:8210/settings/
# Response: JSON with all settings (secrets masked)
# {
#   "TRADING_CAPITAL": 100000.0,
#   "PAPER_TRADING": true,
#   "API_KEY": "***hidden***",
#   ...
# }
```

**PUT /settings/**
```bash
curl -X PUT http://localhost:8210/settings/ \
  -H "Content-Type: application/json" \
  -d '{
    "MAX_DAILY_LOSS_PCT": 5.0,
    "MAX_CONCURRENT_POSITIONS": 10
  }'
# Response: Updated settings (persisted to DB)
```

---

## Frontend Architecture

### File Structure

```
frontend/
├── index.html                  # Main HTML layout
│   ├── <header>: Logo, market status, account info
│   ├── <main>:
│   │   ├── Chart section (TradingView Lightweight Charts)
│   │   ├── Trade log (scrollable table)
│   │   └── Control panel (strategy settings, risk controls)
│   └── <footer>: Status indicators
│
├── styles.css                  # Responsive CSS styling
│
├── lightweight-charts.js       # TradingView charting library (embedded)
│
└── js/
    ├── app.js                  # Main app state & orchestration
    │   ├── initApp() → Setup UI, fetch initial data
    │   ├── handleStrategyChange() → Update strategy via API
    │   └── updateUI(state) → Refresh DOM based on state
    │
    ├── api.js                  # REST API client wrapper
    │   ├── api.auth.login()
    │   ├── api.market.getCandles()
    │   ├── api.orders.place()
    │   └── api.settings.update()
    │
    ├── chart.js                # Chart initialization & updates
    │   ├── initChart(containerId) → Create chart instance
    │   ├── addCandle(ohlc) → Add candle to chart
    │   └── setTheme(light|dark) → Theme switching
    │
    ├── ws.js                   # WebSocket client
    │   ├── connectWS() → Connect to /ws endpoint
    │   ├── subscribe(symbols) → Subscribe to ticks
    │   ├── unsubscribe(symbols)
    │   └── onMessageReceived(message) → Process server broadcasts
    │
    ├── ui.js                   # Utility functions
    │   ├── showToast(message, type) → Toast notification
    │   ├── formatCurrency(value)
    │   ├── formatPercent(value)
    │   └── formatTime(timestamp)
    │
    └── modules/                # (Future: modular components)
        ├── TradeLogPanel.js
        ├── SettingsPanel.js
        └── ChartPanel.js
```

### Key Features

**Real-Time Quotes:**
- WebSocket connection to `/ws` endpoint
- Subscribe to instrument keys dynamically
- Update last-traded-price (LTP) display
- Show volume, open interest

**Interactive Charts:**
- TradingView Lightweight Charts library
- Multiple timeframes: 1m, 5m, 15m, 1h, 4h, 1d
- Add indicators overlay (SuperTrend, Bollinger Bands)
- Click to place orders from chart

**Strategy Controls:**
- Enable/disable strategies via toggles
- Adjust parameters (sliders, inputs)
- Submit to `/strategies/{id}/params` endpoint
- See updated settings reflected immediately

**Settings Panel:**
- Read/update via `/settings/` API
- Risk limits (MAX_DAILY_LOSS_PCT, MAX_CONCURRENT_POSITIONS)
- Notification config (email, Telegram)
- Sandbox ↔ Live mode toggle (requires password)

**Trade Log Viewer:**
- Scrollable table of executed trades
- Columns: Symbol, Entry Price, Exit Price, Quantity, P&L, Time, Strategy
- Real-time updates via WebSocket broadcast
- Filter by strategy, date range

**Client-Side Caching:**
- IndexedDB for historical candles (survives page refresh)
- LocalStorage for UI state (theme, selected symbol, etc.)
- Reduces API calls on reconnect

**Market Status Indicator:**
- Displays: "Market Open" / "Closed", IST time, next event
- Color-coded: Green (open), Red (closed), Yellow (pre-market)

**Portfolio Status Bar:**
- Account balance
- Daily P&L (green if +, red if -)
- Open positions count
- Max daily loss indicator (progress bar)

---

## API Quick Reference

### Authentication

| Endpoint | Method | Purpose | Response |
|----------|--------|---------|----------|
| `/auth/login` | GET | Redirect to Upstox OAuth page | Redirect to Upstox login |
| `/auth/callback?code=...` | GET | Handle OAuth callback, store token | Redirect to dashboard |
| `/auth/status` | GET | Check token validity & user info | `{valid: bool, user_info: {...}}` |

### Market Data

| Endpoint | Method | Purpose | Example |
|----------|--------|---------|---------|
| `/market/ltp?instrument_key=...` | GET | Last traded price | `{ltp: 50123.45, volume: 5000}` |
| `/market/candles?instrument_key=...&interval=...` | GET | Historical candles (cached 55s) | `{candles: [{open, high, low, close, volume}, ...]}` |
| `/market/positions` | GET | Open positions | `{positions: [{symbol, quantity, entry_price, ...}]}` |
| `/market/holdings` | GET | Delivery holdings | `{holdings: [{symbol, quantity, average_price, ...}]}` |
| `/ws` | WebSocket | Real-time tick stream | Continuous `{symbol, ltp, volume, ...}` JSON messages |

### Strategies

| Endpoint | Method | Purpose | Example |
|----------|--------|---------|---------|
| `/strategies/` | GET | List all available strategies | `{strategies: ["SuperTrendPro", "ScalpPro"]}` |
| `/strategies/{name}` | GET | Get strategy metadata | `{name: "SuperTrendPro", enabled: true, version: "6.3"}` |
| `/strategies/{name}/params` | GET | Get current parameters | `{atr_period: 10, atr_multiplier: 3.0, ...}` |
| `/strategies/{name}/params` | PUT | Update parameters | `{atr_period: 12, atr_multiplier: 3.5}` |
| `/strategies/{name}/toggle` | POST | Enable/disable strategy | `{enabled: false}` |

### Orders

| Endpoint | Method | Purpose | Example |
|----------|--------|---------|---------|
| `/orders/place` | POST | Place new order | Body: `{symbol, quantity, side, order_type, ...}` |
| `/orders/book` | GET | Today's order book | `{orders: [{symbol, order_id, status, ...}]}` |
| `/orders/positions` | GET | Current positions | `{positions: [{symbol, quantity, ...}]}` |
| `/orders/{order_id}` | PUT | Modify order | Body: `{price, quantity, ...}` |
| `/orders/{order_id}` | DELETE | Cancel order | Response: `{status: "cancelled"}` |

### Engine Control

| Endpoint | Method | Purpose | Example |
|----------|--------|---------|---------|
| `/engine/initialize` | POST | Init engine (called at startup) | `{status: "initialized"}` |
| `/engine/load-strategy` | POST | Load a strategy | Body: `{strategy_name: "SuperTrendPro"}` |
| `/engine/run-cycle` | POST | Run one evaluation cycle (debug) | `{signals: [signal1, signal2, ...]}` |

### Settings

| Endpoint | Method | Purpose | Example |
|----------|--------|---------|---------|
| `/settings/` | GET | Read all settings (secrets masked) | `{TRADING_CAPITAL: 100000.0, API_KEY: "***", ...}` |
| `/settings/` | PUT | Update multiple settings (persisted to DB) | Body: `{MAX_DAILY_LOSS_PCT: 5.0}` |

### Health & Monitoring

| Endpoint | Method | Purpose | Example |
|----------|--------|---------|---------|
| `/health` | GET | App health check | `{status: "ok", uptime_seconds: 3600}` |
| `/monitoring/status` | GET | Detailed system status | `{market_status: "open", last_cycle_time: "2024-01-15 10:30:00 IST", ...}` |

---

## Key Architecture Patterns

### 1. **Pluggable Strategies**

All trading strategies follow the same interface defined in [app/strategies/base.py](app/strategies/base.py):

```python
class BaseStrategy:
    def on_candle(self, df: pd.DataFrame) -> TradeSignal:
        """Evaluate the latest candle, return signal"""
        pass

    def compute_exit(self, entry_price, side, current_price, df) -> dict:
        """Calculate stop-loss, take-profit, trailing stop"""
        pass

    @staticmethod
    def default_params() -> dict:
        """Return configurable parameters with defaults"""
        pass
```

**Benefits:**
- Easy to add new strategies
- All strategies follow same evaluation cycle
- Parameters configurable at runtime without code changes
- Clean separation of concern

---

### 2. **Database-First Configuration**

Settings follow a priority order: **DB > .env > Defaults**

```python
# In app/config.py
class Settings(BaseSettings):
    TRADING_CAPITAL: float = Field(
        default=100000.0,
        description="Account capital"
    )

    # Resolution:
    # 1. Check ConfigSetting table (DB)
    # 2. Check .env file
    # 3. Use Python default
```

**Benefits:**
- Runtime config changes persist across restarts
- No need to modify .env for trading parameters
- Settings applied immediately via REST API
- Secrets stored securely in DB, not .env

---

### 3. **Service-Oriented Architecture**

Each business domain has a dedicated service:

```
AuthService
├─ handle OAuth2
├─ token refresh
└─ provide working token

MarketDataService
├─ fetch candles (cached)
├─ get quotes
└─ provide data to strategies

OrderService
├─ pre-trade risk checks
├─ place/modify/cancel orders
└─ log trades

NotificationManager
├─ manage multiple providers
└─ broadcast alerts
```

**Benefits:**
- Clear separation of concerns
- Easy to test (mock services)
- Reusable across routes
- Single responsibility principle

**Dependency Injection:**
```python
# In routes:
@app.get("/data")
async def get_data(
    market_service: MarketDataService = Depends(get_market_service)
):
    return await market_service.get_candles(...)
```

---

### 4. **Async/Await Throughout**

Entire codebase uses async I/O:

```python
async def run_cycle():
    """Main trading cycle"""
    df = await market_service.get_candles(...)
    signal = await strategy.on_candle(df)
    if signal:
        await order_service.place_order(signal)
        await notification_manager.send_alert(...)
```

**Benefits:**
- Non-blocking I/O (FastAPI handles thousands of concurrent requests)
- Scheduler runs cycles without blocking market data stream
- WebSocket connections don't block REST API
- Better resource utilization

---

### 5. **Signal Pipeline Architecture**

Every trade signal flows through a pipeline of processors:

```
Signal → Risk Guard → Executor → Alerter → Broadcaster → DB
         ↓             ↓          ↓         ↓             ↓
      ✓/✗         Place       Email     WebSocket    TradeLog
                  order       notice      notify       entry
```

Each processor:
- Can reject the signal (short-circuit)
- Adds metadata (execution details, alert info)
- Passes transformed signal to next processor

**Benefits:**
- Modular: easy to add/remove processors
- Debuggable: clear signal transformation flow
- Testable: mock each processor independently

---

### 6. **Paper Trading by Default**

`PAPER_TRADING=True` is the default:

```python
if paper_trading:
    # Simulate trade execution
    log_simulated_trade(order)
else:
    # Execute real order via Upstox API
    upstox_response = place_real_order(order)
```

**Benefits:**
- Prevents accidental real trades during development/testing
- Safe sandbox for strategy validation
- Switch to live with one setting change (recommended with caution)

---

### 7. **Market-Hours Awareness**

All scheduling aligned to IST (Indian Standard Time):

```python
scheduler.add_job(
    candle_check_hook,
    'cron',
    minute='*/1',  # Every minute
    start_date='09:15',  # Market open (IST)
    end_date='15:30',    # Market close (IST)
    timezone='Asia/Kolkata'
)
```

**Benefits:**
- No off-hours evaluation (saves resources)
- Prevents pre-market false signals
- Auto-handles daylight saving transitions
- Clear market awareness in logs

---

## Common How-Tos & Recipes

### 1. How to Add a New Trading Strategy

**Step 1:** Create strategy file
```bash
touch app/strategies/my_strategy.py
```

**Step 2:** Implement BaseStrategy
```python
# app/strategies/my_strategy.py
from app.strategies.base import BaseStrategy
from app.orders.models import TradeSignal
import pandas as pd

class MyStrategy(BaseStrategy):
    """My custom trading strategy"""

    def on_candle(self, df: pd.DataFrame) -> TradeSignal:
        """
        Evaluate latest candle.
        df: DataFrame with OHLCV data (100+ rows)
        """
        # Get latest candle
        latest = df.iloc[-1]
        prev = df.iloc[-2]

        # Your signal logic
        if latest['close'] > latest['open'] and latest['volume'] > df['volume'].mean():
            return TradeSignal.BUY
        elif latest['close'] < latest['open']:
            return TradeSignal.SELL
        else:
            return TradeSignal.NONE

    def compute_exit(self, entry_price, side, current_price, df):
        """
        Calculate exit levels.
        Returns dict with 'stop_loss', 'take_profit', 'trailing_stop'
        """
        # Simple ATR-based stops
        atr = (df['high'].rolling(14).max() - df['low'].rolling(14).min()).mean()

        return {
            'stop_loss': entry_price - (2 * atr),
            'take_profit': entry_price + (3 * atr),
            'trailing_stop': None  # Disable trailing stop
        }

    @staticmethod
    def default_params():
        """Configurable parameters with defaults"""
        return {
            'enabled': False,
            'volume_multiplier': 1.5,
            'min_volume': 10000,
        }
```

**Step 3:** Register in engine
```python
# In app/engine.py
from app.strategies.my_strategy import MyStrategy

STRATEGY_MAP = {
    'SuperTrendPro': SuperTrendPro,
    'ScalpPro': ScalpPro,
    'MyStrategy': MyStrategy,  # ← Add this
}
```

**Step 4:** Set as active
```bash
# Option A: Via API
curl -X PUT http://localhost:8210/settings/ \
  -H "Content-Type: application/json" \
  -d '{"ACTIVE_STRATEGY_CLASS": "MyStrategy"}'

# Option B: Edit .env
ACTIVE_STRATEGY_CLASS=MyStrategy

# Option C: Via frontend settings panel
```

**Step 5:** Test
```bash
# Backend test
python scripts/check_strategies.py

# Run a manual cycle
curl -X POST http://localhost:8210/engine/run-cycle
```

---

### 2. How to Add a Telegram Notification Provider

**Step 1:** Create Telegram provider
```python
# app/notifications/telegram.py
from app.notifications.base import NotificationProvider
import aiohttp

class TelegramProvider(NotificationProvider):
    """Send notifications via Telegram Bot API"""

    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.base_url = "https://api.telegram.org"

    async def send(self, message: str, alert_type: str = 'INFO'):
        """Send message via Telegram"""
        url = f"{self.base_url}/bot{self.bot_token}/sendMessage"
        payload = {
            'chat_id': self.chat_id,
            'text': message,
            'parse_mode': 'Markdown'
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                if resp.status != 200:
                    raise Exception(f"Telegram API error: {resp.status}")

    def validate_config(self) -> bool:
        """Verify Telegram credentials are set"""
        return bool(self.bot_token and self.chat_id)
```

**Step 2:** Register in app startup
```python
# In app/main.py
from app.notifications.telegram import TelegramProvider

@app.on_event("startup")
async def startup():
    # ... existing code ...

    # Register Telegram provider
    if settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID:
        telegram = TelegramProvider(
            bot_token=settings.TELEGRAM_BOT_TOKEN,
            chat_id=settings.TELEGRAM_CHAT_ID
        )
        notification_manager.register_provider('telegram', telegram)
```

**Step 3:** Add settings
```bash
# In .env
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
NOTIFICATION_CHANNELS=EMAIL,TELEGRAM  # Add TELEGRAM
```

**Step 4:** Test
```bash
curl -X POST http://localhost:8210/notifications/test \
  -H "Content-Type: application/json" \
  -d '{"message": "Test alert", "channels": ["telegram"]}'
```

---

### 3. How to Understand a Trade Execution Flow

**Scenario:** SuperTrendPro generates a BUY signal at 10:30 AM.

**Flow:**

1. **Signal Generation** (app/scheduler/ → app/strategies/supertrend_pro.py)
   ```python
   # Scheduler calls every minute
   df = await market_data_service.get_candles('NSE_INDEX|Nifty 50', '1minute')
   signal = await strategy.on_candle(df)
   # signal = TradeSignal.BUY
   ```

2. **Risk Checks** (app/engine_pipeline.py → RiskGuardProcessor)
   ```python
   # Check 1: Daily loss limit
   if daily_pnl < -MAX_DAILY_LOSS_PCT * capital:
       reject()  # ← Trading stopped for today

   # Check 2: Max concurrent positions
   if len(open_positions) >= MAX_CONCURRENT_POSITIONS:
       reject()  # ← Too many open positions

   # Check 3: Trading side restriction
   if signal.side == SELL and TRADING_SIDE == 'LONG_ONLY':
       reject()  # ← Can't short in LONG_ONLY mode
   ```

3. **Exit Level Computation** (app/strategies/supertrend_pro.py)
   ```python
   exit_levels = strategy.compute_exit(
       entry_price=current_ltp,
       side=BUY,
       current_price=current_ltp,
       df=df
   )
   # exit_levels = {
   #   'stop_loss': 49500,      # 2 ATR below entry
   #   'take_profit': 51500,    # 3 ATR above entry
   #   'trailing_stop': None    # Not using trailing
   # }
   ```

4. **Order Execution** (app/orders/service.py)
   ```python
   order_response = await order_service.place_order({
       'symbol': 'Nifty 50',
       'quantity': 1,
       'side': 'BUY',
       'order_type': 'MARKET',
       'stop_loss': 49500,
       'take_profit': 51500
   })
   # order_response = {
   #   'order_id': '123456',
   #   'status': 'COMPLETED',
   #   'filled_price': 50000
   # }
   ```

5. **Trade Logging** (app/database/)
   ```python
   # Insert into TradeLog table
   trade_log = TradeLog(
       symbol='Nifty 50',
       entry_price=50000,
       side='BUY',
       quantity=1,
       order_id='123456',
       strategy='SuperTrendPro',
       status='OPEN',
       created_at=datetime.now()
   )
   db.add(trade_log)
   db.commit()
   ```

6. **Alert Notification** (app/notifications/)
   ```python
   message = """
   🟢 BUY Signal Executed
   Symbol: Nifty 50
   Entry: ₹50,000
   Stop Loss: ₹49,500
   Take Profit: ₹51,500
   Time: 10:30 AM IST
   """
   await notification_manager.send_alert(message, 'TRADE')
   # Email sent + Telegram sent (if configured)
   ```

7. **Frontend Broadcast** (WebSocket)
   ```python
   # BroadcastProcessor sends to all connected WebSocket clients
   broadcast_message = {
       'type': 'TRADE_EXECUTED',
       'symbol': 'Nifty 50',
       'signal': 'BUY',
       'entry_price': 50000,
       'stop_loss': 49500,
       'take_profit': 51500,
       'timestamp': '2024-01-15T10:30:00Z'
   }
   # Frontend receives via WebSocket
   # Updates trade log table in real-time
   ```

---

### 4. How to Debug a Failing Signal

**If a strategy is not generating signals:**

**Step 1:** Check strategy is enabled
```bash
curl http://localhost:8210/strategies/SuperTrendPro/params
# Check "enabled": true
```

**Step 2:** Verify market data
```bash
curl "http://localhost:8210/market/candles?instrument_key=NSE_INDEX|Nifty%2050&interval=1minute"
# Check that candles array is populated
```

**Step 3:** Run a manual cycle with debug
```bash
# Add debug logging in app/engine.py
# Then run a cycle
curl -X POST http://localhost:8210/engine/run-cycle
# Check response for signal details
```

**Step 4:** Check logs
```bash
# View application logs
tail -f /tmp/upstox_trigger.log  # Adjust path

# Look for:
# - "Signal generated: BUY"
# - "Risk check failed: ..."
# - "Strategy evaluation error: ..."
```

**Step 5:** Test strategy in isolation
```python
# Create test script
# tests/test_signal_debug.py

import pandas as pd
from app.strategies.supertrend_pro import SuperTrendPro

# Create mock candle data
df = pd.read_csv('/path/to/candles.csv')

strategy = SuperTrendPro()
signal = strategy.on_candle(df)

print(f"Signal: {signal}")
print(f"Strategy state: {strategy.get_state()}")
```

---

### 5. How to Modify Risk Settings

**Risk Guard #1: Daily Loss Limit**
```bash
# Set max daily loss to 5% of capital
curl -X PUT http://localhost:8210/settings/ \
  -H "Content-Type: application/json" \
  -d '{"MAX_DAILY_LOSS_PCT": 5.0}'

# Now trading will auto-stop if daily loss > 5%
```

**Risk Guard #2: Max Concurrent Positions**
```bash
# Allow max 5 open positions
curl -X PUT http://localhost:8210/settings/ \
  -H "Content-Type: application/json" \
  -d '{"MAX_CONCURRENT_POSITIONS": 5}'

# Strategy will reject signals if 5+ positions open
```

**Risk Guard #3: Per-Trade Risk**
```bash
# Max 1% of capital risked per trade
curl -X PUT http://localhost:8210/settings/ \
  -H "Content-Type: application/json" \
  -d '{"MAX_RISK_PER_TRADE_PCT": 1.0}'

# Each trade's stop-loss distance capped to 1% of capital
```

**Risk Guard #4: Trading Side Restriction**
```bash
# Only allow LONG trades (no shorting)
curl -X PUT http://localhost:8210/settings/ \
  -H "Content-Type: application/json" \
  -d '{"TRADING_SIDE": "LONG_ONLY"}'

# Options: "LONG_ONLY" | "SHORT_ONLY" | "BOTH"
```

---

### 6. How to Test in Sandbox Mode

**Step 1:** Get Sandbox Credentials from Upstox

1. Go to https://upstox.com/developer (sandbox.upstox.com)
2. Create sandbox app
3. Note down: Sandbox API Key, Sandbox API Secret

**Step 2:** Configure Sandbox in .env
```bash
# .env
USE_SANDBOX=True
SANDBOX_API_KEY=your_sandbox_key
SANDBOX_API_SECRET=your_sandbox_secret
SANDBOX_REDIRECT_URI=http://localhost:8210/auth/callback
```

**Step 3:** Restart app and login
```bash
bash start_terminal.sh

# Then navigate to:
# http://localhost:8210/auth/login
# → Login with sandbox account
```

**Step 4:** Verify sandbox mode
```bash
curl http://localhost:8210/settings/ | grep USE_SANDBOX
# Should show: "USE_SANDBOX": true
```

**Step 5:** Test trades
```bash
# All trades now execute on sandbox (no real money)
# Great for testing before live!
```

---

## External Dependencies & Rate Limits

### Upstox SDK v2.21.0

**Rate Limits (Strictly Enforced):**

| Limit | Value | Used By | Actual Limit |
|-------|-------|---------|--------------|
| Per-Second | 50 req/sec | All API calls | 45 req/sec (safe margin) |
| Per-Minute | 500 req/min | Historical data | 450 req/min (safe margin) |
| Per 30-Minute | 2000 req/30min | Full session | 1900 req/30min (safe margin) |

**Enforced By:** `UpstoxRateLimiter` in [app/engine.py](app/engine.py)

```python
# Applies rate limits to all MarketDataService calls
class UpstoxRateLimiter:
    async def check_rate_limits():
        """Block if any limit would be exceeded"""
        if req_per_sec >= 45:
            await asyncio.sleep(0.1)
        if req_per_min >= 450:
            await asyncio.sleep(1.0)
        if req_per_30min >= 1900:
            await asyncio.sleep(30.0)
```

### WebSocket (MarketDataStreamerV3)

**Protocol:** Protobuf-encoded messages

**Channels:**
- `full`: All fields (price, volume, open interest, etc.)
- `ltpc`: Last traded price & close only (lighter)
- `option_greeks`: Greek values for options

**Auto-Reconnection:** Built-in exponential backoff (1s → 30s max)

### SMTP (Email Notifications)

**Tested Providers:**
- Gmail (SMTP: smtp.gmail.com:587)
- AWS SES (SMTP: email-smtp.region.amazonaws.com:587)
- Custom SMTP servers

**Security:** Use TLS/SSL (port 587 or 465)

---

## Summary for Quick Navigation

| Task | File | Key Function |
|------|------|--------------|
| **Start App** | [start_terminal.sh](start_terminal.sh) | Runs `uvicorn app.main:app` |
| **Main Entry** | [app/main.py](app/main.py) | FastAPI lifespan events |
| **Trading Engine** | [app/engine.py](app/engine.py) | `AutomationEngine`, `run_cycle()` |
| **Strategy Logic** | [app/strategies/supertrend_pro.py](app/strategies/supertrend_pro.py) | `SuperTrendPro.on_candle()` |
| **Risk Guards** | [app/orders/service.py](app/orders/service.py) | `pre_trade_checks()` |
| **Order Execution** | [app/orders/service.py](app/orders/service.py) | `place_order()` |
| **Data Streaming** | [app/market_data/streamer.py](app/market_data/streamer.py) | `MarketDataStreamer` |
| **Scheduling** | [app/scheduler/service.py](app/scheduler/service.py) | `APScheduler` hooks |
| **Database** | [app/database/connection.py](app/database/connection.py) | SQLAlchemy setup |
| **Notifications** | [app/notifications/manager.py](app/notifications/manager.py) | `send_alert()` |
| **Settings API** | [app/settings_routes.py](app/settings_routes.py) | `GET/PUT /settings/` |
| **Frontend** | [frontend/index.html](frontend/index.html) + [frontend/js/app.js](frontend/js/app.js) | Web dashboard |

---

**Happy coding! 🚀**

This guide is maintained alongside the codebase. For questions or updates, refer to [README.md](README.md) or contact the team.
