import upstox_client
import os,csv
from datetime import datetime
from time import sleep

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



def intraday_historical():
    # Get intra-day candle data
    interval = "1minute"
    instrument_key_nifty = os.environ['NIFTY']
    instrument_key_bank = os.environ['BANKNIFTY']
    configuration = make_configuration()
    csv_nifty_filename = f'Historical/Nifty/Intraday-{datetime.today().strftime("%d-%m-%Y")}.csv'
    csv_banknifty_filename = f'Historical/Banknifty/Intraday-{datetime.today().strftime("%d-%m-%Y")}.csv'

    intra_day_nifty_candle_data = get_intra_day_candle_data(
        api_version, configuration, instrument_key_nifty, interval)

    intra_day_banknifty_candle_data = get_intra_day_candle_data(
        api_version, configuration, instrument_key_bank, interval)


    save_ohlc_data_to_csv(intra_day_nifty_candle_data,csv_nifty_filename)
    save_ohlc_data_to_csv(intra_day_banknifty_candle_data,csv_banknifty_filename)


def main():
    intraday_historical()


if __name__ == "__main__":
    main()
