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
        "NOTIFICATIONS": ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]
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
                        freeze_quantity=int(row['freeze_quantity']) if row.get('freeze_quantity') else 0,
                        minimum_lot=int(row['minimum_lot']) if row.get('minimum_lot') else 1,
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

if __name__ == "__main__":
    init_db()
    seed_settings()
    seed_instruments()
