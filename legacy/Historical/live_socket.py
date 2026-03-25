# Import necessary modules
import asyncio
import json,os
import ssl
import upstox_client
import websockets
import csv
from datetime import datetime, timezone, timedelta
from Historical.intraday_data import main_single_fetch
from google.protobuf.json_format import MessageToDict
from Historical.instrument_data import intrument_intra_data

from login.login import do_login

# ------ Load .env File
from dotenv import load_dotenv
load_dotenv()
do_login()
# ---------------
token = os.environ["ACCESS_TOKEN"]
from live_data.options_data import options_csv



import live_data.marketDataFeed_pb2 as pb

def get_current_timestamp():
    # Assuming you want the timezone offset of +05:30
    tz = timezone(timedelta(hours=5, minutes=30))
    current_time = datetime.now(tz)
    # Format with colon in the timezone offset
    timestamp = current_time.strftime("%Y-%m-%dT%H:%M:%S%z")
    return timestamp[:-2] + ':' + timestamp[-2:]

def get_market_data_feed_authorize(api_version, configuration):
    """Get authorization for market data feed."""
    api_instance = upstox_client.WebsocketApi(
        upstox_client.ApiClient(configuration))
    api_response = api_instance.get_market_data_feed_authorize(api_version)
    return api_response


def decode_protobuf(buffer):
    """Decode protobuf message."""
    feed_response = pb.FeedResponse()
    feed_response.ParseFromString(buffer)
    return feed_response


async def fetch_market_data(instrument,symbol):
    """Fetch market data using WebSocket and print it."""

    # Create default SSL context
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE

    # Configure OAuth2 access token for authorization
    configuration = upstox_client.Configuration()

    api_version = '2.0'
    configuration.access_token = token

    # Get market data feed authorization
    response = get_market_data_feed_authorize(api_version, configuration)

    # Connect to the WebSocket with SSL context
    async with websockets.connect(response.data.authorized_redirect_uri, ssl=ssl_context) as websocket:
        print('Connection established')

        await asyncio.sleep(1)  # Wait for 1 second

# ------------------Historical Intraday Data----------------------
        csv_filename = f'Historical/{symbol}/Intraday-{datetime.today().strftime("%d-%m-%Y")}.csv'
        main_single_fetch(instrument_key=instrument,csv_filename=csv_filename)

# ------------------Instrument----------------------
        # Data to be sent over the WebSocket
        data = {
            "guid": "someguid",
            "method": "sub",
            "data": {
                "mode": "full",
                "instrumentKeys": [instrument]
            }
        }

        # Convert data to binary and send over WebSocket
        binary_data = json.dumps(data).encode('utf-8')
        await websocket.send(binary_data)

# ------------------Instrument Data----------------------

        instrument_data = intrument_intra_data(0,10000000000)

# ------------------LIVE SOCKET----------------------
        # Continuously receive and decode data from WebSocket
        while True:
            try:
                message = await websocket.recv()
                decoded_data = decode_protobuf(message)

                # Convert the decoded data to a dictionary
                data_dict = MessageToDict(decoded_data)
                # print(json.dumps(data_dict))

                try:
                    ltp = data_dict['feeds'][instrument]['ff']['marketFF']['ltpc']['ltp']
                    open_price = data_dict['feeds'][instrument]['ff']['marketFF']['marketOHLC']['ohlc'][1]['open']
                    high = data_dict['feeds'][instrument]['ff']['marketFF']['marketOHLC']['ohlc'][1]['high']
                    low = data_dict['feeds'][instrument]['ff']['marketFF']['marketOHLC']['ohlc'][1]['low']
                    close = data_dict['feeds'][instrument]['ff']['marketFF']['marketOHLC']['ohlc'][1]['close']
                except Exception as e:
                    # print(json.dumps(data_dict['feeds'][instrument]['ff'],indent=4),e)
                    pass

                instrument_data.update_high(close)
                instrument_data.update_low(close)
                instrument_data.set_time(get_current_timestamp())

                print(instrument,symbol,instrument_data.get_high(),instrument_data.get_low(),instrument_data.percentage_check_bwn_high_and_low())

                with open(csv_filename, 'w', newline='') as csvfile:
                    fieldnames = ['Datetime','Open', 'High', 'Low', 'Close']
                    csv_writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                                    # Update the CSV file for Nifty Bank
                    csv_writer.writerow({
                        'Datetime': get_current_timestamp(),
                        'Open': f"{open_price:.2f}",
                        'High': f"{high:.2f}",
                        'Low': f"{low:.2f}",
                        'Close': f"{close:.2f}"
                    })
            except Exception as e:
                print("Key Error:",e)


# Run the asynchronous market data fetching function
def main(instrument,symbol):
    asyncio.run(fetch_market_data(instrument=instrument,symbol=symbol))

if __name__ == "__main__":
    main("NSE_INDEX|Nifty 50","NSE_INDEX|Nifty 50")