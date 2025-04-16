import time
import logging
import uuid
import redis
from typing import Any, Dict, Optional
from exchange import DeltaExchangeClient
from order_manager import OrderManager
import config

logger = logging.getLogger(__name__)
TOLERANCE = 1e-6  # Tolerance for treating near-zero sizes as zero

class TradeManager:
    """
    Manages trade execution by placing market orders and monitoring trailing stops.
    """
    def __init__(self) -> None:
        self.client: DeltaExchangeClient = DeltaExchangeClient()
        self.order_manager: OrderManager = OrderManager()
        self.highest_price: Optional[float] = None

    def get_current_price(self, product_symbol: str) -> float:
        """
        Retrieve the current price for the given product using the exchange ticker.
        
        Args:
            product_symbol (str): The trading symbol (e.g., "ETHUSD").
        
        Returns:
            float: The latest price.
        
        Raises:
            Exception: If fetching the ticker fails.
        """
        try:
            ticker = self.client.exchange.fetch_ticker(product_symbol)
            price = float(ticker.get("last"))
            return price
        except Exception as e:
            logger.error("Error fetching current price for %s: %s", product_symbol, e)
            raise

    def monitor_trailing_stop(self, bracket_order_id: Any, product_symbol: str, trailing_stop_percent: float, update_interval: int = 10) -> None:
        """
        Continuously monitors the market price and updates the trailing stop based on the highest price reached.
        
        Args:
            bracket_order_id (Any): Identifier for the bracket order to modify.
            product_symbol (str): The product symbol to monitor (e.g., "ETHUSD").
            trailing_stop_percent (float): The percentage used to calculate the new stop loss.
            update_interval (int, optional): Interval (in seconds) between updates.
        """
        logger.info("Starting trailing stop monitoring for %s", product_symbol)
        try:
            self.highest_price = self.get_current_price(product_symbol)
            logger.info("Initial highest price: %s", self.highest_price)
        except Exception as e:
            logger.error("Could not fetch initial price: %s", e)
            return

        while True:
            try:
                current_price = self.get_current_price(product_symbol)
            except Exception as e:
                logger.error("Error fetching price: %s", e)
                time.sleep(update_interval)
                continue

            if current_price > (self.highest_price or 0):
                self.highest_price = current_price
                logger.info("New highest price reached: %s", self.highest_price)

            new_stop_loss = self.highest_price * (1 - trailing_stop_percent / 100.0)
            logger.info("Current price: %.2f, New stop loss calculated: %.2f", current_price, new_stop_loss)
            stop_loss_order = {
                "order_type": "limit_order",
                "stop_price": f"{round(new_stop_loss, 2)}",
                "limit_price": f"{round(new_stop_loss * 0.99, 2)}"
            }
            try:
                modified_order = self.order_manager.modify_bracket_order(bracket_order_id, stop_loss_order)
                logger.info("Bracket order modified: %s", modified_order)
            except Exception as e:
                logger.error("Error modifying bracket order: %s", e)
            time.sleep(update_interval)

    def place_market_order(self, symbol: str, side: str, amount: float, params: Optional[Dict[str, Any]] = None, force: bool = False) -> Optional[Dict[str, Any]]:
        side_lower = side.lower()

        # If not forced, run the safety checks
        if not force:
            # 1. Check for existing open positions.
            try:
                positions = self.client.fetch_positions()
                for pos in positions:
                    pos_symbol = (pos.get("info", {}).get("product_symbol") or pos.get("symbol") or "")
                    if symbol not in pos_symbol:
                        continue
                    try:
                        size = float(pos.get("size") or pos.get("contracts") or 0)
                    except Exception:
                        size = 0.0
                    if side_lower == "buy" and size > 0:
                        logger.info("An open buy position exists for %s. Skipping market order.", symbol)
                        return None
                    if side_lower == "sell" and size < 0:
                        logger.info("An open sell position exists for %s. Skipping market order.", symbol)
                        return None
            except Exception as e:
                logger.error("Error fetching positions: %s", e)

            # 2. Check for existing pending orders via the exchange API.
            try:
                open_orders = self.client.exchange.fetch_open_orders(symbol)
                for order in open_orders:
                    if order.get("side", "").lower() == side_lower:
                        logger.info("A pending %s order exists for %s. Skipping market order.", side, symbol)
                        return None
            except Exception as e:
                logger.error("Error fetching open orders: %s", e)

            # 3. Clean up stale orders from the local cache.
            current_time = int(time.time() * 1000)
            stale_order_ids = [
                oid for oid, order in self.order_manager.orders.items()
                if current_time - order.get("timestamp", 0) > 60000
            ]
            for oid in stale_order_ids:
                del self.order_manager.orders[oid]

            # 4. Check the local cache for pending orders.
            for order in self.order_manager.orders.values():
                if order.get("side", "").lower() == side_lower and order.get("status") in ["open", "pending"]:
                    logger.info("Local pending %s order exists for %s. Skipping new order.", side, symbol)
                    return None

        # If force is True (used in trailing stop closure), add reduce_only flag.
        if force:
            if params is None:
                params = {}
            # Ensure reduce_only is True to prevent the order from flipping your position.
            params.setdefault("reduce_only", True)

        try:
            order = self.client.exchange.create_order(symbol, "market", side, amount, None, params or {})
            order_id = order.get("id", str(uuid.uuid4()))
            order_info = {
                "id": order_id,
                "symbol": symbol,
                "side": side,
                "amount": amount,
                "params": params or {},
                "status": order.get("status", "open"),
                "timestamp": order.get("timestamp", int(time.time() * 1000))
            }
            self.order_manager.orders[order_id] = order_info
            self.order_manager._store_order(order_info)
            time.sleep(1)  # Brief delay to allow order processing.

            # Optionally, verify the order by fetching positions again.
            positions_after = self.client.fetch_positions()
            for pos in positions_after:
                pos_symbol = (pos.get("info", {}).get("product_symbol") or pos.get("symbol") or "")
                if symbol not in pos_symbol:
                    continue
                try:
                    size = float(pos.get("size") or pos.get("contracts") or 0)
                except Exception:
                    size = 0.0
                if (side_lower == "buy" and size > 0) or (side_lower == "sell" and size < 0):
                    logger.info("Market order verified for %s.", symbol)
                    break

            logger.info("Market order placed: %s", order_info)
            return order_info
        except Exception as e:
            logger.error("Error placing market order for %s: %s", symbol, e)
            raise


if __name__ == "__main__":
    tm = TradeManager()
    logger.info("Testing market order placement...")
    try:
        market_order = tm.place_market_order("ETHUSD", "buy", 1, params={"time_in_force": "ioc"})
        logger.info("Market order placed: %s", market_order)
    except Exception as e:
        logger.error("Failed to place market order: %s", e)
