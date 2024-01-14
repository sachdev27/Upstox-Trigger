from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.columns import Columns
import csv
from datetime import datetime
import instrument
from queue import PriorityQueue

csv_filename_bank = "market_data_log_bank.csv"
csv_filename_n50 = "market_data_log_n50.csv"
options_data = "options_data.csv"
# https://api-v2.upstox.com/historical-candle/intraday/NSE_INDEX%7CNifty%2050/1minute


def options_table_from_csv(option_type, instrument_name, ltp_range, csv_filename='options_data.csv'):
    table = Table(title=f"Top 5 {option_type} Options for {instrument_name}")
    table.add_column("Instrument Token", style="bold")
    table.add_column("Trading Symbol", style="bold")
    table.add_column("Price", style="bold")
    table.add_column("Delta")
    table.add_column("Gamma")
    table.add_column("Theta")

    top_options = PriorityQueue(maxsize=6)

    with open(csv_filename, 'r') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            if row['Instrument'].startswith(instrument_name) and row['Option Type'] == option_type:
                try:
                    ltp_float = float(row['LTP'])
                    # Extract strike price from the trading symbol
                    parts = row['Instrument'].split(option_type)
                    if len(parts) > 1:
                        strike_price = float(parts[0][-5:])  # Assumes last 5 characters represent strike price

                        # Condition for CE and PE based on strike price
                        if (option_type == 'CE' and strike_price <= ltp_range) or (option_type == 'PE' and strike_price-100 <= ltp_range):
                            top_options.put((-ltp_float if option_type == 'CE' else ltp_float, row['Instrument Token'], row['Instrument'],row['Delta'],row['Gamma'],row['Theta']))
                            if top_options.full():
                                top_options.get()
                except (ValueError, IndexError):
                    pass  # Ignore rows with invalid data

    # Retrieve items from the queue and add them to the table
    while not top_options.empty():
        price, token, symbol,delta,gamma,theta = top_options.get()
        table.add_row(token, symbol, f"{-price if option_type == 'CE' else price:.2f}",delta,gamma,theta)  # Correct the sign for CE

    return table


def make_rich_table(data_dict):
    console = Console()
    table = Table(title="Real-time Market Data")
    table.add_column("Instrument", style="bold", width=20)
    table.add_column("LTP", style="bold", width=20)
    table.add_column("Open", style="bold")
    table.add_column("High", style="bold")
    table.add_column("Low", style="bold")
    table.add_column("Close", style="bold")
    ltp = [0, 0]
    open_price = [0, 0]
    high = [0, 0]
    low = [0, 0]
    close = [0, 0]
    instrument_bank = "BANKNIFTY"
    instrument_n50 = "NIFTY"

    try:
        # Assuming the structure of the dictionary
        try:
            ltp[0] = data_dict['feeds']['NSE_INDEX|Nifty Bank']['ff']['indexFF']['ltpc']['ltp']
            open_price[0] = data_dict['feeds']['NSE_INDEX|Nifty Bank']['ff']['indexFF']['marketOHLC']['ohlc'][0]['open']
            high[0] = data_dict['feeds']['NSE_INDEX|Nifty Bank']['ff']['indexFF']['marketOHLC']['ohlc'][0]['high']
            low[0] = data_dict['feeds']['NSE_INDEX|Nifty Bank']['ff']['indexFF']['marketOHLC']['ohlc'][0]['low']
            close[0] = data_dict['feeds']['NSE_INDEX|Nifty Bank']['ff']['indexFF']['marketOHLC']['ohlc'][0]['close']

            ltp[1] = data_dict['feeds']["NSE_INDEX|Nifty 50"]['ff']['indexFF']['ltpc']['ltp']
            open_price[1] = data_dict['feeds']['NSE_INDEX|Nifty 50']['ff']['indexFF']['marketOHLC']['ohlc'][0]['open']
            high[1] = data_dict['feeds']["NSE_INDEX|Nifty 50"]['ff']['indexFF']['marketOHLC']['ohlc'][0]['high']
            low[1] = data_dict['feeds']["NSE_INDEX|Nifty 50"]['ff']['indexFF']['marketOHLC']['ohlc'][0]['low']
            close[1] = data_dict['feeds']["NSE_INDEX|Nifty 50"]['ff']['indexFF']['marketOHLC']['ohlc'][0]['close']

            # Open the CSV files for writing
            with open(csv_filename_bank, 'a', newline='') as csvfile_bank, \
                    open(csv_filename_n50, 'a', newline='') as csvfile_n50:
                fieldnames = ['Timestamp', 'Instrument', 'LTP','Open', 'High', 'Low', 'Close']
                csv_writer_bank = csv.DictWriter(csvfile_bank, fieldnames=fieldnames)
                csv_writer_n50 = csv.DictWriter(csvfile_n50, fieldnames=fieldnames)

                # Write the headers to the CSV files (if not already present)
                if csvfile_bank.tell() == 0:
                    csv_writer_bank.writeheader()
                if csvfile_n50.tell() == 0:
                    csv_writer_n50.writeheader()


                bank_ce_table = options_table_from_csv("CE",instrument_bank,ltp[0])
                bank_pe_table = options_table_from_csv("PE",instrument_bank,ltp[0])
                nifty_ce_table = options_table_from_csv("CE",instrument_n50,ltp[1])
                nifty_pe_table = options_table_from_csv("PE",instrument_n50,ltp[1])

                # Update the row in the table
                table.add_row(instrument_bank, f"{ltp[0]:.2f}",f"{open_price[0]:.2f}", f"{high[0]:.2f}", f"{low[0]:.2f}", f"{close[0]:.2f}")
                table.add_row(instrument_n50, f"{ltp[1]:.2f}", f"{open_price[1]:.2f}", f"{high[1]:.2f}", f"{low[1]:.2f}", f"{close[1]:.2f}")

                # Update the CSV file for Nifty Bank
                csv_writer_bank.writerow({
                    'Timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    'Instrument': instrument_bank,
                    'LTP': f"{ltp[0]:.2f}",
                    'Open': f"{open_price[0]:.2f}",
                    'High': f"{high[0]:.2f}",
                    'Low': f"{low[0]:.2f}",
                    'Close': f"{close[0]:.2f}"
                })

                # Update the CSV file for Nifty 50
                csv_writer_n50.writerow({
                    'Timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    'Instrument': instrument_n50,
                    'LTP': f"{ltp[1]:.2f}",
                    'Open': f"{open_price[1]:.2f}",
                    'High': f"{high[1]:.2f}",
                    'Low': f"{low[1]:.2f}",
                    'Close': f"{close[1]:.2f}"
                })


                # Print the updated table in the same location
                console.print(table, end="")


            # Panel for Time
                panel_time = Panel(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", title="Current Time", border_style="yellow", width=40)

                # Panels for BANK-NIFTY and NIFTY-50
                panel_banknifty = Panel(f"Instrument: BANK-NIFTY\nLTP: {ltp[0]:.2f}", title="BANKNIFTY", border_style="blue", width=40)
                panel_nifty50 = Panel(f"Instrument: NIFTY-50\nLTP: {ltp[1]:.2f}", title="NIFTY-50", border_style="green", width=40)

                # Columns for Bank and Nifty tables
                bank_columns = Columns([bank_ce_table, bank_pe_table], equal=True, expand=True)
                nifty_columns = Columns([nifty_ce_table, nifty_pe_table], equal=True, expand=True)

                # Print main table and panels
                console.print(table, justify="center")
                console.print(Columns([panel_time, panel_banknifty, panel_nifty50], expand=True), justify="center")
                console.print("Bank Nifty Options:", justify="center")
                console.print(bank_columns, justify="center")
                console.print("Nifty 50 Options:", justify="center")
                console.print(nifty_columns, justify="center")

                # Print the updated table and panels side by side
                # console.clear()

        except KeyError as e:
            # print(f"KeyError: {e}")
            pass

    except KeyError as e:
        # print(f"KeyError: {e}")
        return None


if __name__ == "__main__":
    data_dict = {
    "feeds": {
        "NSE_INDEX|Nifty Bank": {
            "ff": {
                "indexFF": {
                    "ltpc": {
                        "ltp": 48084.1,
                        "ltt": "1704441162000",
                        "cp": 48195.85
                    },
                    "marketOHLC": {
                        "ohlc": [
                            {
                                "interval": "1d",
                                "open": 48245.55,
                                "high": 48381.95,
                                "low": 48030.2,
                                "close": 48084.1,
                                "ts": "1704393000000"
                            },
                            {
                                "interval": "I1",
                                "open": 48083.8,
                                "high": 48102.5,
                                "low": 48082.6,
                                "close": 48091.85,
                                "ts": "1704441060000"
                            },
                            {
                                "interval": "I1",
                                "open": 48090.85,
                                "high": 48090.85,
                                "low": 48080.7,
                                "close": 48084.1,
                                "ts": "1704441120000"
                            },
                            {
                                "interval": "I30",
                                "open": 48090.9,
                                "high": 48113.8,
                                "low": 48047.5,
                                "close": 48085.75,
                                "ts": "1704438900000"
                            },
                            {
                                "interval": "I30",
                                "open": 48086.75,
                                "high": 48102.5,
                                "low": 48061.1,
                                "close": 48084.1,
                                "ts": "1704440700000"
                            }
                        ]
                    },
                    "yh": 48636.45,
                    "yl": 38613.15
                }
            }
        },
        "NSE_INDEX|Nifty 50": {
            "ff": {
                "indexFF": {
                    "ltpc": {
                        "ltp": 21694.25,
                        "ltt": "1704441162000",
                        "cp": 21658.6
                    },
                    "marketOHLC": {
                        "ohlc": [
                            {
                                "interval": "1d",
                                "open": 21705.75,
                                "high": 21749.6,
                                "low": 21665.9,
                                "close": 21694.25,
                                "ts": "1704393000000"
                            },
                            {
                                "interval": "I1",
                                "open": 21693.3,
                                "high": 21699.45,
                                "low": 21693.3,
                                "close": 21696.35,
                                "ts": "1704441060000"
                            },
                            {
                                "interval": "I1",
                                "open": 21695.55,
                                "high": 21696.3,
                                "low": 21692.4,
                                "close": 21694.25,
                                "ts": "1704441120000"
                            },
                            {
                                "interval": "I30",
                                "open": 21690.5,
                                "high": 21700.15,
                                "low": 21678.25,
                                "close": 21695.85,
                                "ts": "1704438900000"
                            },
                            {
                                "interval": "I30",
                                "open": 21695.7,
                                "high": 21699.45,
                                "low": 21688.1,
                                "close": 21694.25,
                                "ts": "1704440700000"
                            }
                        ]
                    },
                    "yh": 21834.35,
                    "yl": 16828.35
                }
            }
        }
    }
}
    make_rich_table(data_dict)


#[{'instrument_key': 'NSE_FO|35482', 'exchange_token': '35482', 'tradingsymbol': 'BANKNIFTY2411747000CE', 'name': '', 'last_price': '670.65', 'expiry': '2024-01-17', 'strike': '47000.0', 'tick_size': '0.05', 'lot_size': '15', 'instrument_type': 'OPTIDX', 'option_type': 'CE', 'exchange': 'NSE_FO'}, {'instrument_key': 'NSE_FO|35484', 'exchange_token': '35484', 'tradingsymbol': 'BANKNIFTY2411747100CE', 'name': '', 'last_price': '601.55', 'expiry': '2024-01-17', 'strike': '47100.0', 'tick_size': '0.05', 'lot_size': '15', 'instrument_type': 'OPTIDX', 'option_type': 'CE', 'exchange': 'NSE_FO'}, {'instrument_key': 'NSE_FO|35486', 'exchange_token': '35486', 'tradingsymbol': 'BANKNIFTY2411747200CE', 'name': '', 'last_price': '532.95', 'expiry': '2024-01-17', 'strike': '47200.0', 'tick_size': '0.05', 'lot_size': '15', 'instrument_type': 'OPTIDX', 'option_type': 'CE', 'exchange': 'NSE_FO'}, {'instrument_key': 'NSE_FO|35492', 'exchange_token': '35492', 'tradingsymbol': 'BANKNIFTY2411747300CE', 'name': '', 'last_price': '469.7', 'expiry': '2024-01-17', 'strike': '47300.0', 'tick_size': '0.05', 'lot_size': '15', 'instrument_type': 'OPTIDX', 'option_type': 'CE', 'exchange': 'NSE_FO'}, {'instrument_key': 'NSE_FO|35494', 'exchange_token': '35494', 'tradingsymbol': 'BANKNIFTY2411747400CE', 'name': '', 'last_price': '412.7', 'expiry': '2024-01-17', 'strike': '47400.0', 'tick_size': '0.05', 'lot_size': '15', 'instrument_type': 'OPTIDX', 'option_type': 'CE', 'exchange': 'NSE_FO'}, {'instrument_key': 'NSE_FO|35496', 'exchange_token': '35496', 'tradingsymbol': 'BANKNIFTY2411747500CE', 'name': '', 'last_price': '358.6', 'expiry': '2024-01-17', 'strike': '47500.0', 'tick_size': '0.05', 'lot_size': '15', 'instrument_type': 'OPTIDX', 'option_type': 'CE', 'exchange': 'NSE_FO'}]