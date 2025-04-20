import logging
import time
import json
import redis
from typing import Any, Dict, Optional
from exchange import DeltaExchangeClient
import config

logger = logging.getLogger(__name__)

class OrderManager:
    def __init__(self) -> None:
        """
        Initialize the OrderManager with:
          - an exchange client instance,
          - a local order cache dictionary,
          - and a Redis client for persistent storage.
        """
        self.client: DeltaExchangeClient = DeltaExchangeClient()
        self.orders: Dict[Any, Dict[str, Any]] = {}  # Local cache for orders.
        self.redis_client = redis.Redis(
            host=config.REDIS_HOST,
            port=config.REDIS_PORT,
            db=config.REDIS_DB
        )
        # List to store normalized order info
        self.list_key = f"{config.SYMBOL}_orders"
        # List to record raw API responses on place/cancel
        self.order_info_key = config.ORDER_INFO_KEY

    def _store_order(self, order_info: Dict[str, Any]) -> None:
        """
        Store or update the order info in Redis using its order ID,
        trimming to the last 1000 entries.
        """
        try:
            self.redis_client.rpush(self.list_key, json.dumps(order_info))
            self.redis_client.ltrim(self.list_key, -1000, -1)
        except Exception as e:
            logger.error("Error storing order in Redis: %s", e)

    def is_order_open(self, symbol: str, side: str) -> bool:
        """
        Check if an order is currently open for the given symbol and side.
        First tries via the API; if that fails, falls back to local cache.
        """
        try:
            for order in self.client.exchange.fetch_open_orders(symbol):
                if (order.get('side', '').lower() == side.lower() and 
                    order.get('status', '').lower() == 'open'):
                    return True
        except Exception as e:
            logger.error("Error checking open orders via API: %s", e)

        for order in self.orders.values():
            if (order.get('symbol') == symbol and
                order.get('side', '').lower() == side.lower() and
                order.get('status', '').lower() == 'open'):
                return True
        return False

    def has_open_position(self, symbol: str, side: str) -> bool:
        """
        Determines if there is an actual open position for the given symbol and side.
        For 'buy' positions, size > 0 and for 'sell' positions, size < 0.
        """
        try:
            for pos in self.client.fetch_positions():
                pos_symbol = pos.get('info', {}).get('product_symbol') or pos.get('symbol', '')
                if symbol not in pos_symbol:
                    continue
                try:
                    size = float(pos.get('size') or pos.get('contracts') or 0)
                except Exception:
                    size = 0.0

                if side.lower() == "buy" and size > 0:
                    return True
                if side.lower() == "sell" and size < 0:
                    return True
        except Exception as e:
            logger.error("Error checking open positions via API: %s", e)
        return False

    def place_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        price: float,
        params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Place a new limit order, record the raw API response,
        then update local cache plus Redis.
        """
        try:
            # 1) Call the exchange
            api_response = self.client.create_limit_order(symbol, side, amount, price, params)

            # 2) Record raw API response
            try:
                self.redis_client.rpush(self.order_info_key, json.dumps(api_response))
                self.redis_client.ltrim(self.order_info_key, -1000, -1)
            except Exception as e:
                logger.error("Error recording order API response to Redis: %s", e)

            # 3) Build our normalized order info
            order_id = api_response.get('id') or int(time.time() * 1000)
            order_info = {
                'id': order_id,
                'symbol': symbol,
                'side': side,
                'amount': amount,
                'price': price,
                'params': params or {},
                'status': api_response.get('status', 'open'),
                'timestamp': api_response.get('timestamp', int(time.time() * 1000))
            }
            self.orders[order_id] = order_info

            # 4) Store normalized info
            self._store_order(order_info)
            logger.debug("Placed order: %s", order_info)
            return order_info

        except Exception as e:
            logger.error("Error placing order for %s: %s", symbol, e)
            raise

    def attach_bracket_to_order(
        self,
        order_id: Any,
        product_id: Any,
        product_symbol: str,
        bracket_params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Attach or update bracket parameters (stop loss and take profit settings)
        to an existing order. Preserves original logic.
        """
        try:
            exchange_order = self.client.modify_bracket_order(
                order_id, product_id, product_symbol, bracket_params
            )
            if order_id in self.orders:
                self.orders[order_id]['params'].update(bracket_params)
                self.orders[order_id]['status'] = exchange_order.get('state', self.orders[order_id]['status'])
                updated_order = self.orders[order_id]
            else:
                updated_order = {
                    'id': order_id,
                    'product_id': product_id,
                    'product_symbol': product_symbol,
                    'params': bracket_params,
                    'status': exchange_order.get('state', 'open'),
                    'timestamp': exchange_order.get('created_at', int(time.time() * 1000000))
                }
                self.orders[order_id] = updated_order

            self._store_order(updated_order)
            logger.debug("Bracket attached to order %s: %s", order_id, updated_order)
            return updated_order

        except Exception as e:
            logger.error("Error attaching bracket to order %s: %s", order_id, e)
            raise

    def modify_bracket_order(
        self,
        order_id: Any,
        new_bracket_params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Modify the bracket parameters of an existing order locally.
        """
        if order_id not in self.orders:
            raise ValueError("Bracket order ID not found.")
        self.orders[order_id]['params'].update(new_bracket_params)
        self._store_order(self.orders[order_id])
        logger.debug("Modified bracket order %s locally: %s", order_id, self.orders[order_id])
        return self.orders[order_id]

    def cancel_order(self, order_id: Any) -> Dict[str, Any]:
        """
        Cancel an order given its ID. Records raw cancel response,
        updates local cache and normalized Redis list.
        """
        if order_id not in self.orders:
            raise ValueError("Order ID not found.")
        order = self.orders[order_id]
        symbol = order.get('symbol') or order.get('product_symbol')
        try:
            # 1) Cancel via exchange
            api_response = self.client.cancel_order(order_id, symbol)

            # 2) Record raw cancel response
            try:
                self.redis_client.rpush(self.order_info_key, json.dumps(api_response))
                self.redis_client.ltrim(self.order_info_key, -1000, -1)
            except Exception as e:
                logger.error("Error recording cancel API response to Redis: %s", e)

            # 3) Update normalized info
            order['status'] = 'canceled'
            self._store_order(order)
            logger.debug("Canceled order %s: %s", order_id, api_response)
            return api_response

        except Exception as e:
            logger.error("Error canceling order %s: %s", order_id, e)
            raise

if __name__ == '__main__':
    om = OrderManager()
    try:
        limit_order = om.place_order(config.SYMBOL, "buy", config.QUANTITY, 45000)
        print("Limit order placed:", limit_order)
    except Exception as e:
        print("Failed to place limit order:", e)
        exit(1)

    bracket_params = {
        "bracket_stop_loss_limit_price": "40000",
        "bracket_stop_loss_price": "40000",
        "bracket_take_profit_limit_price": "50000",
        "bracket_take_profit_price": "50000",
        "bracket_stop_trigger_method": "last_traded_price"
    }
    try:
        updated = om.attach_bracket_to_order(
            order_id=limit_order['id'],
            product_id=27,
            product_symbol=config.SYMBOL,
            bracket_params=bracket_params
        )
        print("Bracket attached:", updated)
    except Exception as e:
        print("Failed to attach bracket:", e)
        exit(1)
