import time
import logging
import json
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
        self.position_trailing_stop: Dict[Any, float] = {}   # key -> trailing stop price
        self.last_had_positions: bool = True
        self.last_position_fetch_time: float = 0.0
        self.position_fetch_interval: int = 5  # seconds between position fetches
        self.cached_positions: List[Dict[str, Any]] = []
        self.last_display: Dict[Any, Dict[str, Any]] = {}
        # Store the maximum profit in absolute terms for each position key.
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
        """
        pos_symbol = pos.get("info", {}).get("product_symbol") or pos.get("symbol", "unknown")
        entry_val = pos.get('entryPrice') or pos.get('entry_price') or pos.get('info', {}).get('entry_price')
        size_val = pos.get('size') or pos.get('contracts')
        key = f"{pos_symbol}_{entry_val}_{size_val}"
        
        try:
            entry = float(entry_val)
        except Exception:
            return None, None, None
        try:
            size = float(size_val or 0)
        except Exception:
            size = 0.0
        if size == 0:
            return None, None, None

        # Calculate current profit in absolute terms.
        current_profit = live_price - entry if size > 0 else entry - live_price

        # Update maximum profit recorded for this position.
        prev_max = self.position_max_profit.get(key, 0)
        new_max_profit = max(prev_max, current_profit)
        self.position_max_profit[key] = new_max_profit

        # If a take profit signal is active and profit exceeds threshold, use profit-locking.
        if self.take_profit_detected and new_max_profit >= 400:
            new_trailing = entry + (new_max_profit / 2) if size > 0 else entry - (new_max_profit / 2)
            rule = "lock_50"
        else:
            # Default to fixed offset defined in config.
            default_offset = entry * (config.FIXED_STOP_OFFSET_PERCENT / 100)
            new_trailing = entry - default_offset if size > 0 else entry + default_offset
            rule = "fixed_stop"
        self.position_trailing_stop[key] = new_trailing
        return new_trailing, new_max_profit / entry, rule

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
        # Generate the same key as in update_trailing_stop.
        pos_symbol = pos.get("info", {}).get("product_symbol") or pos.get("symbol", "unknown")
        entry_val = pos.get('entryPrice') or pos.get('entry_price') or pos.get('info', {}).get('entry_price')
        size_val = pos.get('size') or pos.get('contracts')
        key = f"{pos_symbol}_{entry_val}_{size_val}"
        
        try:
            size = float(pos.get('size') or pos.get('contracts') or 0)
        except Exception:
            size = 0.0

        trailing_stop, _, rule = self.update_trailing_stop(pos, live_price)
        if trailing_stop is None:
            return False

        # If the price breaches the trailing stop, trigger a market close.
        if rule == "lock_50":
            if size > 0 and live_price < trailing_stop:
                try:
                    close_order = self.trade_manager.place_market_order(
                        "BTCUSD", "sell", size,
                        params={"time_in_force": "ioc"}, force=True
                    )
                    logger.info("Trailing stop triggered for %s. Closed: %s", key, close_order)
                    return True
                except Exception as e:
                    logger.error("Failed to close %s on trailing stop: %s", key, e)
                    return False
            elif size < 0 and live_price > trailing_stop:
                try:
                    close_order = self.trade_manager.place_market_order(
                        "BTCUSD", "buy", abs(size),
                        params={"time_in_force": "ioc"}, force=True
                    )
                    logger.info("Trailing stop triggered for %s. Closed: %s", key, close_order)
                    return True
                except Exception as e:
                    logger.error("Failed to close %s on trailing stop: %s", key, e)
                    return False
        else:
            if size > 0 and live_price < trailing_stop:
                try:
                    close_order = self.trade_manager.place_market_order(
                        "BTCUSD", "sell", size,
                        params={"time_in_force": "ioc"}, force=True
                    )
                    logger.info("Trailing stop triggered for %s. Closed: %s", key, close_order)
                    return True
                except Exception as e:
                    logger.error("Failed to close %s on trailing stop: %s", key, e)
                    return False
            elif size < 0 and live_price > trailing_stop:
                try:
                    close_order = self.trade_manager.place_market_order(
                        "BTCUSD", "buy", abs(size),
                        params={"time_in_force": "ioc"}, force=True
                    )
                    logger.info("Trailing stop triggered for %s. Closed: %s", key, close_order)
                    return True
                except Exception as e:
                    logger.error("Failed to close %s on trailing stop: %s", key, e)
                    return False
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
                    pos_symbol = pos.get("info", {}).get("product_symbol") or pos.get("symbol", "unknown")
                    entry_val = pos.get('entryPrice') or pos.get('entry_price') or pos.get('info', {}).get('entry_price')
                    size_val = pos.get('size') or pos.get('contracts')
                    key = f"{pos_symbol}_{entry_val}_{size_val}"

                    try:
                        size = float(pos.get('size') or pos.get('contracts') or 0)
                    except Exception:
                        size = 0.0
                    if size == 0:
                        continue

                    try:
                        entry_num = float(entry_val)
                    except Exception:
                        entry_num = None

                    profit_pct = self.compute_profit_pct(pos, live_price)
                    profit_display = profit_pct * 100 if profit_pct is not None else 0
                    raw_profit = self.compute_raw_profit(pos, live_price)
                    profit_usd = raw_profit / 1000 if raw_profit is not None else 0
                    trailing_stop, ratio, rule = self.update_trailing_stop(pos, live_price)
                    side = "long" if size > 0 else "short"
                    max_profit = self.position_max_profit.get(key, 0)

                    display = {
                        "entry": entry_num,
                        "live": live_price,
                        "profit": round(profit_display or 0, 2),
                        "usd": round(profit_usd or 0, 2),
                        "rule": rule,
                        "sl": round(trailing_stop or 0, 2),
                        "size": size,
                        "side": side,
                        "max_profit": round(max_profit, 1)
                    }

                    if self.last_display.get(key) != display:
                        logger.info(
                            f"Order: {key} | Size: {size:.0f} ({side}) | Entry: {entry_num:.1f} | Live: {live_price:.1f} | "
                            f"PnL: {profit_display:.2f}% | USD: {profit_usd:.2f} | Max Profit: {max_profit:.1f} | Rule: {rule} | SL: {trailing_stop:.1f}"
                        )
                        self.last_display[key] = display

                    # Wrapped book_profit in try/except to prevent thread crash
                    try:
                        if self.book_profit(pos, live_price):
                            logger.info("Profit booked for order %s.", key)
                    except Exception as e:
                        logger.error("Error booking profit for %s: %s", key, e)

            time.sleep(self.check_interval)

if __name__ == '__main__':
    # For testing, create a dummy websocket object with a current_price attribute.
    class DummyWS:
        current_price = 83000.0

    dummy_ws = DummyWS()
    pt = ProfitTrailing(dummy_ws, check_interval=1)
    pt.track()
