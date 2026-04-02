import io
import csv
import json
import logging
import os
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from app.database.connection import get_session, Watchlist, Instrument, ActiveSignal
from app.config import get_settings
from typing import List, Optional

import requests

router = APIRouter(prefix="/monitoring", tags=["monitoring"])
logger = logging.getLogger(__name__)
IST = timezone(timedelta(hours=5, minutes=30))


@router.get("/network/proxy-status")
def proxy_status(check_ip: bool = Query(False, description="When true, performs ipify egress check")):
    """Show effective proxy and algo-header runtime settings for troubleshooting."""
    settings = get_settings()

    upstox_proxy = (settings.UPSTOX_PROXY_URL or "").strip()
    http_proxy = (settings.REQUESTS_HTTP_PROXY or "").strip()
    https_proxy = (settings.REQUESTS_HTTPS_PROXY or "").strip()
    all_proxy = (os.getenv("ALL_PROXY") or os.getenv("all_proxy") or "").strip()

    result = {
        "require_upstox_proxy": bool(settings.REQUIRE_UPSTOX_PROXY),
        "upstox_proxy_configured": bool(upstox_proxy),
        "requests_http_proxy_configured": bool(http_proxy),
        "requests_https_proxy_configured": bool(https_proxy),
        "all_proxy_env_configured": bool(all_proxy),
        "algo_name_configured": bool((settings.ALGO_NAME or "").strip()),
        "algo_name": (settings.ALGO_NAME or "").strip() or None,
    }

    if check_ip:
        proxies = {
            "http": http_proxy or https_proxy or upstox_proxy,
            "https": https_proxy or http_proxy or upstox_proxy,
        }
        try:
            r = requests.get("https://api.ipify.org", proxies=proxies, timeout=15)
            r.raise_for_status()
            result["egress_ip"] = r.text.strip()
        except Exception as e:
            result["egress_ip_error"] = str(e)

    return {"status": "success", "data": result}


@router.get("/streamer/status")
def streamer_status(request: Request):
    """Return lightweight diagnostics for SDK market/portfolio streamers."""
    app_state = request.app.state

    market_streamer = getattr(app_state, "market_streamer", None)
    portfolio_streamer = getattr(app_state, "portfolio_streamer", None)
    last_tick = getattr(app_state, "last_market_tick", None)

    market_inner = getattr(market_streamer, "_streamer", None)
    portfolio_inner = getattr(portfolio_streamer, "_streamer", None)

    data = {
        "market_streamer_initialized": market_streamer is not None,
        "portfolio_streamer_initialized": portfolio_streamer is not None,
        "market_streamer_connected": bool(market_inner),
        "portfolio_streamer_connected": bool(portfolio_inner),
        "last_market_tick": last_tick,
    }
    return {"status": "success", "data": data}

# Static Nifty 200 list (simplified for this implementation)
NIFTY200_KEYS = [
    "NSE_EQ|INE002A01018", # RELIANCE
    "NSE_EQ|INE040A01034", # HDFCBANK
    "NSE_EQ|INE009A01021", # INFOSYS
    "NSE_EQ|INE062A01020", # SBIN
    "NSE_EQ|INE030A01027", # HINDUNILVR
    "NSE_EQ|INE467B01029", # TCS
    "NSE_EQ|INE075A01022", # WIPRO
    "NSE_EQ|INE154A01025", # ITC
    "NSE_EQ|INE238A01034", # AXISBANK
    "NSE_EQ|INE081A01012", # ICICIBANK
]

def get_nifty200_list() -> List[str]:
    """Return the list of instrument keys for Nifty 200 components."""
    return NIFTY200_KEYS


# ── Watchlist CRUD ──────────────────────────────────────────────

@router.get("/watchlist")
def get_watchlist(session: Session = Depends(get_session)):
    """Fetch the custom user watchlist."""
    items = session.query(Watchlist).all()
    return {"status": "success", "data": [
        {
            "id": i.id,
            "instrument_key": i.instrument_key,
            "symbol": i.symbol,
            "name": i.name,
            "timeframes": i.timeframes or ["15m"],
        }
        for i in items
    ]}


@router.post("/watchlist")
def add_to_watchlist(
    instrument_key: str,
    timeframes: Optional[str] = Query(None, description="Comma-separated TFs, e.g. '5m,15m,1H'"),
    session: Session = Depends(get_session),
):
    """Add a new instrument to the watchlist."""
    master = session.query(Instrument).filter_by(instrument_key=instrument_key).first()

    existing = session.query(Watchlist).filter_by(instrument_key=instrument_key).first()
    if existing:
        # Update timeframes if provided
        if timeframes:
            existing.timeframes = [t.strip() for t in timeframes.split(",") if t.strip()]
            session.commit()
        return {"status": "success", "message": "Already in watchlist (timeframes updated)"}

    tf_list = [t.strip() for t in timeframes.split(",") if t.strip()] if timeframes else ["15m"]

    item = Watchlist(
        instrument_key=instrument_key,
        symbol=master.symbol if master else instrument_key.split("|")[-1],
        name=master.name if master else instrument_key,
        timeframes=tf_list,
    )
    session.add(item)
    session.commit()
    return {"status": "success", "message": f"Added {item.symbol} to watchlist"}


@router.put("/watchlist/{item_id}/timeframes")
def update_watchlist_timeframes(
    item_id: int,
    timeframes: str = Query(..., description="Comma-separated TFs"),
    session: Session = Depends(get_session),
):
    """Update timeframes for a watchlist item."""
    item = session.query(Watchlist).filter_by(id=item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Watchlist item not found")

    item.timeframes = [t.strip() for t in timeframes.split(",") if t.strip()]
    session.commit()
    return {"status": "success", "timeframes": item.timeframes}


@router.delete("/watchlist/{instrument_key:path}")
def remove_from_watchlist(instrument_key: str, session: Session = Depends(get_session)):
    """Remove an instrument from the watchlist."""
    item = session.query(Watchlist).filter_by(instrument_key=instrument_key).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    session.delete(item)
    session.commit()
    return {"status": "success", "message": "Removed from watchlist"}


# ── Watchlist Import / Export ───────────────────────────────────

@router.get("/watchlist/export")
def export_watchlist(session: Session = Depends(get_session)):
    """Export watchlist as CSV."""
    items = session.query(Watchlist).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["instrument_key", "symbol", "name", "timeframes"])
    for item in items:
        tfs = ",".join(item.timeframes or ["15m"])
        writer.writerow([item.instrument_key, item.symbol, item.name, tfs])

    output.seek(0)
    return StreamingResponse(
        output,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=watchlist.csv"},
    )


@router.post("/watchlist/import")
async def import_watchlist(file: UploadFile = File(...), session: Session = Depends(get_session)):
    """Import watchlist from CSV. Expected columns: instrument_key, symbol, name, timeframes"""
    content = await file.read()
    text = content.decode("utf-8")
    reader = csv.DictReader(io.StringIO(text))

    added = 0
    skipped = 0
    for row in reader:
        key = row.get("instrument_key", "").strip()
        if not key:
            continue

        existing = session.query(Watchlist).filter_by(instrument_key=key).first()
        if existing:
            skipped += 1
            continue

        tfs_raw = row.get("timeframes", "15m").strip()
        tfs = [t.strip() for t in tfs_raw.split(",") if t.strip()] or ["15m"]

        item = Watchlist(
            instrument_key=key,
            symbol=row.get("symbol", key.split("|")[-1]).strip(),
            name=row.get("name", key).strip(),
            timeframes=tfs,
        )
        session.add(item)
        added += 1

    session.commit()
    return {"status": "success", "added": added, "skipped": skipped}


# ── Active Signals ──────────────────────────────────────────────

@router.get("/active-signals")
def get_active_signals(
    status: Optional[str] = Query(None, description="Filter by status: active or closed"),
    session: Session = Depends(get_session),
):
    """Fetch persisted strategy signals."""
    q = session.query(ActiveSignal).order_by(ActiveSignal.created_at.desc())
    if status:
        q = q.filter(ActiveSignal.status == status)

    signals = q.limit(100).all()
    return {"status": "success", "data": [
        {
            "id": s.id,
            "strategy_name": s.strategy_name,
            "instrument_key": s.instrument_key,
            "timeframe": s.timeframe,
            "action": s.action,
            "price": s.price,
            "stop_loss": s.stop_loss,
            "take_profit": s.take_profit,
            "confidence_score": s.confidence_score,
            "status": s.status,
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "closed_at": s.closed_at.isoformat() if s.closed_at else None,
        }
        for s in signals
    ]}


@router.post("/active-signals/{signal_id}/close")
def close_active_signal(signal_id: int, session: Session = Depends(get_session)):
    """Mark an active signal as closed."""
    sig = session.query(ActiveSignal).filter_by(id=signal_id).first()
    if not sig:
        raise HTTPException(status_code=404, detail="Signal not found")

    sig.status = "closed"
    sig.closed_at = datetime.now(IST)
    session.commit()
    return {"status": "success", "message": "Signal closed"}


@router.delete("/active-signals/{signal_id}")
def delete_active_signal(signal_id: int, session: Session = Depends(get_session)):
    """Delete a signal record."""
    sig = session.query(ActiveSignal).filter_by(id=signal_id).first()
    if not sig:
        raise HTTPException(status_code=404, detail="Signal not found")

    session.delete(sig)
    session.commit()
    return {"status": "success", "message": "Signal deleted"}
