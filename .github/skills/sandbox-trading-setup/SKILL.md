---
name: sandbox-trading-setup
description: >
  Fully initialize a sandbox trading environment for safe testing.
  Use when you need a fresh sandbox with test data, mock credentials,
  and disabled live trading. Automated setup takes ~2 minutes.
  Includes database initialization, test data loading, credential
  configuration, and verification steps.
argument-hint: "[bull-market|bear-market|sideways|gap-up-open|circuit-breaker|--cleanup|--validate]"
user-invocable: true
disable-model-invocation: false
---

# Sandbox Trading Environment Setup

This skill automates complete sandbox initialization for testing strategies safely without touching live accounts.

## What this skill does

✅ **Database Initialization**
- Create/reset all tables (TradeLog, StrategyState, ConfigSetting, Watchlist, etc.)
- Seed instruments (Nifty 50, BankNifty)
- Clear old trade logs

✅ **Test Data Loading**
- Load historical candles for chosen scenario (bull/bear/sideways/gap/circuit-breaker)
- Populate CandleCache with fresh data (invalidates 55s TTL)
- Set timestamps to realistic values

✅ **Configuration**
- Write sandbox Upstox credentials to ConfigSetting table
- Disable PAPER_TRADING (test real order flow without money)
- Enable USE_SANDBOX flag
- Set safe risk limits (1% daily loss cap, 2 max positions)

✅ **Services**
- Launch FastAPI app on test port 8211 (doesn't conflict with live 8210)
- Initialize APScheduler with IST timezone
- Start WebSocket mock to simulate market data streams

✅ **Verification**
- Run health checks
- Validate candle data loaded correctly
- Test market data endpoints
- Generate setup report

## When to use

- **New developer** joining the team (fresh environment in 2 min)
- **Testing a new strategy** (known-good environment)
- **Debugging a signal issue** (clean slate, test scenario)
- **Pre-live validation** (ensure all systems work correctly)
- **Resetting after tests** (cleanup command)

## Quick Start

### Full sandbox reset (most common)
```bash
cd /path/to/Upstox-Trigger
./setup-sandbox.sh --scenario bull-market
```

Output:
```
✅ Database reset (9 tables cleared & recreated)
✅ Instruments seeded (Nifty 50, BankNifty)
✅ Test data loaded (bull-market scenario, 100 candles)
✅ Sandbox credentials configured
✅ PAPER_TRADING disabled (simulate real order flow)
✅ Risk limits configured (1% daily loss, max 2 positions)
✅ FastAPI starting on http://localhost:8211
✅ Waiting for app startup... done
✅ Running health checks...

  Database: ✅
  API: ✅ (responding)
  Candle data: ✅ (100 candles loaded for Nifty 50)
  Market data: ✅ (mock stream ready)

📊 Sandbox ready! Environment report: ./sandbox-report.json
```

### Test with different market conditions

```bash
# Bull market (strong uptrend, high volume)
./setup-sandbox.sh --scenario bull-market

# Bear market (strong downtrend, low volume)
./setup-sandbox.sh --scenario bear-market

# Sideways market (choppy, low volatility)
./setup-sandbox.sh --scenario sideways

# Gap up open (test gaps in overnight session)
./setup-sandbox.sh --scenario gap-up-open

# Circuit breaker (test extreme volatility)
./setup-sandbox.sh --scenario circuit-breaker
```

### Quick reset (keep existing database)
```bash
./setup-sandbox.sh --quick
# Reuses existing database, just reloads test data & restarts app
# Faster for iterative testing (~30 seconds)
```

### Cleanup and reset
```bash
./setup-sandbox.sh --cleanup
# Stops FastAPI, closes SQLite connections
# Removes test database (keeps original .env)
# Safe to run if you need to start fresh

# Then restart:
./setup-sandbox.sh --scenario bull-market
```

### Validate existing sandbox
```bash
./setup-sandbox.sh --validate
# Checks database integrity
# Verifies candle data
# Tests all API endpoints
# Does NOT modify data
```

## Test Scenarios Explained

### **bull-market**
- Nifty 50: 16,000 → 17,500 (9.4% gain)
- Volume: 150% of normal (breakout conditions)
- Volatility (ATR): Elevated but controlled
- **Use for:** Testing strategies that catch uptrends

### **bear-market**
- Nifty 50: 17,000 → 15,200 (-10.6% loss)
- Volume: 80% of normal (low conviction selling)
- Volatility: Moderate
- **Use for:** Testing risk guards, stop-loss execution

### **sideways**
- Nifty 50: 16,500 ± 200 (choppy, no direction)
- Volume: Low & inconsistent
- Volatility: Very low (ATR ≈ 50 points)
- **Use for:** Testing whipsaw resilience, confirmation gates

### **gap-up-open**
- Nifty 50: Previous close 16,500 → Open 16,850 (+2.1% gap)
- Post-open: Strong continuation upward
- Volume: High (enthusiasm buying)
- **Use for:** Testing entry logic at market open, gap handling

### **circuit-breaker**
- Nifty 50: 16,500 → 15,200 (-7.9%) rapid
- Multiple halt periods (5%, 10%, 20% limits)
- Volume: Extreme panic
- **Use for:** Testing emergency stop logic, risk limit triggers

## Environment Configuration

The sandbox uses these safe defaults:

```python
# Risk limits (can't blow up account)
MAX_DAILY_LOSS_PCT = 1.0          # Stop after 1% loss
MAX_CONCURRENT_POSITIONS = 2      # Max 2 open positions
MAX_RISK_PER_TRADE_PCT = 0.5      # Each trade risks 0.5%

# Trading restrictions
PAPER_TRADING = False              # Simulate REAL order flow
TRADING_SIDE = "BOTH"              # Allow long & short
USE_SANDBOX = True                 # Use sandbox Upstox credentials

# API credentials (sandbox/fake)
API_KEY = "sandbox_key_xyz"        # Not real, safe to commit
API_SECRET = "sandbox_secret_xyz"  # Not real, safe to commit

# Database
DATABASE = sqlite:///./sandbox.db  # Separate from production.db
```

## Verify Setup Worked

```bash
# Health check
curl http://localhost:8211/health
# Expected: {"status": "ok", "mode": "sandbox", "database": "sandbox.db"}

# Check candle data
curl 'http://localhost:8211/market/candles?instrument_key=NSE_INDEX|Nifty%2050&interval=1minute'
# Expected: {candles: [{open: 16000, high: 16050, ...}, ...]}

# Check settings
curl http://localhost:8211/settings/ | grep -E 'PAPER_TRADING|USE_SANDBOX'
# Expected: "PAPER_TRADING": false, "USE_SANDBOX": true
```

## Common Issues & Solutions

### ❌ "Address already in use: port 8211"
Old process still running. Kill it and restart:
```bash
./setup-sandbox.sh --cleanup
./setup-sandbox.sh --scenario bull-market
```

### ❌ "No such file or directory: test-data-seed.sql"
Make sure you're in the project root:
```bash
cd /Users/diviine/Projects/Upstox-Trigger
./setup-sandbox.sh --scenario bull-market
```

### ❌ "sqlite3: database is locked"
Multiple processes accessing database. Stop all instances:
```bash
pkill -f "uvicorn app.main"
pkill -f "sqlite3"
# Wait 2 seconds
./setup-sandbox.sh --cleanup && ./setup-sandbox.sh --scenario bull-market
```

### ❌ "KeyError: 'TRADING_CAPITAL'"
Database seeding didn't complete. Full reset:
```bash
./setup-sandbox.sh --cleanup
rm -f sandbox.db
./setup-sandbox.sh --full
```

### ❌ "ModuleNotFoundError: No module named 'app'"
Make sure venv is activated:
```bash
source venv/bin/activate
./setup-sandbox.sh --scenario bull-market
```

## What the Setup Scripts Do

### `setup-sandbox.sh` (Main orchestrator)
[See implementation file `setup-sandbox.sh`](./setup-sandbox.sh)
1. Validates environment (venv, Python, files present)
2. Parses command-line arguments
3. Calls appropriate helper functions
4. Runs verification tests
5. Generates report

### `test-data-seed.sql` (Database initialization)
[See implementation file `test-data-seed.sql`](./test-data-seed.sql)
- CREATE TABLE statements for all ORM models
- INSERT statements for Nifty 50 & BankNifty instruments
- Sample ConfigSetting rows (API keys, risk limits)
- Empty tables ready for test data

### `sandbox-config.env` (Configuration template)
[See implementation file `sandbox-config.env`](./sandbox-config.env)
- Sandbox API credentials
- Safe default risk limits
- Database path pointing to sandbox.db
- Flag to enable sandbox mode

## Next Steps After Setup

### Test a strategy
```bash
# 1. Enable your strategy
curl -X PUT http://localhost:8211/strategies/SuperTrendPro/params \
  -d '{"enabled": true}'

# 2. Run one evaluation cycle
curl -X POST http://localhost:8211/engine/run-cycle

# 3. Check if signals generated
curl http://localhost:8211/orders/book

# 4. View trade execution logs
curl http://localhost:8211/settings/ | grep LAST_TRADE
```

### Run the test suite
```bash
pytest tests/test_smoke.py -v
# Runs 10 smoke tests against sandbox environment
```

### Debug a specific issue
```bash
# See the strategy-debugging skill
/strategy-debugging --strategy SuperTrendPro
```

### Validate risk guards
```bash
# See the risk-guard-validation skill
/risk-guard-validation --comprehensive
```

## Cleanup

When you're done testing and want to return to production:

```bash
# Stop sandbox
./setup-sandbox.sh --cleanup

# Verify production is running (port 8210)
curl http://localhost:8210/health
```

## Architecture Notes

The sandbox environment runs completely independently:
- **Port:** 8211 (vs production 8210)
- **Database:** `./sandbox.db` (vs production database)
- **Credentials:** Sandbox Upstox keys (fake/test only)
- **Mode:** USE_SANDBOX=True, PAPER_TRADING=False (test real order flow safely)

This lets you:
- Query production API at port 8210 while testing sandbox at 8211
- Switch between environments instantly
- Test order execution without risking capital
- Keep test data isolated from production logs
