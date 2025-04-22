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
        self.last_executed_side: Optional[str] = None

    def fetch_signal(self, key: str = "BTCUSDT_signal") -> Optional[Dict[str, Any]]:
        try:
            data = self.redis_client.lindex(key, -1)
            if not data:
                return None
            if isinstance(data, bytes):
                data = data.decode("utf-8")
            return json.loads(data)
        except Exception as e:
            logger.error("Error fetching signal from Redis: %s", e)
            return None

    def cancel_conflicting_orders(self, symbol: str, new_side: str) -> None:
        try:
            orders = self.order_manager.client.exchange.fetch_open_orders(symbol)
            if orders:
                for order in orders:
                    if order.get("status", "").lower() != "open":
                        continue
                    order_side = order.get("side", "").lower()
                    if new_side == "" or order_side != new_side.lower():
                        try:
                            self.order_manager.client.cancel_order(order["id"], symbol)
                            logger.info("Canceled conflicting order: %s", order["id"])
                        except Exception as e:
                            logger.error("Error canceling order %s: %s", order["id"], e)
        except Exception as e:
            logger.error("Error fetching open orders: %s", e)

    def cancel_same_side_orders(self, symbol: str, side: str) -> None:
        try:
            pending_orders = self.order_manager.client.exchange.fetch_open_orders(symbol)
            for order in pending_orders:
                if order.get("side", "").lower() == side.lower() and order.get("status", "").lower() == "open":
                    try:
                        self.order_manager.client.cancel_order(order["id"], symbol)
                        logger.info("Canceled same-side order: %s", order["id"])
                    except Exception as e:
                        logger.error("Error canceling same-side order %s: %s", order["id"], e)
        except Exception as e:
            logger.error("Error fetching same-side pending orders: %s", e)

    def open_pending_order_exists(self, symbol: str, side: str) -> bool:
        try:
            orders = self.order_manager.client.exchange.fetch_open_orders(symbol)
            for order in orders:
                if order.get("side", "").lower() == side.lower() and order.get("status", "").lower() == "open":
                    return True
            return False
        except Exception as e:
            logger.error("Error checking for pending orders: %s", e)
            return False

    def signals_are_different(self, new_signal: Dict[str, Any], old_signal: Optional[Dict[str, Any]]) -> bool:
        new_text = new_signal.get("last_signal", {}).get("text", "").strip().lower()
        new_supply_max = new_signal.get("supply_zone", {}).get("max", "").strip()
        new_demand_min = new_signal.get("demand_zone", {}).get("min", "").strip()

        old_text = old_supply_max = old_demand_min = ""
        if old_signal:
            old_text = old_signal.get("last_signal", {}).get("text", "").strip().lower()
            old_supply_max = old_signal.get("supply_zone", {}).get("max", "").strip()
            old_demand_min = old_signal.get("demand_zone", {}).get("min", "").strip()

        return (
            new_text != old_text or
            new_supply_max != old_supply_max or
            new_demand_min != old_demand_min
        )

    def process_signal(self, signal_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not signal_data:
            return None

        last_signal = signal_data.get("last_signal", {})
        signal_text = last_signal.get("text", "").lower()

        if "take profit" in signal_text or "tp" in signal_text:
            logger.info("Take profit signal — no new order should be placed.")
            if self.profit_trailing:
                self.profit_trailing.take_profit_detected = True
            return None
        else:
            logger.info("Non-TP signal detected. Resetting trailing SL mode.")
            if self.profit_trailing:
                self.profit_trailing.take_profit_detected = False

        raw_price = last_signal.get("price")
        supply_zone = signal_data.get("supply_zone", {})
        demand_zone = signal_data.get("demand_zone", {})
        raw_supply_max = supply_zone.get("max")
        raw_demand_min = demand_zone.get("min")
        valid_position = signal_data.get("valid_position")

        if self.profit_trailing:
            try:
                self.profit_trailing.set_zone_limits(
                    supply_max=float(raw_supply_max) if raw_supply_max else None,
                    demand_min=float(raw_demand_min) if raw_demand_min else None,
                    full_supply_zone=supply_zone,
                    full_demand_zone=demand_zone
                )

            except Exception as e:
                logger.warning("Failed to set zone limits: %s", e)

        if raw_price is None or str(raw_price).strip() == "":
            live_price = self.ws.current_price
            if live_price is None:
                logger.error("No valid price in signal and live price unavailable.")
                return None
            raw_price = live_price
            logger.info("Using live price as fallback: %.2f", raw_price)
        else:
            raw_price = float(raw_price)

        if valid_position is not True and ("short" in signal_text or "buy" in signal_text or "long" in signal_text):
            logger.info("Signal has valid_position set to false or null — skipping entry.")
            return None

        try:
            positions = self.order_manager.client.fetch_positions()
            for pos in positions:
                pos_symbol = pos.get("info", {}).get("product_symbol") or pos.get("symbol")
                if pos_symbol and "BTCUSD" in pos_symbol:
                    try:
                        pos_size = float(pos.get("size") or pos.get("contracts") or 0)
                    except Exception:
                        pos_size = 0.0
                    if "buy" in signal_text and pos_size < 0:
                        logger.info("Opposite signal received: Closing short before buying.")
                        self.trade_manager.place_market_order("BTCUSD", "buy", abs(pos_size), params={"time_in_force": "ioc"}, force=True)
                        time.sleep(2)
                    elif ("sell" in signal_text or "short" in signal_text) and pos_size > 0:
                        logger.info("Opposite signal received: Closing long before selling.")
                        self.trade_manager.place_market_order("BTCUSD", "sell", pos_size, params={"time_in_force": "ioc"}, force=True)
                        time.sleep(2)
        except Exception as e:
            logger.error("Error handling opposite positions: %s", e)

        side = "buy" if "buy" in signal_text or "long" in signal_text else "sell"
        self.cancel_conflicting_orders("BTCUSD", side)
        self.cancel_same_side_orders("BTCUSD", side)
        time.sleep(2)

        if self.order_manager.has_open_position("BTCUSD", side):
            logger.info("Open %s position exists. Skipping new order.", side)
            return None

        if side == "buy":
            entry_price = raw_price - (raw_price * (config.ORDER_ENTRY_OFFSET_PERCENT / 100))
            sl_price = float(raw_demand_min) if raw_demand_min else raw_price - (raw_price * (config.ORDER_SL_OFFSET_PERCENT / 100))
        else:
            entry_price = raw_price + (raw_price * (config.ORDER_ENTRY_OFFSET_PERCENT / 100))
            sl_price = float(raw_supply_max) if raw_supply_max else raw_price + (raw_price * (config.ORDER_SL_OFFSET_PERCENT / 100))

        logger.info("Signal: %s | Entry: %.2f | SL: %.2f", last_signal.get("text"), entry_price, sl_price)

        try:
            limit_order = self.order_manager.place_order("BTCUSD", side, 1, entry_price, params={"time_in_force": "gtc"})
            logger.info("Limit order placed: %s", limit_order)
        except Exception as e:
            logger.error("Failed to place limit order: %s", e)
            return None

        bracket_params = {
            "bracket_stop_loss_limit_price": str(sl_price),
            "bracket_stop_loss_price": str(sl_price),
            "bracket_stop_trigger_method": "last_traded_price"
        }

        try:
            updated_order = self.order_manager.attach_bracket_to_order(
                order_id=limit_order["id"],
                product_id=27,
                product_symbol="BTCUSD",
                bracket_params=bracket_params
            )
            logger.info("Bracket attached to order: %s", updated_order)
            return updated_order
        except Exception as e:
            logger.error("Failed to attach bracket: %s", e)
            return None

    def process_signals_loop(self, sleep_interval: int = 5) -> None:
        logger.info("Starting signal processing loop...")
        while True:
            signal_data = self.fetch_signal()
            if signal_data and self.signals_are_different(signal_data, self.last_signal):
                logger.info("New signal detected.")
                processed = self.process_signal(signal_data)
                if processed:
                    logger.info("Order processed successfully: %s", processed)
                else:
                    logger.info("Signal processing skipped or failed.")
                self.last_signal = signal_data
            else:
                logger.debug("No new signal or signal identical to last one.")
            time.sleep(sleep_interval)
