import os
import upstox_client
from upstox_client.rest import ApiException
from dotenv import load_dotenv
import json

def troubleshoot():
    load_dotenv()

    access_token = os.getenv('ACCESS_TOKEN')
    if not access_token:
        print("❌ Error: UPSTOX_ACCESS_TOKEN not found in .env")
        return

    # Configuration
    configuration = upstox_client.Configuration()
    configuration.access_token = access_token
    api_client = upstox_client.ApiClient(configuration)

    options_api = upstox_client.OptionsApi(api_client)

    instrument_key = "NSE_INDEX|Nifty 50"
    print(f"--- 🔍 Troubleshooting Index: {instrument_key} ---")

    try:
        # 1. Get Contracts
        print("\n1. Fetching Option Contracts...")
        contracts_res = options_api.get_option_contracts(instrument_key)
        contracts = contracts_res.to_dict().get('data', [])

        if not contracts:
            print("❌ No contracts found for this instrument.")
            return

        expiries = sorted(list(set(c['expiry'] for c in contracts)))
        print(f"✅ Found {len(expiries)} expiries: {expiries[:5]}...")

        nearest_expiry = expiries[0]
        print(f"👉 Selecting nearest expiry: {nearest_expiry}")

        # 2. Get Option Chain
        print(f"\n2. Fetching Option Chain for {nearest_expiry}...")
        chain_res = options_api.get_put_call_option_chain(instrument_key, nearest_expiry)
        chain_data = chain_res.to_dict().get('data', [])

        if not chain_data:
            print("❌ Option Chain data is empty.")
            return

        print(f"✅ Received {len(chain_data)} strikes.")

        # 3. Inspect a few strikes
        print("\n3. Inspecting middle 3 strikes:")
        mid = len(chain_data) // 2
        for i in range(mid-1, mid+2):
            s = chain_data[i]
            sp = s.get('strike_price')
            ce = s.get('call_options') or {}
            pe = s.get('put_options') or {}

            ce_ltp = ce.get('market_data', {}).get('ltp')
            pe_ltp = pe.get('market_data', {}).get('ltp')

            print(f"   - Strike {sp}: CE LTP={ce_ltp}, PE LTP={pe_ltp}")
            if ce:
                print(f"     CE Greeks: {ce.get('option_greeks')}")
            if pe:
                print(f"     PE Greeks: {pe.get('option_greeks')}")

        print("\n--- 🏁 Troubleshooting Complete ---")

    except ApiException as e:
        print(f"❌ API Exception: {e}")
    except Exception as e:
        print(f"❌ General Exception: {e}")

if __name__ == "__main__":
    troubleshoot()
