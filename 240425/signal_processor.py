# signal_processor.py

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
        try:
            raw = self.redis_client.lrange(key, -1, -1)
            if not raw:
                return None
            data = raw[0].decode() if isinstance(raw[0], bytes) else raw[0]
            return json.loads(data)
        except Exception as e:
            logger.error("Error fetching signal from Redis (%s): %s", key, e)
            return None

    def cancel_conflicting_orders(self, symbol: str, new_side: str) -> None:
        try:
            for order in self.order_manager.client.exchange.fetch_open_orders(symbol):
                if order.get("status", "").lower() != "open":
                    continue
                side = order.get("side", "").lower()
                if side == new_side:
                    continue
                self.order_manager.client.cancel_order(order["id"], symbol)
                logger.info("Canceled conflicting order: %s", order["id"])
        except Exception as e:
            logger.error("Error cancelling conflicting orders: %s", e)

    def cancel_same_side_orders(self, symbol: str, side: str) -> None:
        try:
            for order in self.order_manager.client.exchange.fetch_open_orders(symbol):
                if order.get("side", "").lower() == side and order.get("status", "").lower() == "open":
                    self.order_manager.client.cancel_order(order["id"], symbol)
                    logger.info("Canceled same-side order: %s", order["id"])
        except Exception as e:
            logger.error("Error cancelling same-side orders: %s", e)

    def open_pending_order_exists(self, symbol: str, side: str) -> bool:
        try:
            return any(
                o.get("side", "").lower() == side and o.get("status", "").lower() == "open"
                for o in self.order_manager.client.exchange.fetch_open_orders(symbol)
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
        last = signal_data.get("last_signal", {})
        text = last.get("text", "").lower()
        valid = signal_data.get("valid_position", False)

        # determine new side
        if "buy" in text or "long" in text:
            new_side = "buy"
        elif "sell" in text or "short" in text:
            new_side = "sell"
        else:
            logger.warning("Unknown signal text '%s' — skipping.", text)
            return None

        logger.info("Processing %s signal (valid_position=%s)", new_side, valid)

        # update zone limits
        supply = signal_data.get("supply_zone", {})
        demand = signal_data.get("demand_zone", {})
        raw_supply = supply.get("min")
        raw_demand = demand.get("max")
        if self.profit_trailing:
            try:
                self.profit_trailing.set_zone_limits(
                    supply_max=float(raw_supply) if raw_supply else None,
                    demand_max=float(raw_demand) if raw_demand else None,
                    full_supply_zone=supply,
                    full_demand_zone=demand
                )
                logger.info("Zone targets set: long=%s, short=%s",
                    self.profit_trailing.target_long,
                    self.profit_trailing.target_short
                )
            except Exception as e:
                logger.warning("Failed to set zone limits: %s", e)

        # skip TP signals
        if "take profit" in text or "tp" in text:
            logger.info("Take profit signal — skipping order placement.")
            if self.profit_trailing:
                self.profit_trailing.take_profit_detected = True
            return None
        else:
            logger.info("Non-TP signal — resetting trailing SL.")
            if self.profit_trailing:
                self.profit_trailing.take_profit_detected = False

        # determine price
        price = last.get("price")
        if not price:
            price = self.ws.current_price
            if price is None:
                logger.error("No price available — cannot proceed.")
                return None
            logger.info("Using live price: %.2f", price)
        price = float(price)

        # always close opposite
        try:
            for pos in self.order_manager.client.fetch_positions():
                # filter to our symbol
                sym = pos.get("info", {}).get("product_symbol") or pos.get("symbol", "")
                if not sym.startswith(config.SYMBOL):
                    continue
                size = float(pos.get("size") or pos.get("contracts") or 0)
                if new_side == "buy" and size < 0:
                    logger.info("Closing short of size %.2f before buy.", abs(size))
                    self.trade_manager.place_market_order(
                        config.SYMBOL, "buy", abs(size),
                        params={"time_in_force": "ioc"}, force=True
                    )
                    time.sleep(2)
                elif new_side == "sell" and size > 0:
                    logger.info("Closing long of size %.2f before sell.", size)
                    self.trade_manager.place_market_order(
                        config.SYMBOL, "sell", size,
                        params={"time_in_force": "ioc"}, force=True
                    )
                    time.sleep(2)
        except Exception as e:
            logger.error("Error closing opposite positions: %s", e)

        # skip new order if invalid
        if not valid:
            logger.info("valid_position=false — skipping new order placement.")
            return None

        # cancel any open limit/bracket orders
        self.cancel_conflicting_orders(config.SYMBOL, new_side)
        self.cancel_same_side_orders(config.SYMBOL, new_side)
        time.sleep(2)

        # skip if already in position
        if self.order_manager.has_open_position(config.SYMBOL, new_side):
            logger.info("Already in %s position — skipping new order.", new_side)
            return None

        # compute entry & SL
        if new_side == "buy":
            entry = price * (1 - config.ORDER_ENTRY_OFFSET_PERCENT/100)
            sl = float(raw_demand) if raw_demand else price * (1 - config.ORDER_SL_OFFSET_PERCENT/100)
        else:
            entry = price * (1 + config.ORDER_ENTRY_OFFSET_PERCENT/100)
            sl = float(raw_supply) if raw_supply else price * (1 + config.ORDER_SL_OFFSET_PERCENT/100)

        logger.info("Signal: %s | Entry: %.2f | SL: %.2f", last.get("text"), entry, sl)

        # place limit + bracket
        try:
            order = self.order_manager.place_order(
                config.SYMBOL, new_side, config.QUANTITY, entry,
                params={"time_in_force": "gtc"}
            )
            logger.info("Limit order placed: %s", order)
        except Exception as e:
            logger.error("Failed to place limit order: %s", e)
            return None

        bracket = {
            "bracket_stop_loss_limit_price": str(sl),
            "bracket_stop_loss_price":       str(sl),
            "bracket_stop_trigger_method":   "last_traded_price"
        }
        try:
            updated = self.order_manager.attach_bracket_to_order(
                order_id=order["id"],
                product_id=27,
                product_symbol=config.SYMBOL,
                bracket_params=bracket
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
