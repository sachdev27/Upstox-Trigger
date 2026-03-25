from order.order_definition import MarketOrder, LimitOrder, StopLossOrder, StopLossMarketOrder
from connections import *
import os

API_VERSION = os.getenv("API_VERSION")

# Place order CP
# order_details_cp = {
#   "quantity": 50,
#   "product": "D",
#   "validity": "DAY",
#   "price": 262,
#   "tag": "string",
#   "instrument_token": instrument_key,
#   "order_type": "LIMIT",
#   "transaction_type": "BUY",
#   "disclosed_quantity": 0,
#   "trigger_price": 0,
#   "is_amo": True
# }
# instrument_key = "NSE_FO|40755"

# order_details_cp = {
#   "quantity": 50,
#   "product": "D",
#   "validity": "DAY",
#   "price": 26,
#   "tag": "string",
#   "instrument_token": instrument_key,
#   "order_type": "LIMIT",
#   "transaction_type": "BUY",
#   "disclosed_quantity": 0,
#   "trigger_price": 0,
#   "is_amo": False
# }


def place_order(configuration, instrument_token_obj, lot_size, transaction_type,price=0,order_type="LIMIT", product="D", trigger_price=0, is_amo=False):
    """
    Function to place an order with essential parameters.

    :param api_version: API version for Upstox SDK
    :param configuration: Configuration details for Upstox API
    :param instrument_token: Instrument token for the order
    :param quantity: Quantity of shares to buy/sell
    :param transaction_type: 'BUY' or 'SELL'
    :param order_type: Type of order - 'MARKET', 'LIMIT', 'SL', 'SL-M'
    :param product: Type of product - 'I' for Intraday, 'D' for Delivery
    :param price: Price for limit orders
    :param trigger_price: Trigger price for stop-loss orders
    :param is_amo: Boolean indicating if it's an After Market Order
    :return: Response from order placement
    """
    api_version = API_VERSION
    quantity = lot_size
    order = None
    if order_type == 'MARKET':
        order = MarketOrder(instrument_token_obj, quantity, transaction_type, product, price=price, is_amo=is_amo)
    elif order_type == 'LIMIT':
        order = LimitOrder(instrument_token_obj, quantity, transaction_type, product, price=price, is_amo=is_amo)
    elif order_type == 'SL':
        order = StopLossOrder(instrument_token_obj, quantity, transaction_type, product, trigger_price=trigger_price, is_amo=is_amo)
    elif order_type == 'SL-M':
        order = StopLossMarketOrder(instrument_token_obj, quantity, transaction_type, product, trigger_price=trigger_price, is_amo=is_amo)

    if order:
        return order.exec_order(api_version,configuration=configuration)
    else:
        raise ValueError("Invalid order type specified")

# Example usage
# response = place_order(API_VERSION, configuration, 'NSE_EQ|INE528G01035', 1, 'BUY', 'MARKET', 'I')
