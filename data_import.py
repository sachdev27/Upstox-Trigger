import csv
import sys
import os

# Add current dir to path to find app package
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.database.connection import get_session, Watchlist

def import_nse_eq():
    session = get_session()
    
    # Read existing
    existing = {w.instrument_key for w in session.query(Watchlist.instrument_key).all()}
    
    to_insert = []
    count = 0
    with open("instrument_list.csv", "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("exchange") == "NSE_EQ":
                key = row["instrument_key"]
                if key not in existing:
                    to_insert.append(Watchlist(
                        instrument_key=key,
                        symbol=row.get("tradingsymbol", ""),
                        name=row.get("name", ""),
                        timeframes=["1m", "5m"] # Add multiple timeframes to test engine load
                    ))
                    existing.add(key)
                
                count += 1
                
    if to_insert:
        chunk_size = 1000
        for i in range(0, len(to_insert), chunk_size):
            session.bulk_save_objects(to_insert[i:i+chunk_size])
        session.commit()
    
    print(f"Found {count} NSE_EQ instruments. Inserted {len(to_insert)} new ones.")
    session.close()

if __name__ == "__main__":
    import_nse_eq()
