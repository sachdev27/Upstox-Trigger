import requests
import csv
from datetime import datetime,timedelta
import gzip,json
import shutil,os

csv_filename='instrument_list.csv'
cache_filename='options_cache.json'

def get_instrument_list(csv_filename='instrument_list.csv'):
    # URL pointing to the gzipped CSV file
    url = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.csv.gz"

    # Sending a GET request to the URL
    response = requests.get(url)

    # Check if the request was successful
    if response.status_code == 200:
        # Open a temporary file to write the gzipped content
        with open("tempfile.gz", "wb") as temp_file:
            temp_file.write(response.content)

        # Open the temporary file in gzip mode and read its contents
        with gzip.open("tempfile.gz", "rb") as gz_file:
            # Open or create the target CSV file
            with open(csv_filename, "wb") as csv_file:
                # Copy content from gzip file to csv file
                shutil.copyfileobj(gz_file, csv_file)
    else:
        print(f"Failed to download the file: Status code {response.status_code}")

def create_trading_symbol_mapping(options_data='options_cache.json', json_filename='trading_symbol.json'):
    # Create a mapping of trading symbols to instrument keys

    # Read the input JSON file
    with open(options_data, 'r') as json_file:
            options_data = json.load(json_file)

    trading_symbol_mapping = {}
    for option_type in ['CE', 'PE']:
        for option in options_data.get(option_type, []):
            instrument_key = option.get('instrument_key')
            tradingsymbol = option.get('tradingsymbol')
            if instrument_key and tradingsymbol:
                trading_symbol_mapping[instrument_key] = tradingsymbol

    # Save the mapping to a JSON file
    with open(json_filename, 'w') as json_file:
        json.dump(trading_symbol_mapping, json_file, indent=4)

    print(f"Mapping saved to {json_filename}")




def round_to_nearest(number, diff):
    rounded_down = (number // diff) * diff
    rounded_up = rounded_down + diff
    return rounded_down if number % diff < diff / 2 else rounded_up


def get_nearest_expiry(instrument_name):
    today = datetime.today()

    if instrument_name == "BANKNIFTY" :
        # Calculate days to the nearest Wednesday (weekday 2)
        days_until_wednesday = (2 - today.weekday() + 7) % 7
        nearest_expiry = today + timedelta(days=days_until_wednesday)

    if instrument_name == "NIFTY" :
        # Calculate days to the nearest Thursday (weekday 3)
        days_until_thursday = (3 - today.weekday() + 7) % 7
        nearest_expiry = today + timedelta(days=days_until_thursday)

    # Format the date as per your requirement
    formatted_expiry = nearest_expiry.strftime('%Y-%m-%d')

    return formatted_expiry


def get_instrument_token_eq(instrument_name, csv_filename='instrument_list.csv'):
    with open(csv_filename, 'r') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            if row["exchange"] == "NSE_EQ" and row["instrument_type"] == "EQUITY" and row['name'] == instrument_name:
                return row

    print("Instrument Not Found")



def cache_options_data(csv_filename, cache_filename):
    options_data = {'CE': [], 'PE': []}
    with open(csv_filename, 'r') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            if row['exchange'] == "NSE_FO" and row['instrument_type'] in ['OPTIDX', 'OPTSTK'] and ( row['tradingsymbol'].startswith("NIFTY") or row['tradingsymbol'].startswith("BANKNIFTY") ) :
                if row['option_type'] == 'CE':
                    options_data['CE'].append(row)
                elif row['option_type'] == 'PE':
                    options_data['PE'].append(row)

    # Write to cache file
    with open(cache_filename, 'w') as cache_file:
        json.dump(options_data, cache_file)

def categorize_options(instrument_name, ticker, csv_filename='instrument_list.csv', cache_filename='options_cache.json'):
    # if not os.path.exists(cache_filename) or os.path.getsize(cache_filename) == 0:
    cache_options_data(csv_filename, cache_filename)

    with open(cache_filename, 'r') as cache_file:
        options_data = json.load(cache_file)

    nearest_expiry_date = get_nearest_expiry(instrument_name)
    # print(nearest_expiry_date)
    ce_options = [row for row in options_data['CE'] if row['tradingsymbol'].startswith(instrument_name) and row["expiry"] == nearest_expiry_date]
    pe_options = [row for row in options_data['PE'] if row['tradingsymbol'].startswith(instrument_name) and row["expiry"] == nearest_expiry_date]


    # print(ce_options,pe_options)
    if ce_options and pe_options:
        if instrument_name == 'BANKNIFTY':
            diff = 100
        elif instrument_name in ['NIFTY', 'FINNIFTY']:
            diff = 50


        ticker_rounded = round_to_nearest(ticker, diff)
        # Filter CE options that are less than or equal to the rounded ticker
        filtered_ce_options = [opt for opt in ce_options if float(opt['strike']) <= ticker_rounded]
        # Filter PE options that are greater than or equal to the rounded ticker
        filtered_pe_options = [opt for opt in pe_options if float(opt['strike']) >= ticker_rounded]

        # Sort options by strike price
        sorted_ce_options = sorted(filtered_ce_options, key=lambda x: float(x['strike']), reverse=True)
        sorted_pe_options = sorted(filtered_pe_options, key=lambda x: float(x['strike']))

        # Select top 5 options
        top_5_ce_options = sorted_ce_options[:5]
        top_5_pe_options = sorted_pe_options[:5]

        return top_5_ce_options, top_5_pe_options
    else:
        print("No options data found for the given instrument.")
        return [], []


# -----------------------------------------------

if __name__ == "__main__":
    # Example usage:
    # top_5_ce, top_5_pe = categorize_options("NIFTY",21700)

    # print("Top 5 Call (CE) Options:")
    # for option in top_5_ce:
    #     print(option)

    # print("\nTop 5 Put (PE) Options:")
    # for option in top_5_pe:
    #     print(option)
    # get_instrument_list()
    # cache_options_data(csv_filename=csv_filename,cache_filename=cache_filename)
    # create_trading_symbol_mapping(cache_filename)
    pass
