"""
Order API routes — place, cancel, view orders.
"""

import logging
from fastapi import APIRouter, Query

from app.auth.service import get_auth_service
from app.orders.service import OrderService
from app.orders.models import OrderRequest, OrderType, TransactionType, ProductType

router = APIRouter(prefix="/orders", tags=["Orders"])
logger = logging.getLogger(__name__)

_order_service_singleton: OrderService | None = None
_order_service_token: str | None = None


def _get_order_service() -> OrderService:
    global _order_service_singleton, _order_service_token

    auth = get_auth_service()
    config = auth.get_configuration()
    token = getattr(config, "access_token", None)

    if _order_service_singleton is None or token != _order_service_token:
        _order_service_singleton = OrderService(config)
        _order_service_token = token

    return _order_service_singleton


@router.post("/place")
async def place_order(
    instrument_token: str,
    quantity: int,
    transaction_type: str,
    order_type: str = "MARKET",
    product: str = "I",
    price: float = 0.0,
    trigger_price: float = 0.0,
):
    """Place a new order."""
    svc = _get_order_service()
    order = OrderRequest(
        instrument_token=instrument_token,
        quantity=quantity,
        transaction_type=TransactionType(transaction_type),
        order_type=OrderType(order_type),
        product=ProductType(product),
        price=price,
        trigger_price=trigger_price,
    )
    result = svc.place_order(order)
    return {"status": "success", "data": result}


@router.get("/book")
async def get_order_book():
    """Get today's order book."""
    try:
        svc = _get_order_service()
        return {"status": "success", "data": svc.get_order_book()}
    except Exception as e:
        logger.error(f"Order book fetch failed: {e}", exc_info=True)
        return {"status": "error", "message": str(e), "data": []}


@router.get("/trades")
async def get_trades():
    """Get today's trade history."""
    try:
        svc = _get_order_service()
        return {"status": "success", "data": svc.get_trade_history()}
    except Exception as e:
        logger.error(f"Trade history fetch failed: {e}", exc_info=True)
        return {"status": "error", "message": str(e), "data": []}


@router.get("/trades/paper")
async def get_paper_trades(limit: int = 100):
    """Get paper trade log from the database (all modes)."""
    from app.database.connection import get_session, TradeLog as TradeLogModel
    session = get_session()
    try:
        rows = (
            session.query(TradeLogModel)
            .order_by(TradeLogModel.timestamp.desc())
            .limit(limit)
            .all()
        )
        return {
            "data": [
                {
                    "id": r.id,
                    "timestamp": r.timestamp.isoformat() if r.timestamp else None,
                    "strategy_name": r.strategy_name,
                    "instrument_key": r.instrument_key,
                    "action": r.action,
                    "quantity": r.quantity,
                    "price": r.price,
                    "stop_loss": r.stop_loss,
                    "take_profit": r.take_profit,
                    "status": r.status,
                    "pnl": r.pnl,
                    "metadata": r.metadata_json if isinstance(r.metadata_json, dict) else {},
                }
                for r in rows
            ]
        }
    finally:
        session.close()



@router.delete("/trades/paper")
async def clear_paper_trades():
    """Clear all entries from the trade log."""
    from app.database.connection import get_session, TradeLog as TradeLogModel
    session = get_session()
    try:
        count = session.query(TradeLogModel).delete()
        session.commit()
        return {"status": "success", "deleted": count}
    except Exception as e:
        session.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        session.close()


@router.get("/status/market-hours")
async def check_market_hours():
    """Check if market is currently open."""
    try:
        svc = _get_order_service()
        return {"market_open": svc.is_market_hours()}
    except Exception as e:
        logger.error(f"Market hours check failed: {e}", exc_info=True)
        return {"status": "error", "message": str(e), "market_open": False}


@router.get("/positions")
async def get_positions():
    """Get all open positions."""
    try:
        svc = _get_order_service()
        return {"status": "success", "data": svc.get_positions()}
    except Exception as e:
        logger.error(f"Positions fetch failed: {e}", exc_info=True)
        return {"status": "error", "message": str(e), "data": []}


@router.get("/holdings")
async def get_holdings():
    """Get all equity holdings."""
    try:
        svc = _get_order_service()
        return {"status": "success", "data": svc.get_holdings()}
    except Exception as e:
        logger.error(f"Holdings fetch failed: {e}", exc_info=True)
        return {"status": "error", "message": str(e), "data": []}


@router.get("/funds")
async def get_funds():
    """Get account funds and margin."""
    try:
        svc = _get_order_service()
        return {"status": "success", "data": svc.get_funds_and_margin()}
    except Exception as e:
        logger.error(f"Funds fetch failed: {e}", exc_info=True)
        return {"status": "error", "message": str(e), "data": {}}


@router.get("/{order_id}")
async def get_order(order_id: str):
    """Get details for a specific order."""
    svc = _get_order_service()
    return {"data": svc.get_order_details(order_id)}


@router.delete("/{order_id}")
async def cancel_order(order_id: str):
    """Cancel an order."""
    svc = _get_order_service()
    result = svc.cancel_order(order_id)
    return {"status": "success", "data": result}
