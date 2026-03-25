import upstox_client
import os,csv
from datetime import datetime
import time

# ------ Load .env File
from dotenv import load_dotenv
load_dotenv()
# ---------------
ACCESS_TOKEN = os.environ["ACCESS_TOKEN"]
API_VERSION = os.getenv("API_VERSION")
api_version = API_VERSION


def make_configuration():
    configuration = upstox_client.Configuration()
    configuration.access_token = ACCESS_TOKEN
    return configuration




def get_intra_day_candle_data(api_version, configuration, instrument_key, interval):
    api_instance = upstox_client.HistoryApi(
        upstox_client.ApiClient(configuration))
    api_response = api_instance.get_intra_day_candle_data(
        instrument_key, interval, api_version)
    return api_response


def save_ohlc_data_to_csv(api_response, csv_filename):
    try:
        api_response = api_response.to_dict()
        data = api_response['data']['candles']

        #  Reverse the order of data for initial saving
        data = data[::-1]
        # Open a new CSV file to write data
        # print(csv_filename)
        os.makedirs(os.path.dirname(csv_filename), exist_ok=True)
        with open(csv_filename, mode='w', newline='') as file:
            writer = csv.writer(file)
            # Write the headers
            writer.writerow(['Datetime', 'Open', 'High', 'Low', 'Close', 'Volume', 'Open Intrest'])
            # Write the data
            for row in data:
                writer.writerow(row)
        # print(f"Data saved to {csv_filename}")
    except KeyError as e:
        print(f"Error: Missing key in response - {e}")



def intraday_historical(instrument_key,csv_filename):
    # Get intra-day candle data
    interval = "1minute"
    configuration = make_configuration()
    intra_day_candle_data = get_intra_day_candle_data(
        api_version, configuration, instrument_key, interval)

    save_ohlc_data_to_csv(intra_day_candle_data,csv_filename)


def main_api_fetch_loop(instrument_key):
    while True:
        if csv_filename == None:
            csv_filename = f'Historical/{instrument_key}/Intraday-{datetime.today().strftime("%d-%m-%Y")}.csv'

        current_minute = datetime.now().minute
        intraday_historical(instrument_key=instrument_key,csv_filename=csv_filename)
        # Calculate sleep time to the start of the next minute
        sleep_time = 60 - datetime.now().second
        print(f"Data updated. Next update in {sleep_time} seconds.")
        time.sleep(sleep_time)
        # Ensure we wait until the next minute even if the operation took less than a minute
        while datetime.now().minute == current_minute:
            time.sleep(1)


def main_single_fetch(instrument_key,csv_filename):
    if csv_filename == None:
        csv_filename = f'Historical/{instrument_key}/Intraday-{datetime.today().strftime("%d-%m-%Y")}.csv'
    intraday_historical(instrument_key=instrument_key,csv_filename=csv_filename)


if __name__ == "__main__":
    instrument_key_nifty,instrument_key_bank = os.environ['NIFTY'],os.environ['BANKNIFTY']
    main_api_fetch_loop(instrument_key_nifty)
    main_api_fetch_loop(instrument_key_bank)
