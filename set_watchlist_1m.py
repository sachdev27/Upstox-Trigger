from app.database.connection import get_session, Watchlist

def set_all_1m():
    session = get_session()
    updated = 0
    for w in session.query(Watchlist).all():
        w.timeframes = ["1m"]
        updated += 1
    session.commit()
    print(f"✅ Set {updated} watchlist items to 1m timeframe!")
    session.close()

if __name__ == "__main__":
    set_all_1m()
