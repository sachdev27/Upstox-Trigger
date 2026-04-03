"""
Order pipeline tests — verify paper/live trade execution, risk guards,
position sizing, and the DB log + WebSocket broadcast chain.

All external dependencies (Upstox API, DB) are mocked so tests run
offline without any credentials.
"""

import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest


IST = timezone(timedelta(hours=5, minutes=30))


# ── Helpers ──────────────────────────────────────────────────────

def _make_signal(action="BUY", price=22000.0, sl=21800.0, tp=22400.0, instrument="NSE_EQ|TEST", confidence=70):
    """Build a minimal TradeSignal-like object."""
    from app.orders.models import TradeSignal, TransactionType

    sig = TradeSignal(
        action=TransactionType(action),
        instrument_key=instrument,
        price=price,
        stop_loss=sl,
        take_profit=tp,
        confidence_score=confidence,
        quantity=1,
        strategy_name="TestStrategy",
    )
    return sig


def _make_config(timeframe="15m", paper=True):
    from app.strategies.base import StrategyConfig

    return StrategyConfig(
        name="TestStrategy",
        enabled=True,
        instruments=["NSE_EQ|TEST"],
        timeframe=timeframe,
        paper_trading=paper,
    )


def _make_engine(paper=True, trading_side="BOTH", daily_pnl=0.0, capital=100_000.0, max_loss_pct=3.0):
    """Return a minimal mock AutomationEngine with the attributes the pipeline reads."""
    engine = MagicMock()
    engine.paper_trading = paper
    engine.trading_side = trading_side
    engine._daily_pnl = daily_pnl
    engine._paper_positions = {}
    engine._trades_today = []
    engine.trading_capital = capital
    engine.max_daily_loss_pct = max_loss_pct
    engine.auto_mode = True
    engine.broadcast_callback = None
    return engine


# ── RiskGuardProcessor ───────────────────────────────────────────

class TestRiskGuardProcessor:
    @pytest.fixture
    def processor(self):
        from app.engine_pipeline import RiskGuardProcessor
        return RiskGuardProcessor()

    @pytest.mark.asyncio
    async def test_buy_passes_long_only_mode(self, processor):
        engine = _make_engine(trading_side="LONG_ONLY")
        sig = _make_signal(action="BUY")
        result = await processor.process(sig, _make_config(), engine)
        assert result is True

    @pytest.mark.asyncio
    async def test_sell_blocked_in_long_only_mode(self, processor):
        engine = _make_engine(trading_side="LONG_ONLY")
        sig = _make_signal(action="SELL")
        result = await processor.process(sig, _make_config(), engine)
        assert result is False

    @pytest.mark.asyncio
    async def test_buy_blocked_in_short_only_mode(self, processor):
        engine = _make_engine(trading_side="SHORT_ONLY")
        sig = _make_signal(action="BUY")
        result = await processor.process(sig, _make_config(), engine)
        assert result is False

    @pytest.mark.asyncio
    async def test_sell_passes_short_only_mode(self, processor):
        engine = _make_engine(trading_side="SHORT_ONLY")
        sig = _make_signal(action="SELL")
        result = await processor.process(sig, _make_config(), engine)
        assert result is True

    @pytest.mark.asyncio
    async def test_daily_loss_blocks_trade(self, processor):
        # Capital = 100k, max_loss_pct = 3% → limit = 3000
        # Set daily_pnl to exactly -3000 to trigger the guard
        engine = _make_engine(capital=100_000.0, max_loss_pct=3.0, daily_pnl=-3000.0)
        sig = _make_signal(action="BUY")
        result = await processor.process(sig, _make_config(), engine)
        assert result is False
        # auto_mode should also be disabled
        assert engine.auto_mode is False

    @pytest.mark.asyncio
    async def test_daily_loss_just_under_limit_allows_trade(self, processor):
        # -2999 < -3000 limit, should still pass
        engine = _make_engine(capital=100_000.0, max_loss_pct=3.0, daily_pnl=-2999.0)
        sig = _make_signal(action="BUY")
        result = await processor.process(sig, _make_config(), engine)
        assert result is True

    @pytest.mark.asyncio
    async def test_both_modes_allow_buy_and_sell(self, processor):
        engine = _make_engine(trading_side="BOTH")
        assert await processor.process(_make_signal("BUY"), _make_config(), engine) is True
        assert await processor.process(_make_signal("SELL"), _make_config(), engine) is True


# ── ExecutionProcessor (paper path) ──────────────────────────────

class TestExecutionProcessorPaper:
    @pytest.fixture
    def processor(self):
        from app.engine_pipeline import ExecutionProcessor
        return ExecutionProcessor()

    @pytest.mark.asyncio
    async def test_paper_buy_logged_to_engine(self, processor):
        engine = _make_engine(paper=True)
        sig = _make_signal(action="BUY", price=22000.0)

        with patch("app.engine_pipeline.get_session") as mock_sess:
            mock_db = MagicMock()
            mock_sess.return_value = mock_db

            result = await processor.process(sig, _make_config(paper=True), engine)

        assert result is True
        assert len(engine._trades_today) == 1
        trade = engine._trades_today[0]
        assert trade["action"] == "BUY"
        assert trade["price"] == 22000.0
        assert trade["type"] == "paper"

    @pytest.mark.asyncio
    async def test_paper_buy_stored_in_positions(self, processor):
        engine = _make_engine(paper=True)
        sig = _make_signal(action="BUY", price=22000.0, instrument="NSE_EQ|RELIANCE")

        with patch("app.engine_pipeline.get_session") as mock_sess:
            mock_sess.return_value = MagicMock()
            await processor.process(sig, _make_config(paper=True), engine)

        assert engine._paper_positions.get("NSE_EQ|RELIANCE") == 22000.0

    @pytest.mark.asyncio
    async def test_paper_sell_updates_daily_pnl(self, processor):
        engine = _make_engine(paper=True)
        engine._paper_positions["NSE_EQ|RELIANCE"] = 22000.0  # existing long

        sig = _make_signal(action="SELL", price=22500.0, instrument="NSE_EQ|RELIANCE")
        sig.quantity = 1

        with patch("app.engine_pipeline.get_session") as mock_sess:
            mock_sess.return_value = MagicMock()
            await processor.process(sig, _make_config(paper=True), engine)

        # P&L = (22500 - 22000) * 1 = +500
        assert engine._daily_pnl == pytest.approx(500.0)
        assert "NSE_EQ|RELIANCE" not in engine._paper_positions

    @pytest.mark.asyncio
    async def test_paper_sell_loss_reduces_daily_pnl(self, processor):
        engine = _make_engine(paper=True)
        engine._paper_positions["NSE_EQ|TEST"] = 22000.0

        sig = _make_signal(action="SELL", price=21600.0, instrument="NSE_EQ|TEST")
        sig.quantity = 2

        with patch("app.engine_pipeline.get_session") as mock_sess:
            mock_sess.return_value = MagicMock()
            await processor.process(sig, _make_config(paper=True), engine)

        # P&L = (21600 - 22000) * 2 = -800
        assert engine._daily_pnl == pytest.approx(-800.0)

    @pytest.mark.asyncio
    async def test_paper_trade_written_to_db(self, processor):
        engine = _make_engine(paper=True)
        sig = _make_signal(action="BUY")

        mock_db = MagicMock()
        with patch("app.engine_pipeline.get_session", return_value=mock_db):
            await processor.process(sig, _make_config(paper=True), engine)

        # session.add() must have been called (saving TradeLog entry)
        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_db_rollback_on_error(self, processor):
        """If DB write fails, session is rolled back and pipeline continues."""
        engine = _make_engine(paper=True)
        sig = _make_signal(action="BUY")

        mock_db = MagicMock()
        mock_db.commit.side_effect = Exception("DB error")
        with patch("app.engine_pipeline.get_session", return_value=mock_db):
            result = await processor.process(sig, _make_config(paper=True), engine)

        assert result is True  # pipeline should not halt on DB error
        mock_db.rollback.assert_called_once()


# ── BroadcastProcessor ───────────────────────────────────────────

class TestBroadcastProcessor:
    @pytest.fixture
    def processor(self):
        from app.engine_pipeline import BroadcastProcessor
        return BroadcastProcessor()

    @pytest.mark.asyncio
    async def test_broadcast_fires_when_callback_set(self, processor):
        engine = _make_engine(paper=True)
        broadcast_calls = []

        async def fake_broadcast(msg):
            broadcast_calls.append(msg)

        engine.broadcast_callback = fake_broadcast

        sig = _make_signal(action="BUY", price=22000.0, instrument="NSE_EQ|RELIANCE")
        await processor.process(sig, _make_config(), engine)

        # create_task is async — allow event loop to flush
        await asyncio.sleep(0)

        assert len(broadcast_calls) == 1
        assert broadcast_calls[0]["type"] == "trade_executed"
        assert broadcast_calls[0]["data"]["action"] == "BUY"

    @pytest.mark.asyncio
    async def test_broadcast_skipped_when_no_callback(self, processor):
        engine = _make_engine(paper=True)
        engine.broadcast_callback = None

        sig = _make_signal()
        result = await processor.process(sig, _make_config(), engine)
        assert result is True  # should not raise


# ── Full Pipeline Integration (paper mode) ───────────────────────

class TestFullPipelineIntegration:
    """
    Wires RiskGuard → Execution → Alerter → Broadcast in sequence.
    Mocks all I/O (DB, notifications, WS) so it runs offline.
    """

    @pytest.mark.asyncio
    async def test_full_buy_pipeline_paper(self):
        from app.engine_pipeline import (
            RiskGuardProcessor,
            ExecutionProcessor,
            AlerterProcessor,
            BroadcastProcessor,
        )

        pipeline = [
            RiskGuardProcessor(),
            ExecutionProcessor(),
            AlerterProcessor(),
            BroadcastProcessor(),
        ]

        broadcast_msgs = []

        async def fake_broadcast(msg):
            broadcast_msgs.append(msg)

        engine = _make_engine(paper=True, trading_side="BOTH")
        engine.broadcast_callback = fake_broadcast

        sig = _make_signal("BUY", price=22000.0, instrument="NSE_EQ|TCS")
        config = _make_config()

        with patch("app.engine_pipeline.get_session") as mock_sess, \
             patch("app.notifications.manager.get_notification_manager") as mock_nm:
            mock_sess.return_value = MagicMock()
            mock_nm.return_value = MagicMock()
            mock_nm.return_value.send_alert = AsyncMock()

            for proc in pipeline:
                should_continue = await proc.process(sig, config, engine)
                if not should_continue:
                    break

        # Trade logged in-memory
        assert len(engine._trades_today) == 1
        assert engine._trades_today[0]["action"] == "BUY"

        # Position tracked
        assert engine._paper_positions.get("NSE_EQ|TCS") == 22000.0

        # Broadcast sent
        await asyncio.sleep(0)
        assert any(m["type"] == "trade_executed" for m in broadcast_msgs)

    @pytest.mark.asyncio
    async def test_pipeline_halts_on_risk_breach(self):
        from app.engine_pipeline import (
            RiskGuardProcessor,
            ExecutionProcessor,
        )

        pipeline = [RiskGuardProcessor(), ExecutionProcessor()]

        # Max loss already hit
        engine = _make_engine(capital=100_000.0, max_loss_pct=3.0, daily_pnl=-3000.0)
        sig = _make_signal("BUY")
        config = _make_config()

        with patch("app.engine_pipeline.get_session") as mock_sess:
            mock_sess.return_value = MagicMock()
            for proc in pipeline:
                should_continue = await proc.process(sig, config, engine)
                if not should_continue:
                    break

        # ExecutionProcessor should never have run
        assert len(engine._trades_today) == 0


# ── OrderService Position Sizing ─────────────────────────────────

class TestOrderServicePositionSizing:
    @pytest.fixture
    def svc(self):
        """OrderService with mocked Upstox config."""
        from app.orders.service import OrderService
        mock_config = MagicMock()
        svc = OrderService(mock_config)
        svc.settings.TRADING_CAPITAL = 100_000.0
        svc.settings.MAX_RISK_PER_TRADE_PCT = 1.0  # 1% = ₹1000
        return svc

    def test_position_size_basic(self, svc):
        # Entry 22000, SL 21780 → risk/unit = 220. Risk amount 1% of 100k = 1000
        # qty = 1000 / 220 = 4 (floor)
        with patch.object(svc, "get_funds_and_margin", return_value={}):
            qty = svc._calculate_position_size(entry_price=22000.0, stop_loss=21780.0)
        assert qty == 4

    def test_position_size_returns_at_least_1(self, svc):
        # Very wide stop — risk/unit > risk_amount → qty = 0 → should be 1
        with patch.object(svc, "get_funds_and_margin", return_value={}):
            qty = svc._calculate_position_size(entry_price=100.0, stop_loss=1.0)
        assert qty >= 1

    def test_position_size_uses_available_margin_when_funds_returned(self, svc):
        # If live account has ₹200k available, qty doubles
        funds = {"available_margin": 200_000.0}
        with patch.object(svc, "get_funds_and_margin", return_value=funds):
            qty = svc._calculate_position_size(entry_price=22000.0, stop_loss=21780.0)
        # risk_amount = 200k * 1% = 2000; risk/unit = 220; qty = 9
        assert qty == 9

    def test_position_size_zero_entry_returns_1(self, svc):
        with patch.object(svc, "get_funds_and_margin", return_value={}):
            assert svc._calculate_position_size(0, 0) == 1


# ── Order Route — index instrument guard ─────────────────────────

class TestOrderRoutes:
    @pytest.mark.asyncio
    async def test_place_order_route_passes_valid_equity(self):
        """The /orders/place route should call place_order and return success."""
        from fastapi.testclient import TestClient
        from app.main import app

        mock_result = {"order_id": "ABC123", "status": "COMPLETE"}

        with patch("app.orders.routes._get_order_service") as mock_factory:
            svc = MagicMock()
            svc.place_order.return_value = mock_result
            mock_factory.return_value = svc

            client = TestClient(app)
            resp = client.post(
                "/orders/place",
                params={
                    "instrument_token": "NSE_EQ|INE040A01034",
                    "quantity": 1,
                    "transaction_type": "BUY",
                    "order_type": "MARKET",
                },
            )

        assert resp.status_code == 200
        assert resp.json()["status"] == "success"
        assert resp.json()["data"]["order_id"] == "ABC123"

    @pytest.mark.asyncio
    async def test_paper_trades_endpoint_returns_db_rows(self):
        """GET /orders/trades/paper should return rows from TradeLog."""
        from fastapi.testclient import TestClient
        from app.main import app
        from app.database.connection import TradeLog

        # Build a fake DB row
        fake_row = MagicMock(spec=TradeLog)
        fake_row.id = 1
        fake_row.timestamp = datetime(2026, 4, 1, 10, 30, 0, tzinfo=IST)
        fake_row.strategy_name = "SuperTrendPro"
        fake_row.instrument_key = "NSE_EQ|RELIANCE"
        fake_row.action = "BUY"
        fake_row.quantity = 2
        fake_row.price = 2850.0
        fake_row.stop_loss = 2800.0
        fake_row.take_profit = 2950.0
        fake_row.status = "paper"
        fake_row.pnl = 0.0

        mock_query = MagicMock()
        mock_query.order_by.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.all.return_value = [fake_row]

        mock_session = MagicMock()
        mock_session.query.return_value = mock_query

        with patch("app.database.connection.get_session", return_value=mock_session):
            client = TestClient(app)
            resp = client.get("/orders/trades/paper")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 1
        assert data[0]["instrument_key"] == "NSE_EQ|RELIANCE"
        assert data[0]["action"] == "BUY"
        assert data[0]["status"] == "paper"


# ── Market Hours Guard ────────────────────────────────────────────

class TestMarketHoursGuard:
    @pytest.fixture
    def svc(self):
        from app.orders.service import OrderService
        return OrderService(MagicMock())

    def test_market_open_on_weekday(self, svc):
        # Monday 2026-03-30 10:00 AM IST — weekday()=0, inside 9:15–15:30
        monday_10am = datetime(2026, 3, 30, 10, 0, 0, tzinfo=IST)
        with patch("app.orders.service.datetime") as mock_dt:
            mock_dt.now.return_value = monday_10am
            assert svc.is_market_hours() is True

    def test_market_closed_before_open(self, svc):
        # Monday 8:00 AM IST — before market
        monday_8am = datetime(2026, 3, 30, 8, 0, 0, tzinfo=IST)
        with patch("app.orders.service.datetime") as mock_dt:
            mock_dt.now.return_value = monday_8am
            assert svc.is_market_hours() is False

    def test_market_closed_on_weekend(self, svc):
        # Saturday 2026-04-04 11:00 AM IST — weekday()=5, should be closed
        saturday = datetime(2026, 4, 4, 11, 0, 0, tzinfo=IST)
        with patch("app.orders.service.datetime") as mock_dt:
            mock_dt.now.return_value = saturday
            assert svc.is_market_hours() is False
