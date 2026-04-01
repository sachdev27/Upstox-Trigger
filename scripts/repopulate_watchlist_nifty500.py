import csv
import logging
from app.database.connection import get_session, Watchlist, init_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def repopulate_watchlist():
    # 1. Initialize DB (ensure tables exist)
    init_db()
    session = get_session()
    
    try:
        # 2. Delete all existing items from Watchlist
        logger.info("🗑️ Clearing existing watchlist...")
        session.query(Watchlist).delete()
        session.commit()
        
        # 3. Read Nifty 500 CSV
        csv_path = "ind_nifty500list.csv"
        new_items = []
        
        logger.info(f"💾 Reading Nifty 500 from {csv_path}...")
        with open(csv_path, mode='r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                symbol = row['Symbol']
                name = row['Company Name']
                # Instrument key format for NSE Equity: NSE_EQ|SYMBOL
                instrument_key = f"NSE_EQ|{symbol}"
                
                new_items.append(Watchlist(
                    instrument_key=instrument_key,
                    symbol=symbol,
                    name=name,
                    timeframes=["1m"] # Default to 1m as per recent user changes
                ))
        
        # 4. Bulk Insert
        if new_items:
            logger.info(f"🚀 Importing {len(new_items)} instruments into watchlist...")
            session.bulk_save_objects(new_items)
            session.commit()
            logger.info("✅ Repopulation complete!")
        else:
            logger.warning("⚠️ No items found in CSV!")
            
    except Exception as e:
        session.rollback()
        logger.error(f"❌ Failed to repopulate watchlist: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    repopulate_watchlist()
