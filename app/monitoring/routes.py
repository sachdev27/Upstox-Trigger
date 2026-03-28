import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.database.connection import get_session, Watchlist, Instrument
from typing import List

router = APIRouter(prefix="/monitoring", tags=["monitoring"])
logger = logging.getLogger(__name__)

# Static Nifty 200 list (simplified for this implementation)
# In a real app, this would be fetched from a CSV or external API.
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
    # ... more would be added here or loaded from a file
]

def get_nifty200_list() -> List[str]:
    """Return the list of instrument keys for Nifty 200 components."""
    return NIFTY200_KEYS

@router.get("/watchlist")
def get_watchlist(session: Session = Depends(get_session)):
    """Fetch the custom user watchlist."""
    items = session.query(Watchlist).all()
    return {"status": "success", "data": [
        {"instrument_key": i.instrument_key, "symbol": i.symbol, "name": i.name}
        for i in items
    ]}

@router.post("/watchlist")
def add_to_watchlist(instrument_key: str, session: Session = Depends(get_session)):
    """Add a new instrument to the watchlist."""
    # Check if instrument exists in master
    master = session.query(Instrument).filter_by(instrument_key=instrument_key).first()
    if not master:
        raise HTTPException(status_code=404, detail="Instrument not found in master list")
    
    existing = session.query(Watchlist).filter_by(instrument_key=instrument_key).first()
    if existing:
        return {"status": "success", "message": "Already in watchlist"}
    
    item = Watchlist(
        instrument_key=instrument_key,
        symbol=master.symbol,
        name=master.name
    )
    session.add(item)
    session.commit()
    return {"status": "success", "message": f"Added {master.symbol} to watchlist"}

@router.delete("/watchlist/{instrument_key}")
def remove_from_watchlist(instrument_key: str, session: Session = Depends(get_session)):
    """Remove an instrument from the watchlist."""
    item = session.query(Watchlist).filter_by(instrument_key=instrument_key).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    
    session.delete(item)
    session.commit()
    return {"status": "success", "message": "Removed from watchlist"}
