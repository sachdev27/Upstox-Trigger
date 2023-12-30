from __future__ import print_function
import upstox_client
from upstox_client.rest import ApiException
from pprint import pprint


def get_profile(api_version, configuration):
    api_instance = upstox_client.UserApi(
        upstox_client.ApiClient(configuration))
    api_response = api_instance.get_profile(api_version)
    return api_response


def get_funds_and_margin(api_version, configuration):
    api_instance = upstox_client.UserApi(
        upstox_client.ApiClient(configuration))
    api_response = api_instance.get_user_fund_margin(api_version)
    return api_response


def get_positions(api_version, configuration):
    api_instance = upstox_client.PortfolioApi(
        upstox_client.ApiClient(configuration))
    api_response = api_instance.get_positions(api_version)
    return api_response


def get_holdings(api_version, configuration):
    api_instance = upstox_client.PortfolioApi(
        upstox_client.ApiClient(configuration))
    api_response = api_instance.get_holdings(api_version)
    return api_response
