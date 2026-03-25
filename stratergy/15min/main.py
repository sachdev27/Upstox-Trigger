import json
import csv
import threading
import Historical.live_socket_2 as socket
instrument_filepath='instrument_list.csv'
equity_filepath='ind_nifty50list.csv'


def read_company_symbols(csv_filepath):
    symbols = set()
    with open(csv_filepath, mode='r', newline='') as file:
        reader = csv.DictReader(file)
        for row in reader:
            symbols.add(row["Symbol"])
    return symbols

def filter_equity_data(equity_csv, company_csv):
    # Read symbols from the company CSV
    company_symbols = read_company_symbols(company_csv)

    filtered_equity_data = []
    with open(equity_csv, mode='r', newline='') as file:
        reader = csv.DictReader(file)
        for row in reader:
            if row["exchange"] == "NSE_EQ" and row["tradingsymbol"] in company_symbols:
                filtered_equity_data.append({
                    "instrument_key": row["instrument_key"],
                    "exchange_token": row["exchange_token"],
                    "tradingsymbol": row["tradingsymbol"],
                    "name": row["name"],
                    "last_price": row["last_price"],
                    "expiry": row["expiry"],
                    "strike": row["strike"],
                    "tick_size": row["tick_size"],
                    "lot_size": row["lot_size"],
                    "instrument_type": row["instrument_type"],
                    "option_type": row["option_type"],
                    "exchange": row["exchange"]
                })
    return filtered_equity_data


def start_data_fetch_threads(filtered_data):
    inst_keys = []
    for index,equity in enumerate(filtered_data):
        instrument_key = equity["instrument_key"]
        instrument_symbol = equity["tradingsymbol"]
        # thread = threading.Thread(target=socket.main, args=(instrument_key,instrument_symbol))
        # threads.append(thread)
        # thread.start()
        inst_keys.append(instrument_key)
    socket.main(inst_keys,"None")
    # for thread in threads:
    #     thread.join()


if __name__ == "__main__":
    equity_csv = instrument_filepath
    company_csv = equity_filepath

    filtered_data = filter_equity_data(equity_csv, company_csv)
    # with open("500instrumentlist.txt","w") as f:
    #     for key in filtered_data:
    #         # print(key)
    #         f.write(f"{key['instrument_key']},{key['tradingsymbol']}\n")

    start_data_fetch_threads(filtered_data)
