import fileinput
import os
import upstox_client
from check_token_expiry import token_expired
from dotenv import load_dotenv
import logging

logging.basicConfig()


load_dotenv()

API_VERSION = os.getenv("API_VERSION")
CLIENT_ID = os.getenv("API_KEY")
CLIENT_SECRET = os.getenv("API_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI")
AUTH_CODE = os.getenv("AUTH_CODE")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")




# Get Auth Code
# https://api.upstox.com/v2/login/authorization/dialog?response_type=code&client_id=<API_KEY>&redirect_uri=<REDIRECT_URI>


def update_env_file(file_path, key, new_value):
    with fileinput.FileInput(file_path, inplace=True, backup='.bak') as file:
        for line in file:
            if line.startswith(f'{key}='):
                print(f'{key}="{new_value}"')
            else:
                print(line, end='')


def login_and_authorize(api_instance,api_version, client_id, client_secret, redirect_uri, auth_code):
    api_response = api_instance.token(api_version, code=auth_code, client_id=client_id,
                                      client_secret=client_secret, redirect_uri=redirect_uri, grant_type="authorization_code")
    return api_response.access_token


# Login to the Upstox and get access_token
def set_token(api_instance):
    token =  login_and_authorize(
        api_instance=api_instance,
        api_version=API_VERSION,
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        redirect_uri=REDIRECT_URI,
        auth_code=AUTH_CODE,
    )
    update_env_file(".env","ACCESS_TOKEN",token)
    return token


def is_token_expired():
    # Configure OAuth2 access token for authorization: OAUTH2
    configuration = upstox_client.Configuration()
    api_instance = upstox_client.LoginApi(
            upstox_client.ApiClient(configuration))

    if token_expired(ACCESS_TOKEN):
        tkn = set_token(api_instance=api_instance)
        configuration.access_token = tkn

