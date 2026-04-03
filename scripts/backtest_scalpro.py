#!/usr/bin/env python3
"""
ScalpPro Backtesting Engine — Nifty 500 Parameter Optimization
================================================================
Walk-forward backtesting across 415 Nifty 500 instruments.
Sweeps sl_atr_multiplier × tp_atr_multiplier × fast_ema combos.
Outputs a ranked table of parameter sets with win-rate, profit-factor, Sharpe.

Usage:
  # With live Upstox API (requires .env with ACCESS_TOKEN):
  python scripts/backtest_scalpro.py --mode api --max-instruments 50

  # Offline mode (uses synthetic/cached data):
  python scripts/backtest_scalpro.py --mode offline

  # Full Nifty 500 (needs API, ~30 min):
  python scripts/backtest_scalpro.py --mode api --max-instruments 500

  # Save results to CSV:
  python scripts/backtest_scalpro.py --mode api --out results/backtest_scalp.csv
"""

import sys
import os
import csv
import json
import argparse
import logging
import time
import math
import itertools
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── Ensure project imports work when run from project root ─────
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("backtest")

# ══ Constants ══════════════════════════════════════════════════
SLIPPAGE_PCT  = 0.05   # 0.05% slippage per side (realistic for large-caps)
COST_PCT      = 0.03   # 0.03% brokerage + STT + exchange charges per side
TOTAL_COST    = (SLIPPAGE_PCT + COST_PCT) / 100  # one-way fraction

MIN_BARS      = 50     # minimum bars needed before evaluating

# ── Parameter Grid (the sweep) ─────────────────────────────────
PARAM_GRID = {
    "fast_ema":            [5, 9, 13],
    "slow_ema":            [21],
    "rsi_period":          [14],
    "rsi_buy_thresh":      [45, 50, 55],
    "rsi_sell_thresh":     [45, 50, 55],
    "use_vwap_filter":     [True],
    "sl_atr_multiplier":   [0.5, 0.75, 1.0, 1.25, 1.5, 2.0],
    "tp_atr_multiplier":   [1.0, 1.5, 2.0, 2.5, 3.0],
    "atr_period":          [14],
    # Quality gate sweep
    "adx_period":          [14],
    "adx_threshold":       [0, 15, 20, 25],   # 0 = disabled
    "volume_spike_mult":   [0, 1.2, 1.5],     # 0 = disabled
    "volume_lookback_bars":[20],
}

QUICK_PARAM_GRID = {
    "fast_ema":            [5, 9],
    "slow_ema":            [21],
    "rsi_period":          [14],
    "rsi_buy_thresh":      [50, 55],
    "rsi_sell_thresh":     [45, 50],
    "use_vwap_filter":     [True],
    "sl_atr_multiplier":   [1.0, 1.5, 2.0],
    "tp_atr_multiplier":   [1.5, 2.0, 2.5],
    "atr_period":          [14],
    # Quality gate sweep (small)
    "adx_period":          [14],
    "adx_threshold":       [0, 20],           # 0 = disabled
    "volume_spike_mult":   [0, 1.2],          # 0 = disabled
    "volume_lookback_bars":[20],
}

# ══ Indicator Functions (pure numpy/pandas, no strategy import) ══

def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()

def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    hi, lo, pc = df["high"], df["low"], df["close"].shift(1)
    tr = pd.concat([hi - lo, (hi - pc).abs(), (lo - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

def _vwap(df: pd.DataFrame) -> pd.Series:
    """Anchored VWAP reset each session (date boundary)."""
    if "volume" not in df.columns or df["volume"].sum() == 0:
        return ((df["high"] + df["low"] + df["close"]) / 3)
    tp = (df["high"] + df["low"] + df["close"]) / 3
    cum_tpv = (tp * df["volume"]).cumsum()
    cum_vol = df["volume"].cumsum().replace(0, np.nan)
    return cum_tpv / cum_vol

def _adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder-smoothed ADX matching app/strategies/indicators.py."""
    alpha = 1.0 / period
    hi, lo = df["high"], df["low"]
    up_move   = hi.diff()
    down_move = -lo.diff()
    plus_dm  = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    pc = df["close"].shift(1)
    tr_s = pd.concat([hi - lo, (hi - pc).abs(), (lo - pc).abs()], axis=1).max(axis=1)
    smoothed_tr  = pd.Series(plus_dm,  index=df.index)  # reuse series
    s_tr   = tr_s.ewm(alpha=alpha, min_periods=period, adjust=False).mean()
    s_plus = pd.Series(plus_dm,  index=df.index).ewm(alpha=alpha, min_periods=period, adjust=False).mean()
    s_minus= pd.Series(minus_dm, index=df.index).ewm(alpha=alpha, min_periods=period, adjust=False).mean()
    plus_di  = 100 * s_plus  / s_tr.replace(0, np.nan)
    minus_di = 100 * s_minus / s_tr.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=alpha, min_periods=period, adjust=False).mean()

# ══ Signal Generation ══════════════════════════════════════════

def generate_signals(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    """
    Compute all ScalpPro indicators and generate BUY/SELL signals.
    Returns df with added columns: signal (1=BUY, -1=SELL, 0=NONE),
    sl_price, tp_price.

    Applies the same hard gates as the live strategy:
      • ADX gate  — skip crossovers when ADX < p["adx_threshold"]  (0 = disabled)
      • Volume gate — skip when bar volume < p["volume_spike_mult"] × SMA volume  (0 = disabled)
    """
    df = df.copy()

    df["fast"]  = _ema(df["close"], p["fast_ema"])
    df["slow"]  = _ema(df["close"], p["slow_ema"])
    df["rsi"]   = _rsi(df["close"], p["rsi_period"])
    df["atr"]   = _atr(df, p["atr_period"])
    df["vwap"]  = _vwap(df)

    # Optional hard-gate indicators
    adx_thresh = float(p.get("adx_threshold", 0))
    vol_mult   = float(p.get("volume_spike_mult", 0))

    if adx_thresh > 0:
        df["adx"] = _adx(df, int(p.get("adx_period", 14)))
    if vol_mult > 0 and "volume" in df.columns:
        df["vol_ma"] = df["volume"].rolling(int(p.get("volume_lookback_bars", 20))).mean()

    fast, slow = df["fast"], df["slow"]

    # Crossover detection
    buy_cross  = (fast > slow) & (fast.shift(1) <= slow.shift(1))
    sell_cross = (fast < slow) & (fast.shift(1) >= slow.shift(1))

    close = df["close"]
    rsi   = df["rsi"]
    atr   = df["atr"]
    vwap  = df["vwap"]

    use_vwap = p.get("use_vwap_filter", True)

    buy_cond  = buy_cross  & (rsi >= p["rsi_buy_thresh"])
    sell_cond = sell_cross & (rsi <= p["rsi_sell_thresh"])

    if use_vwap:
        buy_cond  = buy_cond  & (close > vwap)
        sell_cond = sell_cond & (close < vwap)

    # ADX hard gate — block both buy and sell when trend is weak/ranging
    if adx_thresh > 0 and "adx" in df.columns:
        strong_trend = df["adx"] >= adx_thresh
        buy_cond  = buy_cond  & strong_trend
        sell_cond = sell_cond & strong_trend

    # Volume spike hard gate — filter low-conviction crossovers
    if vol_mult > 0 and "vol_ma" in df.columns and "volume" in df.columns:
        vol_ok    = df["volume"] >= (df["vol_ma"] * vol_mult)
        buy_cond  = buy_cond  & vol_ok
        sell_cond = sell_cond & vol_ok

    df["signal"]   = 0
    df.loc[buy_cond,  "signal"] = 1
    df.loc[sell_cond, "signal"] = -1

    sl_dist = atr * p["sl_atr_multiplier"]
    tp_dist = atr * p["tp_atr_multiplier"]

    df["sl_price"] = 0.0
    df["tp_price"] = 0.0

    df.loc[buy_cond,  "sl_price"] = close[buy_cond]  - sl_dist[buy_cond]
    df.loc[buy_cond,  "tp_price"] = close[buy_cond]  + tp_dist[buy_cond]
    df.loc[sell_cond, "sl_price"] = close[sell_cond] + sl_dist[sell_cond]
    df.loc[sell_cond, "tp_price"] = close[sell_cond] - tp_dist[sell_cond]

    return df

# ══ Trade Simulation ════════════════════════════════════════════

def simulate_trades(df: pd.DataFrame) -> list[dict]:
    """
    Event-driven bar-by-bar simulation.
    On signal bar: enter at NEXT bar's open + slippage.
    Each subsequent bar: check if high/low breaches SL or TP.
    Exit on first breach. Max hold = 30 bars (prevent stale positions).
    """
    trades = []
    n = len(df)
    i = MIN_BARS  # skip warm-up period

    while i < n:
        sig    = df["signal"].iloc[i]
        sl_pr  = df["sl_price"].iloc[i]
        tp_pr  = df["tp_price"].iloc[i]

        if sig == 0 or i + 1 >= n:
            i += 1
            continue

        # Enter at next bar's open with slippage
        entry_bar  = i + 1
        raw_entry  = df["open"].iloc[entry_bar]
        if sig == 1:
            entry = raw_entry * (1 + TOTAL_COST)   # buy: slightly higher
        else:
            entry = raw_entry * (1 - TOTAL_COST)   # sell: slightly lower

        # Update SL/TP relative to actual entry (they were computed on signal bar)
        # Keep the same risk/reward distance ratio
        sl_dist = abs(entry - sl_pr)
        tp_dist = abs(entry - tp_pr)
        if sig == 1:
            sl = entry - sl_dist
            tp = entry + tp_dist
        else:
            sl = entry + sl_dist
            tp = entry - tp_dist

        # Scan forward for exit
        exit_price = None
        exit_reason = "TIMEOUT"
        max_hold = min(entry_bar + 30, n)

        for j in range(entry_bar + 1, max_hold):
            bar_high = df["high"].iloc[j]
            bar_low  = df["low"].iloc[j]

            if sig == 1:   # Long
                if bar_low <= sl:
                    exit_price  = sl * (1 - TOTAL_COST)
                    exit_reason = "SL"
                    break
                if bar_high >= tp:
                    exit_price  = tp * (1 - TOTAL_COST)
                    exit_reason = "TP"
                    break
            else:          # Short
                if bar_high >= sl:
                    exit_price  = sl * (1 + TOTAL_COST)
                    exit_reason = "SL"
                    break
                if bar_low <= tp:
                    exit_price  = tp * (1 + TOTAL_COST)
                    exit_reason = "TP"
                    break

        if exit_price is None:
            # TIMEOUT: exit at last bar close
            exit_bar_idx = min(max_hold - 1, n - 1)
            if sig == 1:
                exit_price = df["close"].iloc[exit_bar_idx] * (1 - TOTAL_COST)
            else:
                exit_price = df["close"].iloc[exit_bar_idx] * (1 + TOTAL_COST)

        # Calculate return in %
        if sig == 1:
            ret_pct = (exit_price - entry) / entry * 100
        else:
            ret_pct = (entry - exit_price) / entry * 100

        trades.append({
            "entry_bar": entry_bar,
            "exit_bar": j if exit_reason != "TIMEOUT" else max_hold - 1,
            "entry": entry,
            "exit": exit_price,
            "side": "LONG" if sig == 1 else "SHORT",
            "reason": exit_reason,
            "ret_pct": ret_pct,
            "win": ret_pct > 0,
        })

        # Advance past this trade to avoid double-counting
        i = (j if exit_reason != "TIMEOUT" else max_hold - 1) + 1

    return trades

# ══ Statistics ══════════════════════════════════════════════════

def compute_stats(trades: list[dict]) -> dict:
    if not trades:
        return {
            "n_trades": 0, "win_rate": 0.0, "avg_ret": 0.0,
            "profit_factor": 0.0, "max_drawdown": 0.0, "sharpe": 0.0,
            "expectancy": 0.0,
        }

    rets = [t["ret_pct"] for t in trades]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]

    n = len(trades)
    win_rate = len(wins) / n * 100

    gross_profit = sum(wins)  if wins   else 0.0
    gross_loss   = sum(losses) if losses else 0.0

    profit_factor = (
        gross_profit / abs(gross_loss)
        if gross_loss != 0
        else (float("inf") if gross_profit > 0 else 0.0)
    )

    avg_win  = np.mean(wins)   if wins   else 0.0
    avg_loss = abs(np.mean(losses)) if losses else 0.0

    # Expectancy: avg_win × win_rate - avg_loss × loss_rate
    loss_rate = 1 - win_rate / 100
    expectancy = (avg_win * (win_rate / 100)) - (avg_loss * loss_rate)

    # Max drawdown in cumulative returns
    cum = np.cumsum(rets)
    rolling_max = np.maximum.accumulate(cum)
    drawdowns = rolling_max - cum
    max_dd = float(np.max(drawdowns)) if len(drawdowns) else 0.0

    # Simplified Sharpe (annualized from per-trade returns)
    sharpe = (
        (np.mean(rets) / np.std(rets) * math.sqrt(252))
        if np.std(rets) > 0
        else 0.0
    )

    return {
        "n_trades":      n,
        "win_rate":      round(win_rate, 2),
        "avg_ret":       round(np.mean(rets), 3),
        "profit_factor": round(profit_factor, 3),
        "max_drawdown":  round(max_dd, 3),
        "sharpe":        round(sharpe, 3),
        "expectancy":    round(expectancy, 3),
    }

# ══ Data Layer ══════════════════════════════════════════════════

def _build_synthetic_df(n: int = 600, seed: int = 42) -> pd.DataFrame:
    """Generate realistic OHLCV data for offline testing."""
    rng = np.random.default_rng(seed)
    price = 1000.0
    rows = []
    base_time = int(datetime(2025, 1, 1).timestamp())
    for i in range(n):
        ret    = rng.normal(0.0002, 0.008)
        price  = max(price * (1 + ret), 10.0)
        noise  = price * 0.005
        o      = price + rng.uniform(-noise, noise)
        h      = max(o, price) + abs(rng.normal(0, noise))
        l      = min(o, price) - abs(rng.normal(0, noise))
        c      = price
        vol    = int(abs(rng.normal(500_000, 200_000)))
        rows.append({"time": base_time + i * 60, "open": o, "high": h,
                     "low": l, "close": c, "volume": vol})
        price = c
    return pd.DataFrame(rows)

_candle_cache: dict[str, list[dict]] = {}

def fetch_candles_api(instrument_key: str, configuration) -> Optional[pd.DataFrame]:
    """Fetch 20-day 1-minute candles from Upstox API."""
    try:
        import upstox_client
        api_client  = upstox_client.ApiClient(configuration)
        history_api = upstox_client.HistoryApi(api_client)

        from_date = (datetime.now() - timedelta(days=20)).strftime('%Y-%m-%d')
        to_date   = datetime.now().strftime('%Y-%m-%d')

        res = history_api.get_historical_candle_data1(
            instrument_key, "1minute", to_date, from_date, "2.0"
        )
        data = res.to_dict()
        raw = data.get("data", {}).get("candles", [])
        if not raw:
            return None

        rows = []
        for c in raw:
            try:
                rows.append({
                    "time":   c[0],
                    "open":   float(c[1]),
                    "high":   float(c[2]),
                    "low":    float(c[3]),
                    "close":  float(c[4]),
                    "volume": int(c[5]) if len(c) > 5 else 0,
                })
            except Exception:
                continue

        if not rows:
            return None

        df = pd.DataFrame(rows)
        df["time"] = pd.to_datetime(df["time"])
        df = df.sort_values("time").reset_index(drop=True)
        return df

    except Exception as e:
        logger.debug(f"API fetch failed for {instrument_key}: {e}")
        return None

def load_instrument_keys(max_instruments: int = 50) -> list[tuple[str, str]]:
    """Load (instrument_key, symbol) pairs from Nifty 500 + instrument list."""
    root = Path(__file__).parent.parent

    n500_csv  = root / "ind_nifty500list.csv"
    inst_csv  = root / "instrument_list.csv"

    if not n500_csv.exists() or not inst_csv.exists():
        logger.warning("CSV files not found — using fallback 5 symbols for offline test")
        return [
            ("NSE_EQ|INE002A01018", "RELIANCE"),
            ("NSE_EQ|INE040A01034", "TCS"),
            ("NSE_EQ|INE009A01021", "INFOSYS"),
            ("NSE_EQ|INE062A01020", "HDFCBANK"),
            ("NSE_EQ|INE030A01027", "ICICIBANK"),
        ][:max_instruments]

    n500_isin: dict[str, str] = {}
    with open(n500_csv) as f:
        for row in csv.DictReader(f):
            n500_isin[row["ISIN Code"]] = row["Symbol"]

    pairs = []
    with open(inst_csv) as f:
        for row in csv.DictReader(f):
            key  = row.get("instrument_key", "")
            isin = key.split("|")[-1] if "|" in key else ""
            if isin in n500_isin and row.get("exchange") == "NSE_EQ":
                pairs.append((key, n500_isin[isin]))

    pairs.sort(key=lambda x: x[1])
    return pairs[:max_instruments]

# ══ Core Backtest ═══════════════════════════════════════════════

def backtest_single(df: pd.DataFrame, p: dict) -> dict:
    """Run backtest for one instrument + one param set."""
    if len(df) < MIN_BARS + p["slow_ema"] + 5:
        return {"n_trades": 0, "win_rate": 0.0, "avg_ret": 0.0,
                "profit_factor": 0.0, "max_drawdown": 0.0, "sharpe": 0.0,
                "expectancy": 0.0}

    sig_df  = generate_signals(df, p)
    trades  = simulate_trades(sig_df)
    return compute_stats(trades)

def aggregate_stats(all_per_instrument: list[dict]) -> dict:
    """Merge per-instrument stats into a combined view."""
    total_trades = sum(s["n_trades"] for s in all_per_instrument)
    if total_trades == 0:
        return {
            "total_trades": 0, "win_rate": 0.0, "avg_ret": 0.0,
            "profit_factor": 0.0, "max_drawdown": 0.0, "sharpe": 0.0,
            "expectancy": 0.0, "instruments_with_trades": 0,
        }

    weighted_wr     = sum(s["win_rate"]      * s["n_trades"] for s in all_per_instrument) / total_trades
    weighted_avgret = sum(s["avg_ret"]       * s["n_trades"] for s in all_per_instrument) / total_trades
    weighted_pf     = sum(s["profit_factor"] * s["n_trades"] for s in all_per_instrument) / total_trades
    max_dd          = max(s["max_drawdown"] for s in all_per_instrument)
    avg_sharpe      = np.mean([s["sharpe"] for s in all_per_instrument if s["n_trades"] > 0])
    expectancy      = sum(s["expectancy"] * s["n_trades"] for s in all_per_instrument) / total_trades

    return {
        "total_trades":          total_trades,
        "win_rate":              round(weighted_wr, 2),
        "avg_ret":               round(weighted_avgret, 3),
        "profit_factor":         round(weighted_pf, 3),
        "max_drawdown":          round(max_dd, 3),
        "sharpe":                round(float(avg_sharpe), 3),
        "expectancy":            round(expectancy, 3),
        "instruments_with_trades": sum(1 for s in all_per_instrument if s["n_trades"] > 0),
    }

def walk_forward_test(
    df: pd.DataFrame, p: dict,
    n_splits: int = 3,
) -> dict:
    """
    Walk-forward validation: train on first 60%, validate next 20%, test last 20%.
    Returns stats for each fold + final test-set stats.
    """
    n = len(df)
    fold_size = n // (n_splits + 1)
    folds = []
    for k in range(n_splits):
        start = k * fold_size
        end   = (k + 2) * fold_size
        fold_df = df.iloc[start:end].reset_index(drop=True)
        folds.append(compute_stats(simulate_trades(generate_signals(fold_df, p))))

    # Test set (last 20%)
    test_df  = df.iloc[int(n * 0.8):].reset_index(drop=True)
    test_st  = compute_stats(simulate_trades(generate_signals(test_df, p)))

    wf_win_rates = [f["win_rate"] for f in folds if f["n_trades"] > 0]
    return {
        "test": test_st,
        "folds": folds,
        "wf_consistency": round(float(np.std(wf_win_rates)), 2) if wf_win_rates else 0.0,
    }

# ══ Sweep Runner ════════════════════════════════════════════════

def param_key(p: dict) -> str:
    return (
        f"fe={p['fast_ema']:2d} se={p['slow_ema']:2d} "
        f"rsi_b={p['rsi_buy_thresh']:2d} "
        f"sl={p['sl_atr_multiplier']:.2f} tp={p['tp_atr_multiplier']:.2f}"
    )

def build_param_combos(
    quick: bool = False,
    max_combos: int = 0,
) -> list[dict]:
    """Build parameter combinations with optional quick profile and cap."""
    grid = QUICK_PARAM_GRID if quick else PARAM_GRID
    keys, vals = zip(*grid.items())
    combos = [dict(zip(keys, combo)) for combo in itertools.product(*vals)]
    combos = [c for c in combos if c["tp_atr_multiplier"] >= c["sl_atr_multiplier"]]
    if max_combos and max_combos > 0:
        combos = combos[:max_combos]
    return combos

def run_full_sweep(
    dataframes: dict[str, pd.DataFrame],
    walk_forward: bool = False,
    quick: bool = False,
    max_combos: int = 0,
    refine_top_k: int = 0,
) -> list[dict]:
    """
    Grid search across all param combos × all instruments.
    Returns sorted list of result dicts.
    """
    combos = build_param_combos(quick=quick, max_combos=max_combos)

    logger.info(f"🔬 Sweeping {len(combos)} param combos across {len(dataframes)} instruments")

    results = []
    total = len(combos)

    for idx, p in enumerate(combos):
        per_inst = []
        for sym, df in dataframes.items():
            st = backtest_single(df, p)
            per_inst.append(st)

        agg = aggregate_stats(per_inst)

        # Optional walk-forward on first available instrument
        wf_std = 0.0
        if walk_forward and dataframes:
            first_df = next(iter(dataframes.values()))
            wf = walk_forward_test(first_df, p)
            wf_std = wf["wf_consistency"]

        results.append({
            "params": p,
            "pk": param_key(p),
            **agg,
            "wf_std": wf_std,
        })

        if (idx + 1) % 20 == 0 or idx + 1 == total:
            best_so_far = max(results, key=lambda r: r["win_rate"])
            logger.info(
                f"  [{idx+1}/{total}] best win_rate={best_so_far['win_rate']:.1f}% "
                f"pf={best_so_far['profit_factor']:.2f}"
            )

    # Sort by win_rate desc, then profit_factor desc
    results.sort(key=lambda r: (-r["win_rate"], -r["profit_factor"]))

    # Optional stage-2 refinement: evaluate full grid only for top-K coarse candidates.
    if refine_top_k and refine_top_k > 0 and quick:
        logger.info(f"🧭 Refining top {refine_top_k} quick-mode candidates on full grid neighbors")
        top = results[:refine_top_k]
        full_combos = build_param_combos(quick=False, max_combos=0)

        # Keep only full-grid combos near the top-K by sl/tp/ema/rsi buy threshold.
        shortlisted = []
        for c in full_combos:
            for t in top:
                p = t["params"]
                if (
                    c["fast_ema"] == p["fast_ema"]
                    and abs(c["sl_atr_multiplier"] - p["sl_atr_multiplier"]) <= 0.5
                    and abs(c["tp_atr_multiplier"] - p["tp_atr_multiplier"]) <= 0.5
                    and abs(c["rsi_buy_thresh"] - p["rsi_buy_thresh"]) <= 5
                ):
                    shortlisted.append(c)
                    break

        # Deduplicate while preserving order.
        dedup = []
        seen = set()
        for c in shortlisted:
            k = tuple(sorted(c.items()))
            if k in seen:
                continue
            seen.add(k)
            dedup.append(c)

        if dedup:
            logger.info(f"🔬 Refinement sweep: {len(dedup)} shortlisted combos")
            refined = []
            for i, p in enumerate(dedup):
                per_inst = []
                for _, df in dataframes.items():
                    per_inst.append(backtest_single(df, p))
                agg = aggregate_stats(per_inst)
                refined.append({"params": p, "pk": param_key(p), **agg, "wf_std": 0.0})
                if (i + 1) % 20 == 0 or i + 1 == len(dedup):
                    best_so_far = max(refined, key=lambda r: r["win_rate"])
                    logger.info(
                        f"  [refine {i+1}/{len(dedup)}] best win_rate={best_so_far['win_rate']:.1f}% "
                        f"pf={best_so_far['profit_factor']:.2f}"
                    )
            refined.sort(key=lambda r: (-r["win_rate"], -r["profit_factor"]))
            results = refined

    return results

# ══ Reporting ════════════════════════════════════════════════════

HEADER = (
    f"{'PARAMS':<55} {'TRADES':>7} {'WIN%':>7} {'AVG_RET':>8} "
    f"{'PF':>6} {'SHARPE':>7} {'EXPECTANCY':>11} {'MAX_DD':>8}"
)
ROW_FMT = (
    "{:<55} {:>7} {:>7.1f}% {:>8.3f}% "
    "{:>6.2f} {:>7.2f} {:>10.3f}% {:>8.2f}%"
)

def print_report(results: list[dict], top_n: int = 30):
    """Print a ranked table of parameter sets."""
    print("\n" + "=" * 120)
    print("  SCALP PRO — NIFTY 500 BACKTEST RESULTS")
    print("=" * 120)
    print(HEADER)
    print("-" * 120)

    for r in results[:top_n]:
        p   = r["params"]
        tag = "✅" if r["win_rate"] >= 90 else "🟡" if r["win_rate"] >= 75 else "  "
        row = ROW_FMT.format(
            r["pk"],
            r["total_trades"],
            r["win_rate"],
            r["avg_ret"],
            r["profit_factor"],
            r["sharpe"],
            r["expectancy"],
            r["max_drawdown"],
        )
        print(f"{tag} {row}")

    print("-" * 120)

    # Summary: show parameter ranges for top combos
    best = [r for r in results if r["win_rate"] >= 75] or results[:10]
    if best:
        print("\n📊 RECOMMENDED PARAMETER RANGES (≥75% WIN RATE OR TOP-10):")
        print(f"  sl_atr_multiplier : {min(r['params']['sl_atr_multiplier'] for r in best):.2f} – "
              f"{max(r['params']['sl_atr_multiplier'] for r in best):.2f}")
        print(f"  tp_atr_multiplier : {min(r['params']['tp_atr_multiplier'] for r in best):.2f} – "
              f"{max(r['params']['tp_atr_multiplier'] for r in best):.2f}")
        print(f"  fast_ema          : {sorted(set(r['params']['fast_ema'] for r in best))}")
        print(f"  rsi_buy_thresh    : {sorted(set(r['params']['rsi_buy_thresh'] for r in best))}")
        print(f"  NOTE: Set partial_tp1_atr_mult = sl_atr_multiplier × 1.0  (book 40% fast)")
        print(f"        Set partial_tp2_atr_mult = tp_atr_multiplier × 0.5   (book 40% mid)")
        print(f"        Set tp_atr_multiplier   = best range above           (hold 20% for run)")

    # Best single set
    if results:
        best1 = results[0]
        bp    = best1["params"]
        print(f"\n🏆 BEST SINGLE PARAM SET:")
        print(f"   fast_ema:          {bp['fast_ema']}")
        print(f"   slow_ema:          {bp['slow_ema']}")
        print(f"   rsi_buy_thresh:    {bp['rsi_buy_thresh']}")
        print(f"   rsi_sell_thresh:   {bp['rsi_sell_thresh']}")
        print(f"   sl_atr_multiplier: {bp['sl_atr_multiplier']}")
        print(f"   tp_atr_multiplier: {bp['tp_atr_multiplier']}")
        print(f"   → Win Rate:        {best1['win_rate']:.2f}%")
        print(f"   → Profit Factor:   {best1['profit_factor']:.2f}x")
        print(f"   → Expectancy:      {best1['expectancy']:.3f}% / trade")
        print(f"   → Sharpe:          {best1['sharpe']:.2f}")
        print()
        print("⚙️  PARTIAL BOOKING STRATEGY (recommended for above params):")
        sl_m  = bp["sl_atr_multiplier"]
        tp_m  = bp["tp_atr_multiplier"]
        tp1_m = min(sl_m, tp_m)
        tp2_m = tp1_m + (tp_m - tp1_m) * 0.6
        print(f"   TP1 @ {tp1_m:.2f}× ATR  → book 40% of position  (fast lock-in)")
        print(f"   TP2 @ {tp2_m:.2f}× ATR  → book 40% of position  (mid profit)")
        print(f"   TP3 @ {tp_m:.2f}× ATR  → hold 20%, trail SL at breakeven")
        print(f"   Trailing SL trail distance = {sl_m:.2f}× ATR")

    print("=" * 120)
    print("⚠️  Past performance ≠ future results. Use this only for research.")
    print("   Always test with paper trading before live deployment.\n")

def save_results_csv(results: list[dict], path: str):
    """Save sweep results to CSV."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    flat = []
    for r in results:
        row = {**{f"p_{k}": v for k, v in r["params"].items()}}
        row.update({k: v for k, v in r.items() if k not in ("params", "pk")})
        flat.append(row)

    df = pd.DataFrame(flat)
    df.to_csv(path, index=False)
    logger.info(f"💾 Results saved to {path}")

# ══ Main ═════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="ScalpPro Backtester")
    parser.add_argument("--mode", choices=["offline", "api"], default="offline",
                        help="offline=synthetic data, api=Upstox live fetch")
    parser.add_argument("--max-instruments", type=int, default=20,
                        help="Cap number of instruments (default 20 for speed)")
    parser.add_argument("--walk-forward", action="store_true",
                        help="Enable walk-forward fold analysis")
    parser.add_argument("--top-n", type=int, default=25,
                        help="Number of rows to show in results table")
    parser.add_argument("--out", type=str, default=None,
                        help="Optional CSV path to save full results")
    parser.add_argument("--quick", action="store_true",
                        help="Use reduced parameter grid for much faster sweeps")
    parser.add_argument("--max-combos", type=int, default=0,
                        help="Cap number of parameter combos (0 = no cap)")
    parser.add_argument("--refine-top-k", type=int, default=0,
                        help="After --quick, refine top-K candidates on nearby full-grid combos")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # ── Load configuration (only needed for api mode) ──────────
    configuration = None
    if args.mode == "api":
        try:
            from dotenv import load_dotenv
            load_dotenv()
            from app.auth.service import get_auth_service
            auth = get_auth_service()
            configuration = auth.get_configuration(use_sandbox=False)
            logger.info("✅ Upstox API configuration loaded")
        except Exception as e:
            logger.warning(f"Could not init Upstox API ({e}). Falling back to offline mode.")
            args.mode = "offline"

    # ── Load instruments ────────────────────────────────────────
    instrument_pairs = load_instrument_keys(max_instruments=args.max_instruments)
    logger.info(f"📋 Loaded {len(instrument_pairs)} Nifty-500 instruments")

    # ── Fetch / generate data ───────────────────────────────────
    dataframes: dict[str, pd.DataFrame] = {}

    if args.mode == "api" and configuration:
        logger.info("📡 Fetching historical data from Upstox API ...")
        batch_delay = 0.05  # seconds between requests (rate limiting buffer)

        def _fetch(pair):
            key, sym = pair
            time.sleep(batch_delay)
            df = fetch_candles_api(key, configuration)
            return sym, df

        with ThreadPoolExecutor(max_workers=4) as ex:
            futures = {ex.submit(_fetch, pair): pair for pair in instrument_pairs}
            done, total = 0, len(futures)
            for fut in as_completed(futures):
                sym, df = fut.result()
                done += 1
                if df is not None and len(df) >= MIN_BARS + 30:
                    dataframes[sym] = df
                    logger.debug(f"  ✔ {sym}: {len(df)} bars")
                else:
                    logger.debug(f"  ✘ {sym}: insufficient data")
                if done % 10 == 0:
                    logger.info(f"  Fetched {done}/{total} ({len(dataframes)} usable)")

        logger.info(f"📊 Usable instruments: {len(dataframes)}/{len(instrument_pairs)}")

    else:
        logger.info("🧪 Offline mode: generating synthetic OHLCV data for each instrument ...")
        for idx, (key, sym) in enumerate(instrument_pairs):
            # Different seed per instrument gives realistic diversity
            df = _build_synthetic_df(n=800, seed=idx * 97 + 13)
            dataframes[sym] = df
        logger.info(f"  Generated {len(dataframes)} synthetic datasets")

    if not dataframes:
        logger.error("❌ No data available. Exiting.")
        sys.exit(1)

    # ── Run sweep ───────────────────────────────────────────────
    t0 = time.time()
    results = run_full_sweep(
        dataframes,
        walk_forward=args.walk_forward,
        quick=args.quick,
        max_combos=args.max_combos,
        refine_top_k=args.refine_top_k,
    )
    elapsed = time.time() - t0
    logger.info(f"⏱️  Sweep completed in {elapsed:.1f}s")

    # ── Report ──────────────────────────────────────────────────
    print_report(results, top_n=args.top_n)

    if args.out:
        save_results_csv(results, args.out)

if __name__ == "__main__":
    main()
