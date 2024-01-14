import login.login as login
import upstox_client,os
from order.order import place_order
import connections

import os

API_VERSION = os.getenv("API_VERSION")

configuration = login.do_login()



# instrument_key = "NSE_INDEX|Nifty 50"
# instrument_key = "NSE_EQ|INE848E01016"
# instrument_key = "NSE_INDEX|Nifty BANK"



# response = get_profile(api_version=api_version,configuration=configuration)
# response = connections.get_funds_and_margin(api_version=api_version,configuration=configuration)
# response = connections.get_holdings(api_version=api_version,configuration=configuration)
# response = connections.get_positions(api_version=api_version,configuration=configuration)


# # Get full market quote
# full_market_quote = get_full_market_quote(
#     api_version, configuration, instrument_key)
# pprint(full_market_quote)

# # Get market quote OHLC
# interval = "1d"
# market_quote_ohlc = get_market_quote_ohlc(
#     api_version, configuration, instrument_key, interval)
# pprint(market_quote_ohlc)

# instrument_key = 'NSE_FO|40755,NSE_FO|40757'
# # # Get LTP
# ltp_response = connections.ltp(API_VERSION,configuration, instrument_key)
# print(ltp_response)

# response = place_order(configuration,instrument_key,50, 'BUY',25)
# print(response)