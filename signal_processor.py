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
    If a ProfitTrailing instance is provided, its take_profit_detected flag is updated
    when a take profit signal is detected.
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
        self.last_executed_side: Optional[str] = None

    def fetch_signal(self, key: str = "signal") -> Optional[Dict[str, Any]]:
        try:
            data = self.redis_client.get(key)
            if not data:
                return None
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

    def process_signal(self, signal_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not signal_data:
            return None

        last_signal = signal_data.get("last_signal", {})
        supply_zone = signal_data.get("supply_zone", {})
        demand_zone = signal_data.get("demand_zone", {})

        signal_text = last_signal.get("text", "").lower()
        raw_price = last_signal.get("price")
        raw_supply = supply_zone.get("min")
        raw_demand = demand_zone.get("min")

        # Use live price as fallback if raw price is missing.
        if raw_price is None or str(raw_price).strip() == "":
            live_price = self.ws.current_price
            if live_price is None:
                logger.error("No valid price in signal and live price unavailable.")
                return None
            raw_price = live_price
            logger.info("Using live price as fallback: %.2f", raw_price)

        # Process take profit signals.
        if "take profit" in signal_text or "tp" in signal_text:
            logger.info("Take profit signal detected.")
            # IMPORTANT: Update the flag on the shared ProfitTrailing instance.
            if self.profit_trailing:
                self.profit_trailing.take_profit_detected = True
            live_price = self.ws.current_price
            if live_price is None:
                logger.error("Live price unavailable for TP signal processing.")
                return None
            try:
                positions = self.order_manager.client.fetch_positions()
                for pos in positions:
                    pos_symbol = pos.get("info", {}).get("product_symbol") or pos.get("symbol")
                    if pos_symbol and "BTCUSD" in pos_symbol:
                        entry = pos.get("entryPrice") or pos.get("entry_price") or pos.get("info", {}).get("entry_price")
                        try:
                            entry = float(entry)
                        except Exception:
                            continue
                        try:
                            size = float(pos.get("size") or pos.get("contracts") or 0)
                        except Exception:
                            size = 0.0
                        if size == 0:
                            continue
                        # For long positions: force close if in loss.
                        if size > 0:
                            profit_pct = (live_price - entry) / entry
                            if profit_pct < 0:
                                close_order = self.trade_manager.place_market_order(
                                    "BTCUSD", "sell", size, params={"time_in_force": "ioc"}, force=True
                                )
                                logger.info("TP signal: Force closing long position in loss. Close order: %s", close_order)
                        # For short positions: force close if in loss.
                        elif size < 0:
                            profit_pct = (entry - live_price) / entry
                            if profit_pct < 0:
                                close_order = self.trade_manager.place_market_order(
                                    "BTCUSD", "buy", abs(size), params={"time_in_force": "ioc"}, force=True
                                )
                                logger.info("TP signal: Force closing short position in loss. Close order: %s", close_order)
            except Exception as e:
                logger.error("Error processing TP signal: %s", e)
            return None

        # Determine new order side based on signal text.
        if "short" in signal_text:
            new_side = "sell"
        elif "buy" in signal_text:
            new_side = "buy"
        else:
            new_side = None

        try:
            positions = self.order_manager.client.fetch_positions()
            for pos in positions:
                pos_symbol = pos.get("info", {}).get("product_symbol") or pos.get("symbol")
                if pos_symbol and "BTCUSD" in pos_symbol:
                    try:
                        pos_size = float(pos.get("size") or pos.get("contracts") or 0)
                    except Exception:
                        pos_size = 0.0
                    if new_side == "buy" and pos_size < 0:
                        logger.info("Opposite signal received: Forcing closure of existing short position before buying.")
                        self.trade_manager.place_market_order(
                            "BTCUSD", "buy", abs(pos_size), params={"time_in_force": "ioc"}, force=True
                        )
                        time.sleep(2)
                    elif new_side == "sell" and pos_size > 0:
                        logger.info("Opposite signal received: Forcing closure of existing long position before selling.")
                        self.trade_manager.place_market_order(
                            "BTCUSD", "sell", pos_size, params={"time_in_force": "ioc"}, force=True
                        )
                        time.sleep(2)
        except Exception as e:
            logger.error("Error handling opposite positions: %s", e)

        # Cancel conflicting and same-side orders.
        self.cancel_conflicting_orders("BTCUSD", new_side)
        self.cancel_same_side_orders("BTCUSD", new_side)
        time.sleep(2)

        if self.order_manager.has_open_position("BTCUSD", new_side):
            logger.info("An open %s position exists for BTCUSD. Skipping new order placement.", new_side)
            return None

        if raw_supply is None or raw_demand is None:
            logger.error("Incomplete signal data (supply/demand missing): %s", signal_data)
            return None

        if new_side == "buy":
            entry_price = float(raw_price) - (float(raw_price) * (config.ORDER_ENTRY_OFFSET_PERCENT / 100))
            sl_price = float(raw_price) - (float(raw_price) * (config.ORDER_SL_OFFSET_PERCENT / 100))
            tp_price = float(raw_price) + (float(raw_price) * (config.ORDER_TP_OFFSET_PERCENT / 100))
        elif new_side == "sell":
            entry_price = float(raw_price) + (float(raw_price) * (config.ORDER_ENTRY_OFFSET_PERCENT / 100))
            sl_price = float(raw_price) + (float(raw_price) * (config.ORDER_SL_OFFSET_PERCENT / 100))
            tp_price = float(raw_price) - (float(raw_price) * (config.ORDER_TP_OFFSET_PERCENT / 100))
        else:
            logger.warning("Unable to determine side for signal: %s", signal_text)
            return None

        logger.info("Signal: %s | Entry: %.2f | SL: %.2f | TP: %.2f",
                    last_signal.get("text"), entry_price, sl_price, tp_price)

        try:
            limit_order = self.order_manager.place_order("BTCUSD", new_side, 1, entry_price,
                                                          params={"time_in_force": "gtc"})
            logger.info("Limit order placed: %s", limit_order)
        except Exception as e:
            logger.error("Failed to place limit order: %s", e)
            return None

        bracket_params = {
            "bracket_stop_loss_limit_price": str(sl_price),
            "bracket_stop_loss_price": str(sl_price),
            "bracket_take_profit_limit_price": str(tp_price),
            "bracket_take_profit_price": str(tp_price),
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

    def signals_are_different(self, new_signal: Dict[str, Any], old_signal: Optional[Dict[str, Any]]) -> bool:
        new_text = new_signal.get("last_signal", {}).get("text", "").strip().lower()
        if not new_text:
            return False
        old_text = ""
        if old_signal:
            old_text = old_signal.get("last_signal", {}).get("text", "").strip().lower()
        return new_text != old_text

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

if __name__ == "__main__":
    # For testing, you may create a dummy websocket instance.
    sp = SignalProcessor(None)
    sp.process_signals_loop()
