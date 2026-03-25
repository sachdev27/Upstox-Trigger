"""
Technical Indicators — pure functions operating on pandas DataFrames / Series.

All indicators used by the SuperTrend Pro v6.3 strategy and more.
Faithfully ported from Pine Script (TradingView) to Python.
"""

import numpy as np
import pandas as pd


# ── ATR (Average True Range) ───────────────────────────────────

def true_range(df: pd.DataFrame) -> pd.Series:
    """Calculate True Range."""
    high = df["high"]
    low = df["low"]
    prev_close = df["close"].shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    return pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)


def atr(df: pd.DataFrame, period: int = 14, use_rma: bool = True) -> pd.Series:
    """
    Average True Range.
    use_rma=True uses RMA (Wilder's smoothing) like Pine's ta.atr().
    use_rma=False uses SMA like Pine's ta.sma(ta.tr, period).
    """
    tr = true_range(df)
    if use_rma:
        return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    else:
        return tr.rolling(window=period, min_periods=period).mean()


# ── SuperTrend ──────────────────────────────────────────────────

def supertrend(
    df: pd.DataFrame,
    period: int = 10,
    multiplier: float = 3.0,
    use_rma: bool = True,
) -> pd.DataFrame:
    """
    Calculate SuperTrend indicator.

    Returns DataFrame with columns:
        - supertrend: the SuperTrend value
        - trend: 1 (bullish) or -1 (bearish)
        - upper_band: upper band value
        - lower_band: lower band value
    """
    hl2 = (df["high"] + df["low"]) / 2
    atr_vals = atr(df, period, use_rma)

    upper_band = hl2 + multiplier * atr_vals
    lower_band = hl2 - multiplier * atr_vals

    n = len(df)
    trend = np.ones(n, dtype=int)
    final_upper = np.full(n, np.nan)
    final_lower = np.full(n, np.nan)
    st_values = np.full(n, np.nan)

    close = df["close"].values
    ub = upper_band.values
    lb = lower_band.values

    final_upper[0] = ub[0]
    final_lower[0] = lb[0]

    for i in range(1, n):
        # Lower band (support) — can only ratchet up
        if close[i - 1] > final_lower[i - 1]:
            final_lower[i] = max(lb[i], final_lower[i - 1])
        else:
            final_lower[i] = lb[i]

        # Upper band (resistance) — can only ratchet down
        if close[i - 1] < final_upper[i - 1]:
            final_upper[i] = min(ub[i], final_upper[i - 1])
        else:
            final_upper[i] = ub[i]

        # Trend direction
        prev_trend = trend[i - 1]
        if prev_trend == -1 and close[i] > final_upper[i - 1]:
            trend[i] = 1
        elif prev_trend == 1 and close[i] < final_lower[i - 1]:
            trend[i] = -1
        else:
            trend[i] = prev_trend

    # SuperTrend value
    for i in range(n):
        st_values[i] = final_lower[i] if trend[i] == 1 else final_upper[i]

    result = pd.DataFrame(
        {
            "supertrend": st_values,
            "trend": trend,
            "upper_band": final_upper,
            "lower_band": final_lower,
        },
        index=df.index,
    )
    return result


# ── ADX (Average Directional Index) ────────────────────────────

def adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """
    Calculate ADX, +DI, -DI.

    Returns DataFrame with columns: adx, plus_di, minus_di
    """
    high = df["high"]
    low = df["low"]
    close = df["close"]

    # Directional Movement
    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
        index=df.index,
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
        index=df.index,
    )

    tr = true_range(df)

    # Smoothed with Wilder's RMA
    alpha = 1.0 / period
    smoothed_tr = tr.ewm(alpha=alpha, min_periods=period, adjust=False).mean()
    smoothed_plus = plus_dm.ewm(alpha=alpha, min_periods=period, adjust=False).mean()
    smoothed_minus = minus_dm.ewm(alpha=alpha, min_periods=period, adjust=False).mean()

    plus_di = 100 * smoothed_plus / smoothed_tr
    minus_di = 100 * smoothed_minus / smoothed_tr

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx_val = dx.ewm(alpha=alpha, min_periods=period, adjust=False).mean()

    return pd.DataFrame(
        {"adx": adx_val, "plus_di": plus_di, "minus_di": minus_di},
        index=df.index,
    )


# ── Rate of Change (ROC) ───────────────────────────────────────

def roc(series: pd.Series, period: int = 3) -> pd.Series:
    """Rate of Change as percentage."""
    return series.pct_change(periods=period) * 100


# ── Bollinger Bands ──────────────────────────────────────────────

def bollinger_bands(
    series: pd.Series, period: int = 20, std_mult: float = 2.0
) -> pd.DataFrame:
    """
    Bollinger Bands.

    Returns DataFrame with columns: basis, upper, lower, width
    """
    basis = series.rolling(window=period).mean()
    std = series.rolling(window=period).std()
    upper = basis + std_mult * std
    lower = basis - std_mult * std
    width = upper - lower

    return pd.DataFrame(
        {"basis": basis, "upper": upper, "lower": lower, "width": width},
        index=series.index,
    )


def bb_squeeze(
    series: pd.Series,
    bb_period: int = 20,
    bb_mult: float = 2.0,
    squeeze_lookback: int = 10,
) -> pd.DataFrame:
    """
    Bollinger Band Squeeze detection.

    Returns DataFrame with columns:
        - in_squeeze: True if BB width is at minimum
        - expanding: True if BB width is growing
        - was_squeezing: True if recently came out of squeeze
        - squeeze_breakout: True if transitioning from squeeze to expansion
    """
    bb = bollinger_bands(series, bb_period, bb_mult)
    min_width = bb["width"].rolling(window=squeeze_lookback).min()

    in_squeeze = bb["width"] <= min_width * 1.05
    expanding = bb["width"] > bb["width"].shift(1)

    # Track squeeze state
    was_squeezing = pd.Series(False, index=series.index)
    for i in range(1, len(series)):
        if in_squeeze.iloc[i]:
            was_squeezing.iloc[i] = True
        elif expanding.iloc[i]:
            was_squeezing.iloc[i] = False
        else:
            was_squeezing.iloc[i] = was_squeezing.iloc[i - 1]

    squeeze_breakout = was_squeezing & expanding

    return pd.DataFrame(
        {
            "in_squeeze": in_squeeze,
            "expanding": expanding,
            "was_squeezing": was_squeezing,
            "squeeze_breakout": squeeze_breakout,
        },
        index=series.index,
    )


# ── Volume Surge ─────────────────────────────────────────────────

def volume_surge(
    volume: pd.Series, lookback: int = 20, multiplier: float = 1.4
) -> pd.Series:
    """Check if volume exceeds average by the given multiplier. Returns boolean Series."""
    avg_vol = volume.rolling(window=lookback).mean()
    return volume > avg_vol * multiplier


# ── ATR Percentile ───────────────────────────────────────────────

def atr_percentile(
    atr_series: pd.Series, lookback: int = 100
) -> pd.Series:
    """
    Compute where ATR sits within its recent range (0-100%).
    High percentile = volatility expanding.
    """
    atr_high = atr_series.rolling(window=lookback).max()
    atr_low = atr_series.rolling(window=lookback).min()
    atr_range = atr_high - atr_low
    return np.where(
        atr_range > 0,
        (atr_series - atr_low) / atr_range * 100,
        50.0,
    )


# ── Consecutive Bars ────────────────────────────────────────────

def consecutive_confirming_bars(
    trend: pd.Series, close: pd.Series
) -> pd.Series:
    """
    Count consecutive bars that confirm the current trend direction.
    Bullish trend: close > previous close.
    Bearish trend: close < previous close.
    Resets on trend change.
    """
    n = len(trend)
    counts = np.zeros(n, dtype=int)

    for i in range(1, n):
        if trend.iloc[i] != trend.iloc[i - 1]:
            counts[i] = 1
        else:
            if trend.iloc[i] == 1 and close.iloc[i] > close.iloc[i - 1]:
                counts[i] = counts[i - 1] + 1
            elif trend.iloc[i] == -1 and close.iloc[i] < close.iloc[i - 1]:
                counts[i] = counts[i - 1] + 1
            else:
                counts[i] = counts[i - 1]

    return pd.Series(counts, index=trend.index)
