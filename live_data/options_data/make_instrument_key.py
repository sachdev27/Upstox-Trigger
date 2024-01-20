from live_data import instrument
import json,upstox_client
import connections,os


from dotenv import load_dotenv
load_dotenv()

API_VERSION = '2.0'
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")



def make_configuration():
    configuration = upstox_client.Configuration()
    configuration.access_token = ACCESS_TOKEN
    return configuration

def update_option_instrument_key(data_dict):
    # for data in data_dict:
    #     print(data,data_dict[data])

    ltp_bank =  data_dict['NSE_INDEX:Nifty Bank']['last_price']
    ltp_nifty = data_dict["NSE_INDEX:Nifty 50"]['last_price']
    # print(ltp_bank,ltp_nifty)
    bank_options = instrument.categorize_options("BANKNIFTY", ltp_bank)
    nifty_options = instrument.categorize_options("NIFTY", ltp_nifty)

    bank_options = bank_options[0] + bank_options[1]
    nifty_options = nifty_options[0] + nifty_options[1]
    all_options = bank_options + nifty_options

    # for x in all_options:
    #     print(x['tradingsymbol'],x['strike'])

    # Prepare instrument keys for fetching real-time LTP data
    instrument_keys = ','.join([option['instrument_key'] for option in all_options])
    with open ("instrument_key.txt",'w') as k:
        k.write(instrument_keys)



def worker():
    instrument_key = 'NSE_INDEX|Nifty Bank,NSE_INDEX|Nifty 50'
    # # Get LTP
    configuration = make_configuration()
    data = connections.ltp(API_VERSION,configuration, instrument_key)
    update_option_instrument_key(data.to_dict()['data'])


if __name__ == "__main__":
    worker()
