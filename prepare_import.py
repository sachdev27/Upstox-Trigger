import csv

def build_import_csv():
    count = 0
    with open("instrument_list.csv", "r", encoding="utf-8") as fin, open("nse_eq_import.csv", "w", encoding="utf-8", newline="") as fout:
        reader = csv.DictReader(fin)
        writer = csv.DictWriter(fout, fieldnames=["instrument_key", "symbol", "name", "timeframes"])
        writer.writeheader()
        
        for row in reader:
            if row.get("exchange") == "NSE_EQ":
                # Export all NSE_EQ instruments with just a single TF to prevent extreme rate limiting
                writer.writerow({
                    "instrument_key": row.get("instrument_key", ""),
                    "symbol": row.get("tradingsymbol", ""),
                    "name": row.get("name", ""),
                    "timeframes": "15m"
                })
                count += 1
    print(f"Prepared nse_eq_import.csv with {count} instruments.")

if __name__ == "__main__":
    build_import_csv()
