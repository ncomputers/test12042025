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
        self.ws = ws_instance
        self.client = DeltaExchangeClient()
        self.trade_manager = TradeManager()
        self.check_interval: int = check_interval
        self.position_trailing_stop: Dict[Any, float] = {}
        self.last_had_positions: bool = True
        self.last_position_fetch_time: float = 0.0
        self.position_fetch_interval: int = 5
        self.cached_positions: List[Dict[str, Any]] = []
        self.last_display: Dict[Any, Dict[str, Any]] = {}
        self.position_max_profit: Dict[Any, float] = {}
        self.take_profit_detected: bool = False

    def fetch_open_positions(self) -> List[Dict[str, Any]]:
        try:
            positions = self.client.fetch_positions()
            open_positions: List[Dict[str, Any]] = []
            for pos in positions:
                try:
                    size = float(pos.get('size') or pos.get('contracts') or 0)
                except Exception:
                    size = 0.0
                if size != 0:
                    pos_symbol = pos.get('info', {}).get('product_symbol') or pos.get('symbol', '')
                    if pos_symbol and config.SYMBOL in pos_symbol:
                        open_positions.append(pos)
            return open_positions
        except Exception as e:
            logger.error("Error fetching open positions: %s", e)
            return []

    def compute_profit_pct(self, pos: Dict[str, Any], live_price: float) -> Optional[float]:
        entry = pos.get('info', {}).get('entry_price') or pos.get('entryPrice')
        try:
            entry = float(entry)
        except Exception:
            return None
        size = 0.0
        try:
            size = float(pos.get('size') or pos.get('contracts') or 0)
        except Exception:
            pass
        if size > 0:
            return (live_price - entry) / entry
        else:
            return (entry - live_price) / entry

    def compute_raw_profit(self, pos: Dict[str, Any], live_price: float) -> Optional[float]:
        entry = pos.get('info', {}).get('entry_price') or pos.get('entryPrice')
        try:
            entry = float(entry)
        except Exception:
            return None
        size = 0.0
        try:
            size = float(pos.get('size') or pos.get('contracts') or 0)
        except Exception:
            pass
        if size > 0:
            return (live_price - entry) * size
        else:
            return (entry - live_price) * abs(size)

    def update_trailing_stop(self, pos: Dict[str, Any], live_price: float) -> Tuple[Optional[float], Optional[float], Optional[str]]:
        pos_symbol = pos.get('info', {}).get('product_symbol') or pos.get('symbol', 'unknown')
        entry_val = pos.get('info', {}).get('entry_price') or pos.get('entryPrice')
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

        current_profit = live_price - entry if size > 0 else entry - live_price
        prev_max = self.position_max_profit.get(key, 0)
        new_max = max(prev_max, current_profit)
        self.position_max_profit[key] = new_max

        if self.take_profit_detected and new_max >= 400:
            new_trailing = entry + (new_max / 2) if size > 0 else entry - (new_max / 2)
            rule = "lock_50"
        else:
            default_offset = entry * (config.FIXED_STOP_OFFSET_PERCENT / 100)
            new_trailing = entry - default_offset if size > 0 else entry + default_offset
            rule = "fixed_stop"

        self.position_trailing_stop[key] = new_trailing
        profit_pct = new_max / entry
        return new_trailing, profit_pct, rule

    def book_profit(self, pos: Dict[str, Any], live_price: float) -> bool:
        pos_symbol = pos.get('info', {}).get('product_symbol') or pos.get('symbol', 'unknown')
        entry_val = pos.get('info', {}).get('entry_price') or pos.get('entryPrice')
        size_val = pos.get('size') or pos.get('contracts')
        key = f"{pos_symbol}_{entry_val}_{size_val}"
        try:
            size = float(pos.get('size') or pos.get('contracts') or 0)
        except Exception:
            size = 0.0

        trailing_stop, _, rule = self.update_trailing_stop(pos, live_price)
        if trailing_stop is None:
            return False

        if rule == "lock_50":
            if size > 0 and live_price < trailing_stop:
                try:
                    close = self.trade_manager.place_market_order(
                        config.SYMBOL, "sell", size,
                        params={"time_in_force": "ioc"}, force=True
                    )
                    logger.info("Trailing stop triggered for %s. Closed: %s", key, close)
                    return True
                except Exception as e:
                    logger.error("Failed to close %s on trailing stop: %s", key, e)
                    return False
            elif size < 0 and live_price > trailing_stop:
                try:
                    close = self.trade_manager.place_market_order(
                        config.SYMBOL, "buy", abs(size),
                        params={"time_in_force": "ioc"}, force=True
                    )
                    logger.info("Trailing stop triggered for %s. Closed: %s", key, close)
                    return True
                except Exception as e:
                    logger.error("Failed to close %s on trailing stop: %s", key, e)
                    return False
        else:
            if size > 0 and live_price < trailing_stop:
                try:
                    close = self.trade_manager.place_market_order(
                        config.SYMBOL, "sell", size,
                        params={"time_in_force": "ioc"}, force=True
                    )
                    logger.info("Trailing stop triggered for %s. Closed: %s", key, close)
                    return True
                except Exception as e:
                    logger.error("Failed to close %s on trailing stop: %s", key, e)
                    return False
            elif size < 0 and live_price > trailing_stop:
                try:
                    close = self.trade_manager.place_market_order(
                        config.SYMBOL, "buy", abs(size),
                        params={"time_in_force": "ioc"}, force=True
                    )
                    logger.info("Trailing stop triggered for %s. Closed: %s", key, close)
                    return True
                except Exception as e:
                    logger.error("Failed to close %s on trailing stop: %s", key, e)
                    return False
        return False

    def track(self) -> None:
        """
        Main loop to monitor positions and update trailing stops.
        """
        wait_time = 0
        while self.ws.current_price is None and wait_time < 30:
            logger.info("Waiting for live price update...")
            time.sleep(2)
            wait_time += 2

        if self.ws.current_price is None:
            logger.warning("Live price not available. Exiting profit trailing tracker.")
            return

        while True:
            now = time.time()
            if now - self.last_position_fetch_time >= self.position_fetch_interval:
                self.cached_positions = self.fetch_open_positions()
                self.last_position_fetch_time = now
                if not self.cached_positions:
                    if self.last_had_positions:
                        logger.info("No open positions. Profit trailing paused.")
                        self.last_had_positions = False
                    self.position_trailing_stop.clear()
                    self.position_max_profit.clear()
                    time.sleep(self.check_interval)
                    continue

            live_price = self.ws.current_price
            if live_price is None:
                time.sleep(self.check_interval)
                continue

            if self.cached_positions and not self.last_had_positions:
                logger.info("Open positions detected. Profit trailing resumed.")
                self.last_had_positions = True

            for pos in self.cached_positions:
                entry_num = None
                try:
                    entry_num = float(pos.get('info', {}).get('entry_price') or pos.get('entryPrice'))
                except Exception:
                    pass

                size = 0.0
                try:
                    size = float(pos.get('size') or pos.get('contracts') or 0)
                except Exception:
                    pass
                if size == 0:
                    continue

                profit_pct = self.compute_profit_pct(pos, live_price) or 0
                profit_display = profit_pct * 100
                raw_profit = self.compute_raw_profit(pos, live_price) or 0
                profit_usd = raw_profit / 1000

                trailing_stop, _, rule = self.update_trailing_stop(pos, live_price)
                max_profit = self.position_max_profit.get(
                    f"{pos.get('info', {}).get('product_symbol')}_{pos.get('info', {}).get('entry_price')}_{pos.get('size')}",
                    0
                )

                # API PnL
                try:
                    api_pnl = float(pos.get('info', {}).get('unrealized_pnl') or 0)
                    api_entry   = float(pos.get('info', {}).get('entry_price') or 0)

                    
                except Exception:
                    api_pnl = 0.0
                    api_entry = 0.0


                side = "long" if size > 0 else "short"
                key = f"{pos.get('info', {}).get('product_symbol')}_{pos.get('info', {}).get('entry_price')}_{pos.get('size')}"

                display = {
                    "entry": entry_num,
                    "api_entry": round(api_entry, 2),         # include in your display dict
                    "live": live_price,
                    "profit_pct": round(profit_display, 2),
                    "profit_usd": round(profit_usd, 2),
                    "api_pnl": round(api_pnl, 2),
                    "rule": rule,
                    "sl": round(trailing_stop or 0, 2),
                    "size": size,
                    "side": side,
                    "max_profit": round(max_profit, 2)
                }

                if self.last_display.get(key) != display:
                    logger.info(
                        f"Order: {key} | Size: {size:.0f} ({side}) | Entry: {entry_num:.1f} | API_Entry: {api_entry:.1f} | "
                        f"Live: {live_price:.1f} | PnL: {profit_display:.2f}% | "
                        f"USD: {profit_usd:.2f} | API PnL: {api_pnl:.2f} | "
                        f"Max Profit: {max_profit:.2f} | Rule: {rule} | SL: {trailing_stop:.2f}"
                    )
                    self.last_display[key] = display

                try:
                    if self.book_profit(pos, live_price):
                        logger.info("Profit booked for order %s.", key)
                except Exception as e:
                    logger.error("Error booking profit for %s: %s", key, e)

            time.sleep(self.check_interval)
