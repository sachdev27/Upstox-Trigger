import login
import upstox_client,os
import connections

api_version = os.getenv("API_VERSION")


login.is_token_expired()
configuration = upstox_client.Configuration()
configuration.access_token = os.getenv("ACCESS_TOKEN")



# response = connections.get_profile(api_version=api_version,configuration=configuration)
# response = connections.get_funds_and_margin(api_version=api_version,configuration=configuration)
# response = connections.get_holdings(api_version=api_version,configuration=configuration)
# response = connections.get_positions(api_version=api_version,configuration=configuration)