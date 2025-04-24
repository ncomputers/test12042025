import time
import json
import redis
import logging
from typing import Optional, Any, Dict
from order_manager import OrderManager
from trade_manager import TradeManager
import config

logger = logging.getLogger(__name__)

class SignalProcessor:
    """
    Processes trading signals from Redis and executes order actions.
    Uses a shared BinanceWebsocket instance for accessing live price.
    """
    def __init__(self, ws_instance, profit_trailing: Optional[Any] = None) -> None:
        self.ws = ws_instance
        self.profit_trailing = profit_trailing
        self.order_manager = OrderManager()
        self.trade_manager = TradeManager()
        self.redis_client = redis.Redis(
            host=config.REDIS_HOST,
            port=config.REDIS_PORT,
            db=config.REDIS_DB
        )
        self.last_signal: Optional[Dict[str, Any]] = None

    def fetch_signal(self, key: str = "BTCUSDT_signal") -> Optional[Dict[str, Any]]:
        """
        Fetch the latest signal JSON from Redis list using LRANGE -1,-1.
        """
        try:
            raw_list = self.redis_client.lrange(key, -1, -1)
            if not raw_list:
                return None
            raw = raw_list[0]
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            return json.loads(raw)
        except Exception as e:
            logger.error("Error fetching signal from Redis (%s): %s", key, e)
            return None

    def cancel_conflicting_orders(self, symbol: str, new_side: str) -> None:
        try:
            for order in self.order_manager.client.exchange.fetch_open_orders(symbol):
                if order.get("status", "").lower() != "open":
                    continue
                side = order.get("side", "").lower()
                if new_side and side == new_side.lower():
                    continue
                self.order_manager.client.cancel_order(order["id"], symbol)
                logger.info("Canceled conflicting order: %s", order["id"])
        except Exception as e:
            logger.error("Error cancelling conflicting orders: %s", e)

    def cancel_same_side_orders(self, symbol: str, side: str) -> None:
        try:
            for order in self.order_manager.client.exchange.fetch_open_orders(symbol):
                if order.get("side", "").lower() == side.lower() and order.get("status", "").lower() == "open":
                    self.order_manager.client.cancel_order(order["id"], symbol)
                    logger.info("Canceled same-side order: %s", order["id"])
        except Exception as e:
            logger.error("Error cancelling same-side orders: %s", e)

    def open_pending_order_exists(self, symbol: str, side: str) -> bool:
        try:
            return any(
                order.get("side", "").lower() == side.lower() and order.get("status", "").lower() == "open"
                for order in self.order_manager.client.exchange.fetch_open_orders(symbol)
            )
        except Exception as e:
            logger.error("Error checking pending orders: %s", e)
            return False

    def signals_are_different(self, new_signal: Dict[str, Any], old_signal: Optional[Dict[str, Any]]) -> bool:
        new_text   = new_signal.get("last_signal", {}).get("text", "").strip().lower()
        new_supply = new_signal.get("supply_zone", {}).get("min", "")
        new_demand = new_signal.get("demand_zone", {}).get("max", "")

        old_text = old_supply = old_demand = ""
        if old_signal:
            old_text   = old_signal.get("last_signal", {}).get("text", "").strip().lower()
            old_supply = old_signal.get("supply_zone", {}).get("min", "")
            old_demand = old_signal.get("demand_zone", {}).get("max", "")

        return (new_text != old_text or new_supply != old_supply or new_demand != old_demand)

    def process_signal(self, signal_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        last_signal = signal_data.get("last_signal", {})
        text        = last_signal.get("text", "").lower()

        # 1) Extract zones immediately and set targets
        supply_zone    = signal_data.get("supply_zone", {})
        demand_zone    = signal_data.get("demand_zone", {})
        raw_supply_min = supply_zone.get("min")
        raw_demand_max = demand_zone.get("max")
        valid_pos      = signal_data.get("valid_position")

        if self.profit_trailing:
            try:
                self.profit_trailing.set_zone_limits(
                    supply_max=float(raw_supply_min) if raw_supply_min else None,
                    demand_max=float(raw_demand_max) if raw_demand_max else None,
                    full_supply_zone=supply_zone,
                    full_demand_zone=demand_zone
                )
                logger.info(
                    "Zone targets set: long=%s, short=%s",
                    self.profit_trailing.target_long,
                    self.profit_trailing.target_short
                )
            except Exception as e:
                logger.warning("Failed to set zone limits: %s", e)

        # 2) Skip take-profit signals after setting targets
        if "take profit" in text or "tp" in text:
            logger.info("Take profit signal — skipping order placement.")
            if self.profit_trailing:
                self.profit_trailing.take_profit_detected = True
            return None
        else:
            logger.info("Non-TP signal — resetting trailing SL.")
            if self.profit_trailing:
                self.profit_trailing.take_profit_detected = False

        # 3) Determine entry price from signal or live price
        raw_price = last_signal.get("price")
        if not raw_price:
            live = self.ws.current_price
            if live is None:
                logger.error("No price available.")
                return None
            raw_price = live
            logger.info("Using live price: %.2f", raw_price)
        raw_price = float(raw_price)

        # 4) Skip if invalid position
        if valid_pos is not True and any(k in text for k in ("buy", "short", "long")):
            logger.info("valid_position=false — skipping.")
            return None

        # 5) Close opposite positions
        try:
            for pos in self.order_manager.client.fetch_positions():
                size = float(pos.get("size") or pos.get("contracts") or 0)
                if "buy" in text and size < 0:
                    logger.info("Closing short before buy.")
                    self.trade_manager.place_market_order(
                        config.SYMBOL, "buy", abs(size),
                        params={"time_in_force": "ioc"}, force=True
                    )
                    time.sleep(2)
                elif any(k in text for k in ("sell", "short")) and size > 0:
                    logger.info("Closing long before sell.")
                    self.trade_manager.place_market_order(
                        config.SYMBOL, "sell", size,
                        params={"time_in_force": "ioc"}, force=True
                    )
                    time.sleep(2)
        except Exception as e:
            logger.error("Error closing opposite positions: %s", e)

        # 6) Determine side
        if any(k in text for k in ("buy", "long")):
            side = "buy"
        elif any(k in text for k in ("sell", "short")):
            side = "sell"
        else:
            logger.warning("Unknown side '%s' — skipping.", text)
            return None

        # 7) Cancel existing orders
        self.cancel_conflicting_orders(config.SYMBOL, side)
        self.cancel_same_side_orders(config.SYMBOL, side)
        time.sleep(2)

        # 8) Skip if already in position
        if self.order_manager.has_open_position(config.SYMBOL, side):
            logger.info("Already in %s position — skipping.", side)
            return None

        # 9) Compute entry & SL
        if side == "buy":
            entry_price = raw_price * (1 - config.ORDER_ENTRY_OFFSET_PERCENT / 100)
            sl_price    = float(raw_demand_max) if raw_demand_max else raw_price * (1 - config.ORDER_SL_OFFSET_PERCENT / 100)
        else:
            entry_price = raw_price * (1 + config.ORDER_ENTRY_OFFSET_PERCENT / 100)
            sl_price    = float(raw_supply_min) if raw_supply_min else raw_price * (1 + config.ORDER_SL_OFFSET_PERCENT / 100)

        logger.info("Signal: %s | Entry: %.2f | SL: %.2f", last_signal.get("text"), entry_price, sl_price)

        # 10) Place order + bracket
        try:
            limit_order = self.order_manager.place_order(
                config.SYMBOL, side, config.QUANTITY, entry_price,
                params={"time_in_force": "gtc"}
            )
            logger.info("Limit order placed: %s", limit_order)
        except Exception as e:
            logger.error("Failed to place limit order: %s", e)
            return None

        bracket_params = {
            "bracket_stop_loss_limit_price": str(sl_price),
            "bracket_stop_loss_price":       str(sl_price),
            "bracket_stop_trigger_method":   "last_traded_price"
        }
        try:
            updated = self.order_manager.attach_bracket_to_order(
                order_id=limit_order["id"],
                product_id=27,
                product_symbol=config.SYMBOL,
                bracket_params=bracket_params
            )
            logger.info("Bracket attached: %s", updated)
            return updated
        except Exception as e:
            logger.error("Failed to attach bracket: %s", e)
            return None

    def process_signals_loop(self, sleep_interval: int = 5) -> None:
        logger.info("Starting signal processing loop...")
        while True:
            sig = self.fetch_signal()
            if sig and self.signals_are_different(sig, self.last_signal):
                logger.info("New signal: %s", sig["last_signal"]["text"])
                _ = self.process_signal(sig)
                self.last_signal = sig
            time.sleep(sleep_interval)
