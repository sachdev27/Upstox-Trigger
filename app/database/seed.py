import os
import csv
import logging
from pathlib import Path
from datetime import datetime
from dotenv import dotenv_values

from app.database.connection import init_db, get_session, ConfigSetting, Instrument
from app.config import BASE_DIR

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def seed_settings():
    """Migrate settings from .env to the database."""
    session = get_session()
    
    # Load all variables from .env
    env_vars = dotenv_values(BASE_DIR / ".env")
    
    # Define categories and secrets
    secret_keys = ["API_KEY", "API_SECRET", "ACCESS_TOKEN", "TELEGRAM_BOT_TOKEN"]
    category_map = {
        "API": ["API_KEY", "API_SECRET", "REDIRECT_URI", "ACCESS_TOKEN"],
        "RISK": ["MAX_RISK_PER_TRADE_PCT", "MAX_DAILY_LOSS_PCT", "MAX_CONCURRENT_POSITIONS", "SQUARE_OFF_TIME"],
        "ENGINE": ["BANKNIFTY", "NIFTY", "HOST", "PORT", "DEBUG", "LOG_LEVEL"],
        "NOTIFICATIONS": [
            "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
            "SMTP_SERVER", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "EMAIL_RECIPIENT",
            "NOTIFICATION_CHANNELS"
        ]
    }

    count = 0
    for key, value in env_vars.items():
        # Strip UPSTOX_ prefix if present (consistent with Settings class)
        db_key = key.replace("UPSTOX_", "")
        
        # Check if already exists
        existing = session.query(ConfigSetting).filter_by(key=db_key).first()
        if not existing:
            category = "GENERAL"
            for cat, keys in category_map.items():
                if db_key in keys:
                    category = cat
                    break
            
            setting = ConfigSetting(
                key=db_key,
                value=value,
                category=category,
                is_secret=db_key in secret_keys,
                description=f"Initial seed from .env: {db_key}"
            )
            session.add(setting)
            count += 1
            
    session.commit()
    logger.info(f"✅ Seeded {count} settings from .env")
    session.close()

def seed_instruments():
    """Import featured instruments into the database."""
    session = get_session()
    
    # We'll import Nifty 50, Bank Nifty, and stocks from ind_nifty50list.csv
    # But first, let's get the master data from instrument_list.csv for these symbols
    master_csv = BASE_DIR / "instrument_list.csv"
    if not master_csv.exists():
        logger.warning(f"Master instrument list not found at {master_csv}")
        return

    # 1. Gather Nifty 50 symbols from the CSV
    n50_symbols = []
    n50_list_path = BASE_DIR / "ind_nifty50list.csv"
    if n50_list_path.exists():
        with open(n50_list_path, mode='r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                n50_symbols.append(row['Symbol'])
    
    # 2. Key indices
    target_symbols = set(n50_symbols) | {"Nifty 50", "Nifty Bank", "NIFTY", "BANKNIFTY"}
    
    count = 0
    with open(master_csv, mode='r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            symbol = row['tradingsymbol']
            name = row['name']
            instrument_key = row['instrument_key']
            
            # Simple heuristic to find our target instruments
            is_target = False
            if symbol in target_symbols or name in target_symbols:
                is_target = True
            elif instrument_key in ["NSE_INDEX|Nifty 50", "NSE_INDEX|Nifty Bank"]:
                is_target = True
                
            if is_target:
                existing = session.query(Instrument).filter_by(instrument_key=instrument_key).first()
                if not existing:
                    inst = Instrument(
                        instrument_key=instrument_key,
                        symbol=symbol or name,
                        name=name,
                        exchange=row['exchange'],
                        segment=row['instrument_type'], # Simplified mapping
                        lot_size=int(row['lot_size']) if row['lot_size'] else 1,
                        tick_size=float(row['tick_size']) if row['tick_size'] else 0.05,
                        instrument_type=row['instrument_type'],
                        expiry=row['expiry'] if row['expiry'] else None,
                        strike=float(row['strike']) if row['strike'] else None
                    )
                    session.add(inst)
                    count += 1
    
    session.commit()
    logger.info(f"✅ Seeded {count} instruments into the database")
    session.close()


# ── Nifty 50 Watchlist Seed ──────────────────────────────────────

NIFTY50_STOCKS = [
    ("NSE_EQ|INE002A01018", "RELIANCE", "Reliance Industries"),
    ("NSE_EQ|INE040A01034", "HDFCBANK", "HDFC Bank"),
    ("NSE_EQ|INE009A01021", "INFOSYS", "Infosys"),
    ("NSE_EQ|INE062A01020", "SBIN", "State Bank of India"),
    ("NSE_EQ|INE467B01029", "TCS", "Tata Consultancy"),
    ("NSE_EQ|INE081A01012", "ICICIBANK", "ICICI Bank"),
    ("NSE_EQ|INE030A01027", "HINDUNILVR", "Hindustan Unilever"),
    ("NSE_EQ|INE154A01025", "ITC", "ITC Limited"),
    ("NSE_EQ|INE238A01034", "AXISBANK", "Axis Bank"),
    ("NSE_EQ|INE075A01022", "WIPRO", "Wipro"),
    ("NSE_EQ|INE585B01010", "MARUTI", "Maruti Suzuki"),
    ("NSE_EQ|INE397D01024", "BAJFINANCE", "Bajaj Finance"),
    ("NSE_EQ|INE296A01024", "BHARTIARTL", "Bharti Airtel"),
    ("NSE_EQ|INE021A01026", "ASIANPAINT", "Asian Paints"),
    ("NSE_EQ|INE047A01021", "KOTAKBANK", "Kotak Mahindra Bank"),
    ("NSE_EQ|INE669E01016", "TATAMOTORS", "Tata Motors"),
    ("NSE_EQ|INE176A01028", "TATASTEEL", "Tata Steel"),
    ("NSE_EQ|INE117A01022", "ADANIENT", "Adani Enterprises"),
    ("NSE_EQ|INE848E01016", "HCLTECH", "HCL Technologies"),
    ("NSE_EQ|INE024J01012", "LTIM", "LTIMindtree"),
    ("NSE_EQ|INE860A01027", "JSWSTEEL", "JSW Steel"),
    ("NSE_EQ|INE040H01021", "NTPC", "NTPC"),
    ("NSE_EQ|INE028A01039", "BAJAJFINSV", "Bajaj Finserv"),
    ("NSE_EQ|INE245A01021", "SUNPHARMA", "Sun Pharmaceutical"),
    ("NSE_EQ|INE038A01020", "POWERGRID", "Power Grid"),
    ("NSE_EQ|INE090A01021", "TECHM", "Tech Mahindra"),
    ("NSE_EQ|INE042A01014", "ULTRACEMCO", "UltraTech Cement"),
    ("NSE_EQ|INE019A01038", "M_M", "Mahindra & Mahindra"),
    ("NSE_EQ|INE733E01010", "INDUSINDBK", "IndusInd Bank"),
    ("NSE_EQ|INE239A01016", "NESTLEIND", "Nestle India"),
    ("NSE_EQ|INE029A01011", "BPCL", "Bharat Petroleum"),
    ("NSE_EQ|INE001A01036", "HDFC", "HDFC"),
    ("NSE_EQ|INE256A01028", "ADANIPORTS", "Adani Ports"),
    ("NSE_EQ|INE114A01011", "GRASIM", "Grasim Industries"),
    ("NSE_EQ|INE213A01029", "ONGC", "ONGC"),
    ("NSE_EQ|INE160A01022", "DIVISLAB", "Divi's Laboratories"),
    ("NSE_EQ|INE152A01029", "DRREDDY", "Dr. Reddy's"),
    ("NSE_EQ|INE376G01013", "TITAN", "Titan Company"),
    ("NSE_EQ|INE726G01019", "COALINDIA", "Coal India"),
    ("NSE_EQ|INE134E01011", "EICHERMOT", "Eicher Motors"),
    ("NSE_EQ|INE758T01015", "APOLLOHOSP", "Apollo Hospitals"),
    ("NSE_EQ|INE437A01024", "LT", "Larsen & Toubro"),
    ("NSE_EQ|INE092T01019", "SBILIFE", "SBI Life Insurance"),
    ("NSE_EQ|INE044A01036", "HINDALCO", "Hindalco Industries"),
    ("NSE_EQ|INE121A01024", "CIPLA", "Cipla"),
    ("NSE_EQ|INE018A01030", "HEROMOTOCO", "Hero MotoCorp"),
    ("NSE_EQ|INE522F01014", "TATACONSUM", "Tata Consumer"),
    ("NSE_EQ|INE261F01019", "HDFCLIFE", "HDFC Life Insurance"),
    ("NSE_EQ|INE774D01024", "BRITANNIA", "Britannia Industries"),
    ("NSE_EQ|INE020B01018", "WIPRO", "Wipro"),
]


def seed_watchlist_nifty50():
    """Pre-populate watchlist with Nifty 50 stocks if empty."""
    from app.database.connection import Watchlist
    session = get_session()

    existing = session.query(Watchlist).count()
    if existing > 0:
        logger.info(f"Watchlist already has {existing} items — skipping Nifty 50 seed")
        session.close()
        return

    count = 0
    for instrument_key, symbol, name in NIFTY50_STOCKS:
        # Avoid duplicates on the symbol level
        exists = session.query(Watchlist).filter_by(instrument_key=instrument_key).first()
        if not exists:
            item = Watchlist(
                instrument_key=instrument_key,
                symbol=symbol,
                name=name,
                timeframes=["15m"],
            )
            session.add(item)
            count += 1

    session.commit()
    logger.info(f"✅ Seeded watchlist with {count} Nifty 50 stocks")
    session.close()


if __name__ == "__main__":
    init_db()
    seed_settings()
    seed_instruments()
    seed_watchlist_nifty50()
