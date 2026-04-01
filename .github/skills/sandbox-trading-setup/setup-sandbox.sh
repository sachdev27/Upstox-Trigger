#!/bin/bash
#
# setup-sandbox.sh - Initialize Upstox-Trigger sandbox environment
#
# Usage:
#   ./setup-sandbox.sh --scenario bull-market    # Full reset with test data
#   ./setup-sandbox.sh --quick                   # Quick reset (reuse DB)
#   ./setup-sandbox.sh --cleanup                 # Stop and cleanup
#   ./setup-sandbox.sh --validate                # Validate existing sandbox
#
# Scenarios: bull-market, bear-market, sideways, gap-up-open, circuit-breaker
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SKILLS_DIR="$PROJECT_ROOT/.github/skills/sandbox-trading-setup"
SANDBOX_DB="$PROJECT_ROOT/sandbox.db"
SANDBOX_PORT=8211
PRODUCTION_PORT=8210
VENV_PATH="$PROJECT_ROOT/venv"

# Configuration
SCENARIO="bull-market"
MODE="full"
QUICK=false
CLEANUP=false
VALIDATE=false

# Parse arguments
while [[ $# -gt 0 ]]; do
  case $1 in
    --scenario)
      SCENARIO="$2"
      shift 2
      ;;
    --quick)
      MODE="quick"
      QUICK=true
      shift
      ;;
    --cleanup)
      CLEANUP=true
      shift
      ;;
    --validate)
      VALIDATE=true
      shift
      ;;
    --full)
      MODE="full"
      shift
      ;;
    *)
      echo "Unknown option: $1"
      echo "Usage: ./setup-sandbox.sh [--scenario bull-market|bear-market|sideways|gap-up-open|circuit-breaker|--cleanup|--validate|--quick]"
      exit 1
      ;;
  esac
done

# Validate scenario
valid_scenarios=("bull-market" "bear-market" "sideways" "gap-up-open" "circuit-breaker")
if [[ ! " ${valid_scenarios[@]} " =~ " ${SCENARIO} " ]]; then
  echo -e "${RED}❌ Invalid scenario: $SCENARIO${NC}"
  echo "Valid scenarios: ${valid_scenarios[*]}"
  exit 1
fi

# Helper functions
print_header() {
  echo -e "\n${BLUE}═══════════════════════════════════════════════${NC}"
  echo -e "${BLUE}$1${NC}"
  echo -e "${BLUE}═══════════════════════════════════════════════${NC}\n"
}

check_success() {
  if [ $? -eq 0 ]; then
    echo -e "${GREEN}✅ $1${NC}"
  else
    echo -e "${RED}❌ $1${NC}"
    exit 1
  fi
}

check_venv() {
  if [ ! -d "$VENV_PATH" ]; then
    echo -e "${RED}❌ Virtual environment not found at $VENV_PATH${NC}"
    echo "Create it with: python3 -m venv venv"
    exit 1
  fi

  # Activate venv
  source "$VENV_PATH/bin/activate"
  check_success "Virtual environment activated"
}

check_files() {
  if [ ! -f "$PROJECT_ROOT/app/main.py" ]; then
    echo -e "${RED}❌ app/main.py not found. Wrong directory?${NC}"
    exit 1
  fi
  check_success "Project files found"
}

check_ports() {
  if lsof -Pi :$SANDBOX_PORT -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo -e "${YELLOW}⚠️  Port $SANDBOX_PORT already in use${NC}"
    echo "Kill existing process with: lsof -ti:$SANDBOX_PORT | xargs kill -9"
    exit 1
  fi
  check_success "Port $SANDBOX_PORT is available"
}

stop_running_apps() {
  echo "Stopping running Upstox-Trigger instances..."

  # Kill any uvicorn processes on our ports
  lsof -ti:$SANDBOX_PORT | xargs kill -9 2>/dev/null || true
  lsof -ti:$PRODUCTION_PORT | xargs kill -9 2>/dev/null || true

  sleep 1
  check_success "Running instances stopped"
}

reset_database() {
  echo "Resetting database..."

  if [ -f "$SANDBOX_DB" ]; then
    rm -f "$SANDBOX_DB"
  fi

  # Initialize database using Python
  python3 << 'EOF'
from app.database.connection import engine
from app.database.models import Base
Base.metadata.create_all(bind=engine)
print("Database tables created")
EOF

  check_success "Database reset"
}

seed_config() {
  echo "Seeding configuration and instruments..."

  python3 << 'EOF'
import os
os.environ['DATABASE_URL'] = 'sqlite:///./sandbox.db'

from app.database.connection import SessionLocal
from app.database.models import ConfigSetting, Watchlist
from app.database.seed import seed_settings, seed_watchlist_nifty50

db = SessionLocal()

# Seed settings
seed_settings(db)
print("✓ Settings seeded")

# Seed watchlist
seed_watchlist_nifty50(db)
print("✓ Watchlist seeded")

# Override with sandbox-safe values
config_overrides = {
    'USE_SANDBOX': 'True',
    'PAPER_TRADING': 'False',  # Test real order flow
    'MAX_DAILY_LOSS_PCT': '1.0',  # Conservative
    'MAX_CONCURRENT_POSITIONS': '2',
    'MAX_RISK_PER_TRADE_PCT': '0.5',
    'TRADING_SIDE': 'BOTH',
}

for key, value in config_overrides.items():
    setting = db.query(ConfigSetting).filter_by(key=key).first()
    if setting:
        setting.value = value
    else:
        setting = ConfigSetting(key=key, value=value)
        db.add(setting)

db.commit()
print("✓ Sandbox configuration applied")
db.close()
EOF

  check_success "Configuration seeded"
}

load_test_data() {
  echo "Loading test data scenario: $SCENARIO..."

  python3 << EOF
import os
import json
import pandas as pd
from datetime import datetime, timedelta
os.environ['DATABASE_URL'] = 'sqlite:///./sandbox.db'

from app.database.connection import SessionLocal
from app.database.models import CandleCache

db = SessionLocal()

# Generate test candles based on scenario
scenario = "$SCENARIO"
num_candles = 100
base_price = 16500  # Nifty 50 base

if scenario == "bull-market":
    prices = [base_price + (i * 15) + (i % 3 * 5) for i in range(num_candles)]
    volumes = [1000000 + (i * 5000) for i in range(num_candles)]
    description = "Strong uptrend, high volume"
elif scenario == "bear-market":
    prices = [base_price - (i * 16) + (i % 3 * 3) for i in range(num_candles)]
    volumes = [800000 - (i * 2000) for i in range(num_candles)]
    description = "Strong downtrend, low volume"
elif scenario == "sideways":
    prices = [base_price + (50 * (i % 4 - 2)) for i in range(num_candles)]
    volumes = [600000 + (i * 500) for i in range(num_candles)]
    description = "Choppy sideways, low volatility"
elif scenario == "gap-up-open":
    # Gap up 2%, then continue up
    prices = [base_price + 350] + [base_price + 350 + (i * 10) for i in range(1, num_candles)]
    volumes = [1500000 for _ in range(num_candles)]
    description = "Gap up at open, strong continuation"
elif scenario == "circuit-breaker":
    # Simulate crash
    prices = [base_price - (i * 20) for i in range(num_candles)]
    volumes = [2000000 for _ in range(num_candles)]
    description = "Extreme volatility, panic selling"

# Create candle data
now = datetime.now()
candles = []

for i in range(num_candles):
    timestamp = now - timedelta(minutes=num_candles - i)
    open_price = prices[i] - 10
    close_price = prices[i]
    high_price = max(open_price, close_price) + 20
    low_price = min(open_price, close_price) - 20

    candles.append({
        "timestamp": timestamp.isoformat(),
        "open": round(open_price, 2),
        "high": round(high_price, 2),
        "low": round(low_price, 2),
        "close": round(close_price, 2),
        "volume": volumes[i % len(volumes)]
    })

# Store in cache
cache = CandleCache(
    instrument_key="NSE_INDEX|Nifty 50",
    interval="1minute",
    timestamp=now.isoformat(),
    ohlcv_data=json.dumps(candles),
    cache_time=now.isoformat()
)

db.add(cache)
db.commit()
db.close()

print(f"✓ Loaded {num_candles} candles ({description})")
EOF

  check_success "Test data loaded"
}

start_app() {
  echo "Starting FastAPI app on port $SANDBOX_PORT..."

  # Start in background
  cd "$PROJECT_ROOT"
  PYTHONUNBUFFERED=1 uvicorn app.main:app --port $SANDBOX_PORT --reload > /tmp/sandbox-app.log 2>&1 &
  APP_PID=$!

  # Wait for startup
  sleep 3

  if ! kill -0 $APP_PID 2>/dev/null; then
    echo -e "${RED}❌ App failed to start${NC}"
    cat /tmp/sandbox-app.log
    exit 1
  fi

  # Check health
  for i in {1..10}; do
    if curl -s http://localhost:$SANDBOX_PORT/health >/dev/null 2>&1; then
      check_success "FastAPI running on port $SANDBOX_PORT"
      return 0
    fi
    sleep 1
  done

  echo -e "${RED}❌ App didn't respond to health check${NC}"
  kill $APP_PID 2>/dev/null || true
  exit 1
}

run_health_checks() {
  print_header "Running Health Checks"

  echo "Checking database..."
  curl -s http://localhost:$SANDBOX_PORT/health > /dev/null && echo -e "${GREEN}✅ Database OK${NC}"

  echo "Checking API connectivity..."
  curl -s http://localhost:$SANDBOX_PORT/market/ltp?instrument_key=NSE_INDEX%7CNifty%2050 > /dev/null && echo -e "${GREEN}✅ API responding${NC}"

  echo "Checking candle cache..."
  response=$(curl -s 'http://localhost:8211/market/candles?instrument_key=NSE_INDEX|Nifty%2050&interval=1minute')
  num_candles=$(echo "$response" | python3 -c "import json,sys; print(len(json.load(sys.stdin).get('candles', [])))" 2>/dev/null || echo 0)
  if [ "$num_candles" -gt 50 ]; then
    echo -e "${GREEN}✅ Candle cache loaded ($num_candles candles)${NC}"
  fi

  echo "Checking settings..."
  curl -s http://localhost:$SANDBOX_PORT/settings/ | grep -q "USE_SANDBOX" && echo -e "${GREEN}✅ Settings accessible${NC}"
}

generate_report() {
  print_header "Setup Complete"

  cat > "$PROJECT_ROOT/sandbox-report.json" << EOF
{
  "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "scenario": "$SCENARIO",
  "database": "$SANDBOX_DB",
  "port": $SANDBOX_PORT,
  "venv": "$VENV_PATH",
  "status": "ready",
  "endpoints": {
    "health": "http://localhost:$SANDBOX_PORT/health",
    "api": "http://localhost:$SANDBOX_PORT/docs",
    "settings": "http://localhost:$SANDBOX_PORT/settings/"
  },
  "next_steps": [
    "Test a strategy: /strategy-debugging",
    "Validate risk guards: /risk-guard-validation",
    "Run test suite: pytest tests/test_smoke.py -v"
  ]
}
EOF

  echo "📊 Sandbox Ready!"
  echo ""
  echo "  API:            http://localhost:$SANDBOX_PORT"
  echo "  Swagger Docs:   http://localhost:$SANDBOX_PORT/docs"
  echo "  Scenario:       $SCENARIO"
  echo "  Database:       $SANDBOX_DB"
  echo "  Report:         $PROJECT_ROOT/sandbox-report.json"
  echo ""
  echo "Next steps:"
  echo "  • Test strategy:      /strategy-debugging"
  echo "  • Validate risk:      /risk-guard-validation"
  echo "  • Run test suite:     pytest tests/test_smoke.py -v"
  echo ""
}

cleanup_environment() {
  print_header "Cleaning Up"

  stop_running_apps

  rm -f "$SANDBOX_DB"
  rm -f "$PROJECT_ROOT/sandbox-report.json"
  rm -f /tmp/sandbox-app.log

  check_success "Cleanup complete"
}

validate_environment() {
  print_header "Validating Sandbox"

  if [ ! -f "$SANDBOX_DB" ]; then
    echo -e "${RED}❌ Sandbox database not found${NC}"
    exit 1
  fi

  echo "✅ Database exists"

  if ! curl -s http://localhost:$SANDBOX_PORT/health >/dev/null 2>&1; then
    echo -e "${YELLOW}⚠️  App not running on port $SANDBOX_PORT${NC}"
    echo "Start with: ./setup-sandbox.sh --scenario bull-market"
  else
    echo "✅ API responding"
  fi

  if [ -f "$PROJECT_ROOT/sandbox-report.json" ]; then
    echo "✅ Report found"
  fi

  print_header "Validation Complete"
}

# Main execution
if [ "$CLEANUP" = true ]; then
  cleanup_environment
  exit 0
fi

if [ "$VALIDATE" = true ]; then
  validate_environment
  exit 0
fi

print_header "Upstox-Trigger Sandbox Setup"

check_venv
check_files
check_ports

if [ "$MODE" = "full" ]; then
  stop_running_apps
  reset_database
  seed_config
fi

load_test_data
start_app
run_health_checks
generate_report
