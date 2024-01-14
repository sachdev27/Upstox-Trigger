import connections

class Order:
    def __init__(self, instrument_token, quantity, transaction_type, product='D', validity='DAY', price=0, tag='string', disclosed_quantity=0, trigger_price=0, is_amo=False):
        self.instrument_token = instrument_token
        self.quantity = quantity
        self.transaction_type = transaction_type
        self.product = product
        self.validity = validity
        self.price = price
        self.tag = tag
        self.disclosed_quantity = disclosed_quantity
        self.trigger_price = trigger_price
        self.is_amo = is_amo

    def exec_order(self, api_version, configuration):
        # Implementation to place order using Upstox SDK
        order_details = {
            'quantity': self.quantity,
            'product': self.product,
            'validity': self.validity,
            'price': self.price,
            'tag': self.tag,
            'instrument_token': self.instrument_token,
            'order_type': self.get_order_type(),
            'transaction_type': self.transaction_type,
            'disclosed_quantity': self.disclosed_quantity,
            'trigger_price': self.trigger_price,
            'is_amo': self.is_amo
        }
        return connections.place_order(api_version, configuration, order_details)

    def get_order_type(self):
        # This method should be overridden by subclasses
        raise NotImplementedError


class MarketOrder(Order):
    def get_order_type(self):
        return 'MARKET'


class LimitOrder(Order):
    def get_order_type(self):
        return 'LIMIT'


class StopLossOrder(Order):
    def get_order_type(self):
        return 'SL'


class StopLossMarketOrder(Order):
    def get_order_type(self):
        return 'SL-M'



if __name__ == "__main__":
    # Example of creating and placing a Delivery Market Order
    delivery_market_order = MarketOrder(
        instrument_token='NSE_EQ|INE669E01016',
        quantity=1,
        transaction_type='BUY',
        product='D'
    )
