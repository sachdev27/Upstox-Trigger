"""
Order API routes — place, cancel, view orders.
"""

from fastapi import APIRouter, Query

from app.auth.service import get_auth_service
from app.orders.service import OrderService
from app.orders.models import OrderRequest, OrderType, TransactionType, ProductType

router = APIRouter(prefix="/orders", tags=["Orders"])


def _get_order_service() -> OrderService:
    auth = get_auth_service()
    return OrderService(auth.get_configuration())


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
    svc = _get_order_service()
    return {"data": svc.get_order_book()}


@router.get("/trades")
async def get_trades():
    """Get today's trade history."""
    svc = _get_order_service()
    return {"data": svc.get_trade_history()}


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



@router.get("/status/market-hours")
async def check_market_hours():
    """Check if market is currently open."""
    svc = _get_order_service()
    return {"market_open": svc.is_market_hours()}


@router.get("/positions")
async def get_positions():
    """Get all open positions."""
    svc = _get_order_service()
    return {"data": svc.get_positions()}


@router.get("/holdings")
async def get_holdings():
    """Get all equity holdings."""
    svc = _get_order_service()
    return {"data": svc.get_holdings()}


@router.get("/funds")
async def get_funds():
    """Get account funds and margin."""
    svc = _get_order_service()
    return {"data": svc.get_funds_and_margin()}


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
