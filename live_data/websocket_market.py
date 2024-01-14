# Import necessary modules
import asyncio
import json,os,time
import ssl
import upstox_client
import websockets
from chart import CandlestickChart
from google.protobuf.json_format import MessageToDict
from live_data.rich_table import make_rich_table

from login.login import do_login

# ------ Load .env File
from dotenv import load_dotenv
load_dotenv()
do_login()
# ---------------
token = os.environ["ACCESS_TOKEN"]
# candlestick_chart = CandlestickChart()
from live_data.options_data import options_csv


import live_data.marketDataFeed_pb2 as pb


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


async def fetch_market_data():
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

        ## Indices
        indices_instrument = ["NSE_INDEX|Nifty Bank", "NSE_INDEX|Nifty 50"]

        oi_keys = ''
        ## OI Instrument
        with open("instrument_key.txt",'r') as k:
            keys = k.readline()
        oi_keys = keys.split(",")

        All_instruments = indices_instrument + oi_keys

        # Data to be sent over the WebSocket
        data = {
            "guid": "someguid",
            "method": "sub",
            "data": {
                "mode": "full",
                "instrumentKeys": All_instruments
            }
        }





        # Convert data to binary and send over WebSocket
        binary_data = json.dumps(data).encode('utf-8')
        await websocket.send(binary_data)

        # Continuously receive and decode data from WebSocket
        while True:
            message = await websocket.recv()
            decoded_data = decode_protobuf(message)

            # Convert the decoded data to a dictionary
            data_dict = MessageToDict(decoded_data)
            # print(data_dict)
            make_rich_table(data_dict=data_dict)
            options_csv.make_options_data(data_dict=data_dict)

            # ltp_bank = data_dict['feeds']['NSE_INDEX|Nifty Bank']['ff']['indexFF']['ltpc']['ltp']
            # ohlc_bank = data_dict['feeds']['NSE_INDEX|Nifty Bank']['ff']['indexFF']['marketOHLC']['ohlc']

            # ltp_n50 = data_dict['feeds']["NSE_INDEX|Nifty 50"]['ff']['indexFF']['ltpc']['ltp']
            # ohlc_n50 = data_dict['feeds']["NSE_INDEX|Nifty 50"]['ff']['indexFF']['marketOHLC']['ohlc']

            # # Determine the interval
            # interval_bank = data_dict['feeds']['NSE_INDEX|Nifty Bank']['ff']['indexFF']['marketOHLC']['ohlc'][0]['interval']
            # interval_n50 = data_dict['feeds']["NSE_INDEX|Nifty 50"]['ff']['indexFF']['marketOHLC']['ohlc'][0]['interval']

            # # Update the candlestick chart based on the interval
            # if interval_bank == '1d':
            #     # Handle 1d interval (single candle for the previous day)
            #     candlestick_chart.update_chart(ltp_bank, ohlc_bank[0], ltp_n50, ohlc_n50[0])
            # elif interval_bank == 'I1':
            #     # Handle I1 interval (two candles for the current and preceding 1-minute intervals)
            #     candlestick_chart.update_chart(ltp_bank, ohlc_bank[1], ltp_n50, ohlc_n50[1])
            # elif interval_bank == 'I30':
            #     # Handle I30 interval (two candles for the current and preceding 30-minute intervals)
            #     candlestick_chart.update_chart(ltp_bank, ohlc_bank[1], ltp_n50, ohlc_n50[1])

            # Print the dictionary representation




# Run the asynchronous market data fetching function
asyncio.run(fetch_market_data())
