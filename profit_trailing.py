import time
import logging
import threading
from typing import Dict, Any, List, Optional, Tuple
from exchange import DeltaExchangeClient
import config
from trade_manager import TradeManager

logger = logging.getLogger(__name__)

class ProfitTrailing:
    """
    Monitors open positions and updates trailing stops using live price updates
    from a shared BinanceWebsocket instance.
    """
    def __init__(self, ws_instance, check_interval: int = 1) -> None:
        self.ws = ws_instance  # Shared BinanceWebsocket instance.
        self.client = DeltaExchangeClient()
        self.trade_manager = TradeManager()
        self.check_interval: int = check_interval
        self.position_trailing_stop: Dict[Any, float] = {}   # order_id -> trailing stop price
        self.last_had_positions: bool = True
        self.last_position_fetch_time: float = 0.0
        self.position_fetch_interval: int = 5  # seconds between position fetches
        self.cached_positions: List[Dict[str, Any]] = []
        self.last_display: Dict[Any, Dict[str, Any]] = {}
        self.position_max_profit: Dict[Any, float] = {}
        self.take_profit_detected: bool = False  # Flag to indicate if a TP signal has been detected

    def fetch_open_positions(self) -> List[Dict[str, Any]]:
        try:
            positions = self.client.fetch_positions()
            open_positions = []
            for pos in positions:
                try:
                    size = float(pos.get('size') or pos.get('contracts') or 0)
                except Exception:
                    size = 0.0
                if size != 0:
                    pos_symbol = pos.get('info', {}).get('product_symbol') or pos.get('symbol', '')
                    if pos_symbol and "BTCUSD" in pos_symbol:
                        open_positions.append(pos)
            return open_positions
        except Exception as e:
            logger.error("Error fetching open positions: %s", e)
            return []

    def compute_profit_pct(self, pos: Dict[str, Any], live_price: float) -> Optional[float]:
        entry = pos.get('entryPrice') or pos.get('entry_price') or pos.get('info', {}).get('entry_price')
        try:
            entry = float(entry)
        except Exception:
            return None
        try:
            size = float(pos.get('size') or pos.get('contracts') or 0)
        except Exception:
            size = 0.0
        if size > 0:
            return (live_price - entry) / entry
        else:
            return (entry - live_price) / entry

    def update_trailing_stop(self, pos: Dict[str, Any], live_price: float) -> Tuple[Optional[float], Optional[float], Optional[str]]:
        """
        Updates the trailing stop for a given position.

        This function does the following:
        - Attempts to retrieve a valid order ID from 'id' or 'orderId'. If none is found,
        it generates a unique identifier based on the position data.
        - Calculates the current profit (in absolute points) and updates the maximum profit achieved.
        - If the take profit signal has been detected and the current profit is positive,
        it applies the "lock_50" rule (stop loss is set to entry plus half the maximum profit for longs,
        or entry minus half for shorts). Otherwise, if maximum profit exceeds 1000 points,
        it moves the stop loss to the entry (break-even). If neither condition is met,
        it uses a fixed offset from the entry price (defined in config).

        Returns a tuple: (new_trailing_stop, profit_ratio, rule)
        """
        # Obtain order ID; if not found, generate one.
        order_id = pos.get('id') or pos.get('orderId')
        if not order_id:
            order_id = str(hash(frozenset(pos.items())))
        
        entry = pos.get('entryPrice') or pos.get('entry_price') or pos.get('info', {}).get('entry_price')
        try:
            entry = float(entry)
        except Exception:
            return None, None, None
        try:
            size = float(pos.get('size') or pos.get('contracts') or 0)
        except Exception:
            size = 0.0

        # Calculate current profit in absolute points.
        current_profit = live_price - entry if size > 0 else entry - live_price

        # Update maximum profit for this order.
        prev_max = self.position_max_profit.get(order_id, 0)
        new_max_profit = max(prev_max, current_profit)
        self.position_max_profit[order_id] = new_max_profit

        # If take profit is detected and current profit is positive, use lock_50 rule.
        if self.take_profit_detected and current_profit > 0:
            new_trailing = entry + new_max_profit / 2 if size > 0 else entry - new_max_profit / 2
            rule = "lock_50"
            self.position_trailing_stop[order_id] = new_trailing
            return new_trailing, new_max_profit / entry, rule
        # Otherwise, if max profit > 1000 points and current profit is positive, use break-even rule.
        elif new_max_profit > 1000 and current_profit > 0:
            new_trailing = entry
            rule = "break_even"
            self.position_trailing_stop[order_id] = new_trailing
            return new_trailing, new_max_profit / entry, rule
        else:
            # Default: use the fixed offset from config.
            default_offset = config.FIXED_STOP_OFFSET  # e.g., 500 points
            default_sl = entry - default_offset if size > 0 else entry + default_offset
            stored_trailing = self.position_trailing_stop.get(order_id)
            if stored_trailing is not None:
                new_trailing = max(stored_trailing, default_sl) if size > 0 else min(stored_trailing, default_sl)
            else:
                new_trailing = default_sl
            self.position_trailing_stop[order_id] = new_trailing
            return new_trailing, current_profit / entry, "fixed_stop"



    def compute_raw_profit(self, pos: Dict[str, Any], live_price: float) -> Optional[float]:
        entry = pos.get('entryPrice') or pos.get('entry_price') or pos.get('info', {}).get('entry_price')
        try:
            entry = float(entry)
        except Exception:
            return None
        try:
            size = float(pos.get('size') or pos.get('contracts') or 0)
        except Exception:
            size = 0.0
        return (live_price - entry) * size if size > 0 else (entry - live_price) * abs(size)

    def book_profit(self, pos: Dict[str, Any], live_price: float) -> bool:
        order_id = pos.get('id') or pos.get('orderId') or "unknown"
        try:
            size = float(pos.get('size') or pos.get('contracts') or 0)
        except Exception:
            size = 0.0

        trailing_stop, _, rule = self.update_trailing_stop(pos, live_price)
        if trailing_stop is None:
            return False

        if rule in ["lock_50", "fixed_stop", "break_even"]:
            if size > 0 and live_price < trailing_stop:
                close_order = self.trade_manager.place_market_order("BTCUSD", "sell", size,
                                                                      params={"time_in_force": "ioc"}, force=True)
                logger.info("Trailing stop triggered for long order %s. Closing position: %s", order_id, close_order)
                return True
            elif size < 0 and live_price > trailing_stop:
                close_order = self.trade_manager.place_market_order("BTCUSD", "buy", abs(size),
                                                                      params={"time_in_force": "ioc"}, force=True)
                logger.info("Trailing stop triggered for short order %s. Closing position: %s", order_id, close_order)
                return True
        return False

    def track(self) -> None:
        """
        Main loop to monitor positions and update trailing stops.
        """
        # Wait until the shared websocket provides a live price.
        wait_time = 0
        while self.ws.current_price is None and wait_time < 30:
            logger.info("Waiting for live price update...")
            time.sleep(2)
            wait_time += 2

        if self.ws.current_price is None:
            logger.warning("Live price not available. Exiting profit trailing tracker.")
            return

        while True:
            current_time = time.time()
            if current_time - self.last_position_fetch_time >= self.position_fetch_interval:
                self.cached_positions = self.fetch_open_positions()
                self.last_position_fetch_time = current_time
                if not self.cached_positions:
                    self.position_trailing_stop.clear()
                    self.position_max_profit.clear()

            live_price = self.ws.current_price
            if live_price is None:
                time.sleep(self.check_interval)
                continue

            if not self.cached_positions:
                if self.last_had_positions:
                    logger.info("No open positions. Profit trailing paused.")
                    self.last_had_positions = False
                    self.position_trailing_stop.clear()
                    self.position_max_profit.clear()
            else:
                if not self.last_had_positions:
                    logger.info("Open positions detected. Profit trailing resumed.")
                    self.last_had_positions = True

                for pos in self.cached_positions:
                    # Attempt to extract order id using multiple keys.
                    order_id = pos.get('id') or pos.get('orderId') or "unknown"
                    try:
                        size = float(pos.get('size') or pos.get('contracts') or 0)
                    except Exception:
                        size = 0.0
                    if size == 0:
                        continue

                    try:
                        entry_val = float(pos.get('entryPrice') or pos.get('entry_price') or pos.get('info', {}).get('entry_price', 0))
                    except Exception:
                        entry_val = None

                    profit_pct = self.compute_profit_pct(pos, live_price)
                    profit_display = profit_pct * 100 if profit_pct is not None else 0
                    raw_profit = self.compute_raw_profit(pos, live_price)
                    profit_usd = raw_profit / 1000 if raw_profit is not None else 0
                    trailing_stop, _, rule = self.update_trailing_stop(pos, live_price)

                    side = "long" if size > 0 else "short"
                    max_profit = self.position_max_profit.get(order_id, 0)

                    display = {
                        "entry": entry_val,
                        "live": live_price,
                        "profit": round(profit_display or 0, 2),
                        "usd": round(profit_usd or 0, 2),
                        "rule": rule,
                        "sl": round(trailing_stop or 0, 2),
                        "size": size,
                        "side": side,
                        "max_profit": round(max_profit, 1)
                    }

                    if self.last_display.get(order_id) != display:
                        logger.info(
                            f"Order: {order_id} | Size: {size:.0f} ({side}) | Entry: {entry_val:.1f} | Live: {live_price:.1f} | "
                            f"PnL: {profit_display:.2f}% | USD: {profit_usd:.2f} | Max Profit: {max_profit:.1f} | Rule: {rule} | SL: {trailing_stop:.1f}"
                        )
                        self.last_display[order_id] = display

                    if self.book_profit(pos, live_price):
                        logger.info("Profit booked for order %s.", order_id)

            time.sleep(self.check_interval)

if __name__ == '__main__':
    # For testing, create a dummy websocket object with a current_price attribute.
    class DummyWS:
        current_price = 83000.0

    # Replace DummyWS with your actual shared websocket instance.
    dummy_ws = DummyWS()
    pt = ProfitTrailing(dummy_ws, check_interval=1)
    pt.track()
