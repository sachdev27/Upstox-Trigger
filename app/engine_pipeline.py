import logging
import asyncio
import re
from abc import ABC, abstractmethod
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING
from app.config import get_settings
from app.notifications.manager import get_notification_manager
from app.database.connection import get_session, TradeLog
from app.orders.models import TransactionType

if TYPE_CHECKING:
    from app.engine import AutomationEngine
    from app.orders.models import TradeSignal
    from app.strategies.base import StrategyConfig

logger = logging.getLogger(__name__)
IST = timezone(timedelta(hours=5, minutes=30))

class SignalProcessor(ABC):
    """Abstract base class for signal processing steps."""
    @abstractmethod
    async def process(self, signal: 'TradeSignal', config: 'StrategyConfig', engine: 'AutomationEngine') -> bool:
        """Process a signal. Returns False to halt the pipeline."""
        pass

class RiskGuardProcessor(SignalProcessor):
    """Checks for trading side restrictions and daily loss limits."""
    async def process(self, signal: 'TradeSignal', config: 'StrategyConfig', engine: 'AutomationEngine') -> bool:
        is_manual_test = (config.name == "Test" and signal.strategy_name == "Manual Test")

        # Trading Side check
        if engine.trading_side == "LONG_ONLY" and signal.action.value == "SELL":
            logger.info("🚫 SHORT signal skipped (LONG_ONLY mode)")
            return False
        if engine.trading_side == "SHORT_ONLY" and signal.action.value == "BUY":
            logger.info("🚫 LONG signal skipped (SHORT_ONLY mode)")
            return False

        # Daily Loss check — skip for manual test signals
        if is_manual_test:
            logger.info("🧪 Manual test signal — bypassing daily loss guard")
        else:
            max_loss_abs = engine.trading_capital * (engine.max_daily_loss_pct / 100)
            if engine._daily_pnl <= -max_loss_abs:
                logger.warning(
                    f"🛑 MAX DAILY LOSS HIT ({-engine._daily_pnl:.2f} >= {max_loss_abs:.2f}). "
                    f"Blocking {signal.action.value} on {signal.instrument_key}."
                )
                engine.auto_mode = False
                return False
        return True


class OptionChainInsightProcessor(SignalProcessor):
    """
    Enrich signals with real-time option chain analysis (PCR, OI, IV, Max-Pain).

    Runs BEFORE ATMResolver so the underlying index chain is analyzed
    before the signal is converted to a specific option contract.

    Behaviour:
    - Fetches option chain for the signal's underlying (index or equity)
    - Computes directional_score (-100 to +100)
    - Adds OC insights to signal.metadata["oc_analysis"]
    - Adjusts confidence_score: boost if aligned, penalty if contradicted
    - Blocks the signal entirely if strategy param "oc_block_contradictions" is set
      and the chain strongly contradicts the trade direction.
    """

    # Cache chain analysis for 30s to avoid redundant API calls within the same cycle
    _cache: dict[str, tuple[float, dict]] = {}
    _cache_ttl = 30.0

    async def process(self, signal: 'TradeSignal', config: 'StrategyConfig', engine: 'AutomationEngine') -> bool:
        p = config.params or {}
        if not p.get("use_oc_insight", False):
            return True  # Feature disabled — pass through

        if not engine._market_service:
            return True  # No market service — skip silently

        # Determine which underlying to analyze
        underlying = signal.instrument_key
        # If it's already an option contract, try to find the underlying
        if "FO" in underlying:
            underlying = signal.metadata.get("underlying", underlying)

        # Only analyze indices (equities don't have liquid enough chains for real-time insight)
        if "INDEX" not in underlying:
            return True

        import time
        from app.market_data.option_analysis import analyze_option_chain

        # Check cache
        now = time.monotonic()
        cached = self._cache.get(underlying)
        if cached and (now - cached[0]) < self._cache_ttl:
            analysis = cached[1]
        else:
            try:
                chain_data = await engine._market_service.get_detailed_option_chain(underlying)
                if chain_data.get("status") != "success" or not chain_data.get("chain"):
                    logger.debug(f"OC Insight: no chain data for {underlying}")
                    return True

                analysis = analyze_option_chain(
                    chain_data["chain"],
                    float(chain_data.get("spot_price") or signal.price or 0),
                )
                self._cache[underlying] = (now, analysis)
            except Exception as e:
                logger.warning(f"OC Insight fetch failed for {underlying}: {e}")
                return True  # Don't block on failure

        # Attach analysis to signal metadata
        signal.metadata["oc_analysis"] = {
            "sentiment": analysis["sentiment"],
            "directional_score": analysis["directional_score"],
            "pcr_oi": analysis["pcr"]["pcr_oi"],
            "max_pain": analysis["max_pain"]["max_pain_strike"],
            "immediate_support": analysis["oi_concentration"]["immediate_support"],
            "immediate_resistance": analysis["oi_concentration"]["immediate_resistance"],
            "iv_skew_bias": analysis["iv_skew"]["skew_bias"],
            "oi_bias": analysis["oi_buildup"]["oi_bias"],
            "signals": analysis["signals"],
        }

        ds = analysis["directional_score"]
        is_buy = signal.action.value == "BUY"

        # ── Confidence adjustment ────────────────────────────────
        oc_boost = int(p.get("oc_confidence_boost", 10))     # points added when aligned
        oc_penalty = int(p.get("oc_confidence_penalty", 15))  # points removed when contradicted

        if (is_buy and ds >= 30) or (not is_buy and ds <= -30):
            # OC aligns with signal direction → boost
            signal.confidence_score = min(100, signal.confidence_score + oc_boost)
            logger.info(
                f"📊 OC Insight ALIGNED: {analysis['sentiment']} (score={ds}) "
                f"→ confidence boosted to {signal.confidence_score}"
            )
        elif (is_buy and ds <= -30) or (not is_buy and ds >= 30):
            # OC contradicts signal direction → penalize
            signal.confidence_score = max(0, signal.confidence_score - oc_penalty)
            logger.info(
                f"📊 OC Insight CONTRADICTS: {analysis['sentiment']} (score={ds}) "
                f"→ confidence reduced to {signal.confidence_score}"
            )

            # Block if configured and contradiction is strong
            block_threshold = int(p.get("oc_block_threshold", 60))
            if p.get("oc_block_contradictions", False) and abs(ds) >= block_threshold:
                logger.warning(
                    f"🚫 OC BLOCK: {signal.action.value} {signal.instrument_key} "
                    f"blocked by option chain sentiment ({analysis['sentiment']}, score={ds})"
                )
                return False
        else:
            logger.info(
                f"📊 OC Insight NEUTRAL: score={ds} — no adjustment"
            )

        return True


class ATMResolverProcessor(SignalProcessor):
    """Resolves index instruments to their closest ATM option contract."""

    @staticmethod
    def _to_float(value, default: float = 0.0) -> float:
        try:
            if value is None:
                return default
            return float(value)
        except Exception:
            return default

    def _option_liquidity_score(self, row: dict, opt_side: str, target_strike: float, step_size: float, distance_penalty: float) -> tuple[float, dict]:
        strike = self._to_float(row.get("strike_price"), target_strike)
        option_leg = row.get(opt_side) or {}
        ltp = self._to_float(option_leg.get("ltp"), 0.0)
        oi = self._to_float(option_leg.get("oi"), 0.0)
        volume = self._to_float(option_leg.get("volume"), 0.0)
        bid = self._to_float(option_leg.get("bid_price") or option_leg.get("bid") or option_leg.get("best_bid"), 0.0)
        ask = self._to_float(option_leg.get("ask_price") or option_leg.get("ask") or option_leg.get("best_ask"), 0.0)

        spread_pct = 999.0
        if bid > 0 and ask > 0 and ask >= bid:
            mid = (bid + ask) / 2.0
            spread_pct = ((ask - bid) / mid) * 100 if mid > 0 else 999.0

        strike_distance_steps = abs(strike - target_strike) / max(step_size, 1.0)

        score = 0.0
        score += min(30.0, volume / 80.0)
        score += min(30.0, oi / 1200.0)
        score += min(20.0, ltp / 10.0)
        score += 20.0 if spread_pct <= 1.0 else (12.0 if spread_pct <= 2.0 else (6.0 if spread_pct <= 4.0 else 0.0))
        score -= strike_distance_steps * max(distance_penalty, 0.0)

        return score, {
            "strike": strike,
            "ltp": ltp,
            "oi": oi,
            "volume": volume,
            "bid": bid,
            "ask": ask,
            "spread_pct": round(spread_pct, 4) if spread_pct < 999 else None,
            "distance_steps": round(strike_distance_steps, 3),
        }

    async def process(self, signal: 'TradeSignal', config: 'StrategyConfig', engine: 'AutomationEngine') -> bool:
        if ("INDEX" in signal.instrument_key or signal.instrument_key in ["NSE_INDEX|Nifty 50", "NSE_INDEX|Nifty Bank"]) and engine._market_service:
            try:
                logger.info(f"🔍 Resolving ATM option for {signal.instrument_key} @ {signal.price}")
                p = config.params or {}
                expiry_mode = str(p.get("option_expiry_mode", "current")).lower()
                moneyness_steps = int(p.get("option_moneyness_steps", 0) or 0)
                buy_only = bool(p.get("option_buy_only", True))
                liquidity_filter = bool(p.get("option_liquidity_filter", True))
                liquidity_window_steps = int(p.get("option_liquidity_window_steps", 2) or 2)
                distance_penalty = float(p.get("option_distance_penalty", 5.0) or 5.0)

                # Initial fetch gets spot + available expiries.
                chain_data = await engine._market_service.get_detailed_option_chain(signal.instrument_key)
                if chain_data["status"] == "success" and chain_data["chain"]:
                    available_expiries = chain_data.get("available_expiries", [])
                    selected_expiry = chain_data.get("expiry_date")
                    if available_expiries:
                        if expiry_mode == "next" and len(available_expiries) > 1:
                            selected_expiry = available_expiries[1]
                        else:
                            selected_expiry = available_expiries[0]

                    # Refetch for selected expiry (if different from default response)
                    if selected_expiry and selected_expiry != chain_data.get("expiry_date"):
                        refetch = await engine._market_service.get_detailed_option_chain(
                            signal.instrument_key,
                            expiry_date=selected_expiry,
                        )
                        if refetch.get("status") == "success" and refetch.get("chain"):
                            chain_data = refetch

                    matrix = chain_data["chain"]
                    spot = float(chain_data.get("spot_price") or signal.price or 0.0)
                    atm_row = min(matrix, key=lambda x: abs(float(x["strike_price"]) - spot))

                    # Infer strike step from chain (e.g., 50 for Nifty, 100 for BankNifty)
                    strikes = sorted({float(r["strike_price"]) for r in matrix})
                    step_size = min(
                        [b - a for a, b in zip(strikes, strikes[1:]) if (b - a) > 0],
                        default=50.0,
                    )

                    # BUY signal => CE; SELL signal => PE (directional long options)
                    opt_side = "ce" if signal.action.value == "BUY" else "pe"
                    target_strike = float(atm_row["strike_price"])
                    if moneyness_steps > 0:
                        if opt_side == "ce":
                            target_strike += moneyness_steps * step_size
                        else:
                            target_strike -= moneyness_steps * step_size

                    # Pick closest row around target strike that has desired side contract
                    candidate_rows = [r for r in matrix if r.get(opt_side)]
                    if candidate_rows:
                        if liquidity_filter:
                            nearby_rows = [
                                r for r in candidate_rows
                                if abs(self._to_float(r.get("strike_price"), target_strike) - target_strike)
                                <= max(step_size, 1.0) * max(1, liquidity_window_steps)
                            ]
                            if not nearby_rows:
                                nearby_rows = candidate_rows

                            scored_rows = []
                            for row in nearby_rows:
                                score, details = self._option_liquidity_score(
                                    row,
                                    opt_side,
                                    target_strike,
                                    step_size,
                                    distance_penalty,
                                )
                                scored_rows.append((score, row, details))

                            scored_rows.sort(key=lambda x: x[0], reverse=True)
                            best_score, chosen_row, best_details = scored_rows[0]
                            signal.metadata["option_selection"] = {
                                "mode": "liquidity_scored",
                                "best_score": round(best_score, 3),
                                "window_steps": max(1, liquidity_window_steps),
                                "target_strike": target_strike,
                                "picked": best_details,
                            }
                        else:
                            chosen_row = min(candidate_rows, key=lambda r: abs(float(r["strike_price"]) - target_strike))
                    else:
                        chosen_row = atm_row

                    opt = chosen_row.get(opt_side)
                    if opt:
                        # Store original key and update current for execution
                        signal.metadata["underlying"] = signal.instrument_key
                        signal.metadata["option_side"] = opt_side.upper()
                        signal.metadata["expiry_date"] = chain_data.get("expiry_date")
                        signal.metadata["strike_price"] = float(chosen_row.get("strike_price"))
                        signal.metadata["direction_signal"] = signal.action.value
                        signal.instrument_key = opt["instrument_key"]
                        bid = self._to_float(opt.get("bid_price") or opt.get("bid") or opt.get("best_bid"), 0.0)
                        ask = self._to_float(opt.get("ask_price") or opt.get("ask") or opt.get("best_ask"), 0.0)
                        spread_pct = None
                        if bid > 0 and ask > 0 and ask >= bid:
                            mid = (bid + ask) / 2.0
                            spread_pct = ((ask - bid) / mid) * 100 if mid > 0 else None

                        signal.metadata["option_snapshot"] = {
                            "ltp": self._to_float(opt.get("ltp"), 0.0),
                            "oi": self._to_float(opt.get("oi"), 0.0),
                            "volume": self._to_float(opt.get("volume"), 0.0),
                            "bid": bid,
                            "ask": ask,
                            "spread_pct": round(spread_pct, 4) if spread_pct is not None else None,
                        }

                        # ── Recalculate price / SL / TP for the option premium ──
                        option_ltp = float(opt.get("ltp") or 0.0)
                        if option_ltp > 0:
                            old_price = signal.price or spot
                            # Preserve the original risk-reward *ratio* from the
                            # underlying signal and translate it into option-premium
                            # terms.  For long options the SL is a % drop in premium
                            # and TP is a % rise.
                            if old_price > 0 and signal.stop_loss > 0:
                                sl_pct = abs(old_price - signal.stop_loss) / old_price
                            else:
                                sl_pct = 0.30  # default 30% SL on premium

                            if old_price > 0 and signal.take_profit > 0:
                                tp_pct = abs(signal.take_profit - old_price) / old_price
                            else:
                                tp_pct = 0.60  # default 60% TP on premium

                            signal.metadata["underlying_price"] = old_price
                            signal.metadata["underlying_sl"] = signal.stop_loss
                            signal.metadata["underlying_tp"] = signal.take_profit
                            signal.metadata["execution_price_model"] = "option_premium"

                            signal.price = option_ltp
                            signal.stop_loss = round(option_ltp * (1 - sl_pct), 2)
                            signal.take_profit = round(option_ltp * (1 + tp_pct), 2)
                            signal.metadata["premium_risk_distance"] = round(abs(signal.price - signal.stop_loss), 2)
                            logger.info(
                                f"💱 Option price recalc: LTP={option_ltp:.2f}, "
                                f"SL={signal.stop_loss:.2f} (-{sl_pct*100:.0f}%), "
                                f"TP={signal.take_profit:.2f} (+{tp_pct*100:.0f}%)"
                            )

                        # Look up lot size for the resolved option contract
                        try:
                            underlying = signal.metadata.get("underlying")
                            lot_size = engine._market_service.get_lot_size(
                                signal.instrument_key, underlying_key=underlying
                            )
                            if lot_size > 1:
                                signal.quantity = lot_size
                                signal.metadata["lot_size"] = lot_size
                                logger.info(f"📦 Lot size for {signal.instrument_key}: {lot_size}")
                        except Exception as e:
                            logger.warning(f"Lot size lookup failed: {e}")

                        # Enforce long-options model when configured
                        if buy_only:
                            signal.action = TransactionType.BUY

                        logger.info(
                            f"🎯 Resolved {opt_side.upper()} {signal.instrument_key} "
                            f"(Expiry: {chain_data.get('expiry_date')}, Strike: {chosen_row['strike_price']})"
                        )
                    else:
                        logger.warning(f"No {opt_side.upper()} contract available for resolved strike")
            except Exception as e:
                logger.error(f"Option resolution failed: {e}")
        return True

class ExecutionProcessor(SignalProcessor):
    """
    Handles paper or live order execution and database logging.
    Supports:
      - Single-lot execution (swarm_count = 1)
      - Swarm execution: N parallel lots per signal, each with its own TP level
      - Partial booking metadata extracted from signal and stored in managed_positions
    """

    def _derive_trailing_gap(self, signal: 'TradeSignal', config: 'StrategyConfig') -> float:
        """Derive a trailing gap in the execution instrument's price domain.

        For option-premium execution, preserve the relative trail-to-SL ratio rather
        than reusing the underlying ATR directly.
        """
        meta = signal.metadata or {}
        premium_risk_distance = abs(float(signal.price or 0.0) - float(signal.stop_loss or 0.0))
        if premium_risk_distance <= 0:
            return float((config.params or {}).get("trailing_gap", 0.0))

        params = config.params or {}
        sl_mult = float(
            meta.get("sl_atr_multiplier")
            or meta.get("sl_multiplier")
            or params.get("sl_atr_multiplier")
            or params.get("sl_multiplier")
            or 0.0
        )
        trail_mult = float(
            meta.get("trailing_atr_mult")
            or meta.get("trail_multiplier")
            or params.get("trailing_atr_mult")
            or params.get("trail_multiplier")
            or 0.0
        )

        if sl_mult > 0 and trail_mult > 0:
            return round(premium_risk_distance * (trail_mult / sl_mult), 2)

        explicit_gap = float(params.get("trailing_gap", 0.0) or 0.0)
        return explicit_gap if explicit_gap > 0 else round(premium_risk_distance, 2)

    def _passes_option_execution_sanity(self, signal: 'TradeSignal', config: 'StrategyConfig') -> tuple[bool, str | None]:
        """Validate option contract quality before placing live/paper entry."""
        meta = signal.metadata or {}
        option_snapshot = meta.get("option_snapshot") or {}
        if not option_snapshot:
            return True, None

        p = config.params or {}
        min_ltp = float(p.get("option_min_ltp", 8.0) or 0.0)
        min_oi = float(p.get("option_min_oi", 800.0) or 0.0)
        min_volume = float(p.get("option_min_volume", 80.0) or 0.0)
        max_spread_pct = float(p.get("option_max_spread_pct", 4.0) or 0.0)

        # Regime-adaptive quality thresholds for option execution.
        # High volatility: slightly relax spread/depth constraints to reduce missed fills.
        # Low volatility: tighten spread/depth constraints to avoid paying edge away in chop.
        if bool(p.get("option_quality_regime_adaptive", True)):
            atr_val = float(meta.get("atr") or 0.0)
            signal_px = float(signal.price or 0.0)
            atr_pct = (atr_val / max(signal_px, 1e-9)) * 100.0 if atr_val > 0 and signal_px > 0 else 0.0

            high_vol_cutoff = float(p.get("option_high_vol_atr_pct", 1.20) or 1.20)
            low_vol_cutoff = float(p.get("option_low_vol_atr_pct", 0.35) or 0.35)
            spread_relax = float(p.get("option_high_vol_spread_relax_pct", 1.5) or 1.5)
            spread_tighten = float(p.get("option_low_vol_spread_tighten_pct", 0.8) or 0.8)
            depth_relax_factor = float(p.get("option_high_vol_depth_relax_factor", 0.80) or 0.80)
            depth_tighten_factor = float(p.get("option_low_vol_depth_tighten_factor", 1.20) or 1.20)

            if atr_pct >= high_vol_cutoff:
                max_spread_pct = max_spread_pct + spread_relax if max_spread_pct > 0 else max_spread_pct
                min_oi = min_oi * depth_relax_factor if min_oi > 0 else min_oi
                min_volume = min_volume * depth_relax_factor if min_volume > 0 else min_volume
                meta["option_quality_regime"] = "high_vol_relaxed"
            elif atr_pct > 0 and atr_pct <= low_vol_cutoff:
                max_spread_pct = max(max_spread_pct - spread_tighten, 0.5) if max_spread_pct > 0 else max_spread_pct
                min_oi = min_oi * depth_tighten_factor if min_oi > 0 else min_oi
                min_volume = min_volume * depth_tighten_factor if min_volume > 0 else min_volume
                meta["option_quality_regime"] = "low_vol_tightened"
            else:
                meta["option_quality_regime"] = "normal"

            meta["option_quality_thresholds"] = {
                "min_ltp": round(min_ltp, 4),
                "min_oi": round(min_oi, 4),
                "min_volume": round(min_volume, 4),
                "max_spread_pct": round(max_spread_pct, 4),
            }

        ltp = float(option_snapshot.get("ltp") or 0.0)
        oi = float(option_snapshot.get("oi") or 0.0)
        volume = float(option_snapshot.get("volume") or 0.0)
        spread_pct = option_snapshot.get("spread_pct")
        spread_pct_val = float(spread_pct) if spread_pct is not None else None

        if min_ltp > 0 and ltp < min_ltp:
            return False, f"Option premium too low ({ltp:.2f} < {min_ltp:.2f})"
        if min_oi > 0 and oi < min_oi:
            return False, f"Option OI too low ({oi:.0f} < {min_oi:.0f})"
        if min_volume > 0 and volume < min_volume:
            return False, f"Option volume too low ({volume:.0f} < {min_volume:.0f})"
        if max_spread_pct > 0 and spread_pct_val is not None and spread_pct_val > max_spread_pct:
            return False, f"Option spread too wide ({spread_pct_val:.2f}% > {max_spread_pct:.2f}%)"

        return True, None

    def _build_position_record(
        self,
        signal: 'TradeSignal',
        config: 'StrategyConfig',
        exec_qty: int,
        is_paper: bool,
        lot_idx: int = 0,
        swarm_count: int = 1,
        tp_override: float | None = None,
        gtt_order_id: str | None = None,
    ) -> dict:
        """Build the managed_positions dict entry for one lot."""
        meta = signal.metadata or {}
        entry = float(signal.price or 0.0)
        sl    = float(signal.stop_loss or 0.0)
        tp    = tp_override if tp_override is not None else float(signal.take_profit or 0.0)
        trail_distance = abs(entry - sl)

        # Retrieve partial-booking levels from signal metadata (populated by strategy)
        tp1 = float(meta.get("tp1") or 0.0)
        tp2 = float(meta.get("tp2") or 0.0)
        tp3 = float(meta.get("tp3") or tp)

        return {
            "entry_price":        entry,
            "entry_side":         signal.action.value,  # "BUY" or "SELL"
            "stop_loss":          sl,
            "take_profit":        tp3 if swarm_count == 1 else tp,  # single lot always goes to tp3
            "quantity":           exec_qty,
            "quantity_remaining": exec_qty,
            "is_paper":           is_paper,
            "highest_price":      entry,
            "lowest_price":       entry,
            "trailing_enabled":   bool((config.params or {}).get("enable_trailing_sl", False)),
            "trail_distance":     trail_distance,
            "strategy_name":      signal.strategy_name or config.name,
            # Partial booking levels (only relevant for swarm_count == 1 with partial_tp)
            "partial_tp_enabled": bool(meta.get("partial_tp_enabled", False)) and swarm_count == 1,
            "tp1":                tp1,
            "tp2":                tp2,
            "tp1_book_pct":       int(meta.get("tp1_book_pct", 40)),
            "tp2_book_pct":       int(meta.get("tp2_book_pct", 40)),
            "tp1_booked":         False,
            "tp2_booked":         False,
            "partial_pnl":        0.0,
            # Swarm metadata
            "swarm_idx":          lot_idx,
            "swarm_total":        swarm_count,
            # GTT order tracking — SL/TP handled by exchange, not software
            "gtt_order_id":        gtt_order_id,
            "is_gtt":              gtt_order_id is not None,
            "execution_state":     "entry_pending" if gtt_order_id is not None else "filled",
            "entry_confirmed":     gtt_order_id is None,
            "entry_submitted_at":  datetime.now(IST).isoformat(),
            "entry_timeout_sec":   int((config.params or {}).get("gtt_entry_timeout_sec", 45)),
            "entry_fill_price":    None,
            "require_broker_confirmation": bool((config.params or {}).get("require_broker_fill_confirmation", True)),
        }

    async def _place_one_lot(
        self,
        instrument_key: str,
        signal: 'TradeSignal',
        config: 'StrategyConfig',
        engine: 'AutomationEngine',
        exec_qty: int,
        is_paper: bool,
        pos_key: str,
        lot_idx: int,
        swarm_count: int,
        meta: dict,
        tp_override: float | None,
    ) -> bool:
        """Execute a single lot (paper or live via GTT) and register in _managed_positions."""
        tp_levels = [
            float(meta.get("tp1") or signal.take_profit or 0.0),
            float(meta.get("tp2") or signal.take_profit or 0.0),
            float(meta.get("tp3") or signal.take_profit or 0.0),
        ]
        # Each swarm lot gets a progressively further TP
        swarm_tp = tp_levels[min(lot_idx, len(tp_levels) - 1)] if swarm_count > 1 else None

        gtt_order_id = None

        if is_paper:
            logger.info(
                f"📝 [PAPER LOT {lot_idx+1}/{swarm_count}] "
                f"{signal.action.value} {instrument_key} @ {signal.price:.2f} "
                f"TP={swarm_tp or signal.take_profit:.2f}"
            )
            meta.setdefault("_placement_modes", []).append("paper")
        else:
            try:
                # ── GTT Order: single call places ENTRY + TARGET + STOPLOSS ──
                trailing_gap = self._derive_trailing_gap(signal, config)
                meta["execution_trailing_gap"] = trailing_gap
                result = await asyncio.to_thread(
                    engine._order_service.place_gtt_signal, signal, trailing_gap
                )
                gtt_order_id = result.get("gtt_order_id") if isinstance(result, dict) else None
                if gtt_order_id:
                    meta.setdefault("_gtt_order_ids", []).append(str(gtt_order_id))
                meta.setdefault("_placement_modes", []).append("live")

                if not gtt_order_id:
                    meta["_last_execution_error"] = "GTT order returned no gtt_order_id"
                    logger.error(
                        f"❌ Swarm lot {lot_idx+1} GTT placement returned no order_id; "
                        "skipping managed position registration."
                    )
                    return False

                logger.info(
                    f"💰 [LIVE GTT LOT {lot_idx+1}/{swarm_count}] GTT order placed: "
                    f"gtt_order_id={gtt_order_id}"
                )
            except Exception as e:
                err_text = str(e)
                code_match = re.search(r'"errorCode"\s*:\s*"([A-Z0-9_]+)"', err_text)
                msg_match = re.search(r'"message"\s*:\s*"([^"]+)"', err_text)
                err_code = code_match.group(1) if code_match else None
                err_msg = msg_match.group(1) if msg_match else None

                if err_code and err_msg:
                    meta["_last_execution_error"] = f"{err_code}: {err_msg}"
                    meta["_last_execution_error_code"] = err_code
                else:
                    meta["_last_execution_error"] = f"GTT order placement exception: {e}"
                logger.error(f"❌ Swarm lot {lot_idx+1} GTT order failed: {e}")
                return False

        # Register position
        engine._managed_positions[pos_key] = self._build_position_record(
            signal, config, exec_qty, is_paper,
            lot_idx=lot_idx, swarm_count=swarm_count, tp_override=swarm_tp,
            gtt_order_id=gtt_order_id,
        )

        # DB log
        try:
            session = get_session()
            try:
                log = TradeLog(
                    timestamp=datetime.now(IST),
                    strategy_name=signal.strategy_name or config.name,
                    instrument_key=instrument_key,
                    action=signal.action.value,
                    quantity=exec_qty,
                    price=signal.price,
                    stop_loss=signal.stop_loss,
                    take_profit=swarm_tp or signal.take_profit,
                    status="paper" if is_paper else ("gtt_pending_entry" if gtt_order_id else "filled"),
                    metadata_json={
                        "underlying": meta.get("underlying", instrument_key),
                        "swarm_lot": lot_idx + 1,
                        "swarm_total": swarm_count,
                        "gtt_order_id": gtt_order_id,
                        "execution_state": "paper" if is_paper else ("entry_pending" if gtt_order_id else "filled"),
                        **(signal.metadata or {}),
                    }
                )
                session.add(log)
                session.commit()
            except Exception as e:
                logger.error(f"DB log failed (lot {lot_idx+1}): {e}")
                session.rollback()
            finally:
                session.close()
        except Exception as e:
            logger.error(f"DB session failed: {e}")

        return True

    async def process(self, signal: 'TradeSignal', config: 'StrategyConfig', engine: 'AutomationEngine') -> bool:
        trade_instrument = signal.instrument_key
        meta           = signal.metadata or {}
        force_live = bool(meta.get("force_live", False))
        is_paper   = (False if force_live else (engine.paper_trading or config.paper_trading))

        option_ok, option_reason = self._passes_option_execution_sanity(signal, config)
        if not option_ok:
            meta["_last_execution_error"] = option_reason
            logger.warning(f"🚫 Execution sanity blocked {trade_instrument}: {option_reason}")
            return False

        # Do not stack duplicate entries on an already managed open position.
        has_existing_position = any(
            key == trade_instrument or key.startswith(f"{trade_instrument}#")
            for key in engine._managed_positions
        )
        if signal.action.value == "BUY" and has_existing_position:
            logger.info(f"⏭️ Entry skipped: {trade_instrument} already has an open managed position")
            return False

        exec_qty    = max(int(signal.quantity or 0), 1)
        swarm_count = max(1, int(meta.get("swarm_count", 1)))

        executed_any = False

        if signal.action.value == "BUY":
            if swarm_count > 1:
                # ── Swarm: fire N lots concurrently, keys = instrument_key#1..#N ──
                # Remove any existing lots first (safety)
                for i in range(1, swarm_count + 1):
                    engine._managed_positions.pop(f"{trade_instrument}#{i}", None)

                lot_tasks = [
                    self._place_one_lot(
                        trade_instrument, signal, config, engine,
                        exec_qty, is_paper,
                        pos_key=f"{trade_instrument}#{i+1}",
                        lot_idx=i, swarm_count=swarm_count,
                        meta=meta, tp_override=None,
                    )
                    for i in range(swarm_count)
                ]
                results = await asyncio.gather(*lot_tasks)
                executed_any = any(bool(r) for r in results)
            else:
                # ── Single lot with optional partial-booking ─────────────────────
                engine._managed_positions.pop(trade_instrument, None)
                executed_any = await self._place_one_lot(
                    trade_instrument, signal, config, engine,
                    exec_qty, is_paper,
                    pos_key=trade_instrument,
                    lot_idx=0, swarm_count=1,
                    meta=meta, tp_override=None,
                )

            if not executed_any:
                logger.warning(
                    f"⚠️ Execution skipped for {trade_instrument} ({signal.action.value}): "
                    f"{meta.get('_last_execution_error', 'no placement confirmation')}"
                )
                return False

            if is_paper:
                engine._paper_positions[trade_instrument] = signal.price

        elif signal.action.value == "SELL":
            entry = engine._paper_positions.pop(trade_instrument, None)
            if entry is not None and is_paper:
                pnl = (signal.price - entry) * exec_qty
                engine._daily_pnl += pnl
                logger.info(
                    f"📊 Paper P&L: ₹{pnl:.2f} on {trade_instrument} "
                    f"(daily total: ₹{engine._daily_pnl:.2f})"
                )
            executed_any = True

        engine._trades_today.append({
            "timestamp":  datetime.now(IST).isoformat(),
            "type":       "paper" if is_paper else "live",
            "strategy":   signal.strategy_name or config.name,
            "instrument": trade_instrument,
            "action":     signal.action.value,
            "price":      signal.price,
            "stop_loss":  signal.stop_loss,
            "take_profit": signal.take_profit,
            "score":      signal.confidence_score,
            "swarm_count": swarm_count,
        })
        return executed_any

class AlerterProcessor(SignalProcessor):
    """Sends notifications (Email) for trade signals and persists to ActiveSignal."""
    async def process(self, signal: 'TradeSignal', config: 'StrategyConfig', engine: 'AutomationEngine') -> bool:
        # 1. Persist to ActiveSignal table
        try:
            from app.database.connection import ActiveSignal
            session = get_session()
            try:
                # DB-level duplicate guard: block same strategy/instrument/timeframe/action
                # on the same bar (or within a short fallback window) to avoid UI spam.
                recent = (
                    session.query(ActiveSignal)
                    .filter(
                        ActiveSignal.strategy_name == (signal.strategy_name or config.name),
                        ActiveSignal.instrument_key == signal.instrument_key,
                        ActiveSignal.timeframe == config.timeframe,
                        ActiveSignal.action == signal.action.value,
                        ActiveSignal.status == "active",
                        ActiveSignal.created_at >= (datetime.now(timezone.utc) - timedelta(seconds=90)),
                    )
                    .order_by(ActiveSignal.created_at.desc())
                    .first()
                )

                duplicate = False
                bar_key = (signal.metadata or {}).get("bar_key")
                if recent:
                    recent_meta = recent.metadata_json if isinstance(recent.metadata_json, dict) else {}
                    recent_bar_key = recent_meta.get("bar_key")
                    if bar_key and recent_bar_key:
                        duplicate = (recent_bar_key == bar_key)
                    else:
                        duplicate = abs(float(recent.price or 0.0) - float(signal.price or 0.0)) < 1e-9

                if duplicate:
                    logger.info(
                        f"⏭️ Skipping duplicate ActiveSignal: {signal.action.value} "
                        f"{signal.instrument_key} ({config.timeframe})"
                    )
                    return True

                active_sig = ActiveSignal(
                    strategy_name=signal.strategy_name or config.name,
                    instrument_key=signal.instrument_key,
                    timeframe=config.timeframe,
                    action=signal.action.value,
                    price=signal.price,
                    stop_loss=signal.stop_loss,
                    take_profit=signal.take_profit,
                    confidence_score=signal.confidence_score,
                    status="active",
                    metadata_json=signal.metadata or {},
                )
                session.add(active_sig)
                session.commit()
            except Exception as e:
                logger.error(f"Failed to persist ActiveSignal: {e}")
                session.rollback()
            finally:
                session.close()
        except Exception as e:
            logger.error(f"DB session creation failed for ActiveSignal: {e}")

        # 2. Resolve instrument name from watchlist
        instrument_name = signal.instrument_key
        instrument_symbol = signal.instrument_key.split("|")[-1] if "|" in signal.instrument_key else signal.instrument_key
        try:
            from app.database.connection import Watchlist
            wl_session = get_session()
            try:
                wl_item = wl_session.query(Watchlist).filter_by(instrument_key=signal.instrument_key).first()
                if wl_item:
                    instrument_name = wl_item.name or wl_item.symbol or instrument_name
                    instrument_symbol = wl_item.symbol or instrument_symbol
            finally:
                wl_session.close()
        except Exception:
            pass

        # 3. Send notification
        manager = get_notification_manager()
        is_paper = engine.paper_trading or config.paper_trading
        mode_str = "📋 PAPER" if is_paper else "🔴 LIVE"
        action_emoji = "🟢" if signal.action.value == "BUY" else "🔴"
        meta = signal.metadata or {}
        option_side = meta.get("option_side")
        strike = meta.get("strike_price")
        expiry = meta.get("expiry_date")
        underlying = meta.get("underlying")
        option_line = ""
        if option_side or strike or expiry:
            strike_txt = f"{float(strike):.0f}" if strike is not None else "-"
            option_line = (
                f"\n🎯 Option Contract:\n"
                f"   Underlying: {underlying or '-'}\n"
                f"   Side:       {option_side or '-'}\n"
                f"   Strike:     {strike_txt}\n"
                f"   Expiry:     {expiry or '-'}\n"
            )

        subject = f"🎯 {signal.action.value} Signal: {instrument_symbol} ({instrument_name})"

        body = (
            f"{'━' * 40}\n"
            f"  {action_emoji} {signal.action.value} SIGNAL — {mode_str}\n"
            f"{'━' * 40}\n\n"
            f"📌 Instrument:\n"
            f"   Symbol:    {instrument_symbol}\n"
            f"   Name:      {instrument_name}\n"
            f"   Key:       {signal.instrument_key}\n\n"
            f"{option_line}"
            f"📊 Strategy:  {signal.strategy_name or config.name}\n"
            f"⏱️ Timeframe: {config.timeframe}\n\n"
            f"{'─' * 40}\n"
            f"  💰 PRICE LEVELS\n"
            f"{'─' * 40}\n"
            f"   Entry:       ₹{signal.price:.2f}\n"
            f"   Stop Loss:   ₹{signal.stop_loss:.2f}\n"
            f"   Take Profit: ₹{signal.take_profit:.2f}\n\n"
            f"   Confidence:  {signal.confidence_score}/100\n"
            f"   Time:        {datetime.now(IST).strftime('%d-%b-%Y %H:%M:%S IST')}\n"
            f"{'━' * 40}\n"
        )

        asyncio.create_task(manager.send_alert(subject, body))
        return True

class BroadcastProcessor(SignalProcessor):
    """Broadcasts signal/trade updates to connected UI clients via WebSocket."""
    async def process(self, signal: 'TradeSignal', config: 'StrategyConfig', engine: 'AutomationEngine') -> bool:
        if engine.broadcast_callback:
            is_paper = engine.paper_trading or config.paper_trading
            asyncio.create_task(engine.broadcast_callback({
                "type": "trade_executed",
                "data": {
                    "type": "paper" if is_paper else "live",
                    "strategy": signal.strategy_name or config.name,
                    "instrument": signal.instrument_key,
                    "underlying": (signal.metadata or {}).get("underlying"),
                    "option_side": (signal.metadata or {}).get("option_side"),
                    "strike_price": (signal.metadata or {}).get("strike_price"),
                    "expiry_date": (signal.metadata or {}).get("expiry_date"),
                    "action": signal.action.value,
                    "price": signal.price
                }
            }))
        return True
