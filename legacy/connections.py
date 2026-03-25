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


def place_order(api_version, configuration, order_details):
    api_instance = upstox_client.OrderApi(
        upstox_client.ApiClient(configuration))
    api_response = api_instance.place_order(order_details, api_version)
    return api_response


def modify_order(api_version, configuration, order_details):
    api_instance = upstox_client.OrderApi(
        upstox_client.ApiClient(configuration))
    api_response = api_instance.modify_order(order_details, api_version)
    return api_response


def get_trades_by_order(api_version, configuration, order_id):
    api_instance = upstox_client.OrderApi(
        upstox_client.ApiClient(configuration))
    api_response = api_instance.get_trades_by_order(order_id, api_version)
    return api_response


def get_trade_history(api_version, configuration):
    api_instance = upstox_client.OrderApi(
        upstox_client.ApiClient(configuration))
    api_response = api_instance.get_trade_history(api_version)
    return api_response


def get_order_book(api_version, configuration):
    api_instance = upstox_client.OrderApi(
        upstox_client.ApiClient(configuration))
    api_response = api_instance.get_order_book(api_version)
    return api_response


def get_order_details(api_version, configuration, order_id):
    api_instance = upstox_client.OrderApi(
        upstox_client.ApiClient(configuration))
    api_response = api_instance.get_order_details(
        api_version, order_id=order_id)
    return api_response


def get_full_market_quote(api_version, configuration, instrument_key):
    api_instance = upstox_client.MarketQuoteApi(
        upstox_client.ApiClient(configuration))
    api_response = api_instance.get_full_market_quote(
        instrument_key, api_version)
    return api_response


def get_market_quote_ohlc(api_version, configuration, instrument_key, interval):
    api_instance = upstox_client.MarketQuoteApi(
        upstox_client.ApiClient(configuration))
    api_response = api_instance.get_market_quote_ohlc(
        instrument_key, interval, api_version)
    return api_response


def ltp(api_version, configuration, instrument_key):
    api_instance = upstox_client.MarketQuoteApi(
        upstox_client.ApiClient(configuration))
    api_response = api_instance.ltp(instrument_key, api_version)
    return api_response


def get_trade_wise_profit_and_loss_data(api_version, configuration, segment, year):
    api_instance = upstox_client.TradeProfitAndLossApi(
        upstox_client.ApiClient(configuration))
    api_response = api_instance.get_trade_wise_profit_and_loss_data(
        segment, year, 1, 3000, api_version)
    return api_response


def get_profit_and_loss_charges(api_version, configuration, segment, year):
    api_instance = upstox_client.TradeProfitAndLossApi(
        upstox_client.ApiClient(configuration))
    api_response = api_instance.get_profit_and_loss_charges(
        segment, year, api_version)
    return api_response