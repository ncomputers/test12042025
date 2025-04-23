# profit_trailing.py

import time
import logging
from typing import Dict, Any, List, Optional, Tuple
from exchange import DeltaExchangeClient
import config
from trade_manager import TradeManager
from profit_trailing_rules import fixed_stop

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
        self.target_long: Optional[float] = None
        self.target_short: Optional[float] = None

    def set_zone_limits(
        self,
        supply_max: Optional[float] = None,
        demand_min: Optional[float] = None,
        full_supply_zone: Any = None,
        full_demand_zone: Any = None
    ) -> None:
        """
        Store the zone limits for use as trade targets.
        """
        self.target_long = supply_max
        self.target_short = demand_min

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
                    sym = pos.get('info', {}).get('product_symbol') or pos.get('symbol', '')
                    if sym and config.SYMBOL in sym:
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
        try:
            size = float(pos.get('size') or pos.get('contracts') or 0)
        except Exception:
            size = 0.0
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
        try:
            size = float(pos.get('size') or pos.get('contracts') or 0)
        except Exception:
            size = 0.0
        if size > 0:
            return (live_price - entry) * size
        else:
            return (entry - live_price) * abs(size)

    def update_trailing_stop(
        self,
        pos: Dict[str, Any],
        live_price: float
    ) -> Tuple[Optional[float], Optional[float], Optional[str]]:
        pos_symbol = pos.get('info', {}).get('product_symbol') or pos.get('symbol', 'unknown')
        entry_val = pos.get('info', {}).get('entry_price') or pos.get('entryPrice')
        size_val  = pos.get('size') or pos.get('contracts')
        key = f"{pos_symbol}_{entry_val}_{size_val}"

        try:
            entry = float(entry_val)
            size  = float(size_val or 0)
        except Exception:
            return None, None, None

        # compute and store max profit (absolute)
        current_profit = (live_price - entry) if size > 0 else (entry - live_price)
        prev_max = self.position_max_profit.get(key, 0)
        new_max  = max(prev_max, current_profit)
        self.position_max_profit[key] = new_max

        # choose rule
        if self.take_profit_detected:
            new_trailing = entry
            rule = "breakeven"
        elif size > 0 and self.target_long is not None and live_price >= self.target_long:
            new_trailing = entry + 0.9 * new_max
            rule = "lock_90"
        elif size < 0 and self.target_short is not None and live_price <= self.target_short:
            new_trailing = entry - 0.9 * new_max
            rule = "lock_90"
        else:
            new_trailing, rule = fixed_stop(entry, size)

        self.position_trailing_stop[key] = new_trailing
        profit_pct = new_max / entry if entry else None
        return new_trailing, profit_pct, rule

    def book_profit(self, pos: Dict[str, Any], live_price: float) -> bool:
        pos_symbol = pos.get('info', {}).get('product_symbol') or pos.get('symbol', 'unknown')
        entry_val = pos.get('info', {}).get('entry_price') or pos.get('entryPrice')
        size_val  = pos.get('size') or pos.get('contracts')
        key = f"{pos_symbol}_{entry_val}_{size_val}"
        try:
            size = float(pos.get('size') or pos.get('contracts') or 0)
        except Exception:
            size = 0.0

        trailing_stop, _, rule = self.update_trailing_stop(pos, live_price)
        if trailing_stop is None:
            return False

        should_close = (live_price < trailing_stop) if size > 0 else (live_price > trailing_stop)
        if should_close:
            side = "sell" if size > 0 else "buy"
            try:
                close = self.trade_manager.place_market_order(
                    config.SYMBOL, side, abs(size),
                    params={"time_in_force": "ioc"}, force=True
                )
                logger.info("%s stop triggered (%s). Closed: %s", rule, key, close)
                return True
            except Exception as e:
                logger.error("Failed to close %s on %s stop: %s", key, rule, e)
                return False

        return False

    def track(self) -> None:
        """
        Main loop to monitor positions and update trailing stops.
        """
        # wait for price
        wait = 0
        while self.ws.current_price is None and wait < 30:
            logger.info("Waiting for live price update...")
            time.sleep(2)
            wait += 2

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
                else:
                    if not self.last_had_positions:
                        logger.info("Open positions detected. Profit trailing resumed.")
                        self.last_had_positions = True

            live_price = self.ws.current_price
            if live_price is None:
                time.sleep(self.check_interval)
                continue

            for pos in self.cached_positions:
                try:
                    entry_num = float(pos.get('info', {}).get('entry_price') or pos.get('entryPrice'))
                except Exception:
                    entry_num = None
                try:
                    size = float(pos.get('size') or pos.get('contracts') or 0)
                except Exception:
                    size = 0.0
                if size == 0:
                    continue

                profit_pct = self.compute_profit_pct(pos, live_price) or 0
                profit_display = profit_pct * 100
                raw_profit = self.compute_raw_profit(pos, live_price) or 0
                profit_usd = raw_profit / 1000

                trailing_stop, _, rule = self.update_trailing_stop(pos, live_price)
                # use consistent key logic
                pos_symbol = pos.get('info', {}).get('product_symbol') or pos.get('symbol', 'unknown')
                entry_val  = pos.get('info', {}).get('entry_price') or pos.get('entryPrice')
                size_val   = pos.get('size') or pos.get('contracts')
                key        = f"{pos_symbol}_{entry_val}_{size_val}"
                max_profit = self.position_max_profit.get(key, 0)

                try:
                    api_pnl   = float(pos.get('info', {}).get('unrealized_pnl') or 0)
                    api_entry = float(pos.get('info', {}).get('entry_price') or 0)
                except Exception:
                    api_pnl = api_entry = 0.0

                side   = "long" if size > 0 else "short"
                target = self.target_long if size > 0 else self.target_short
                target_str = f"{target:.2f}" if target is not None else "N/A"

                display = {
                    "entry": entry_num,
                    "api_entry": round(api_entry, 2),
                    "live": live_price,
                    "profit_pct": round(profit_display, 2),
                    "profit_usd": round(profit_usd, 2),
                    "api_pnl": round(api_pnl, 2),
                    "rule": rule,
                    "sl": round(trailing_stop or 0, 2),
                    "target": target_str,
                    "size": size,
                    "side": side,
                    "max_profit": round(max_profit, 2)
                }

                if self.last_display.get(key) != display:
                    logger.info(
                        "Order: %s | Size: %.0f (%s) | Entry: %.2f | Live: %.2f | PnL: %.2f%% | USD: %.2f | "
                        "Max Profit: %.2f | Rule: %s | SL: %.2f | Target: %s",
                        key, size, side, entry_num, live_price,
                        profit_display, profit_usd, max_profit,
                        rule, trailing_stop or 0, target_str
                    )
                    self.last_display[key] = display

                try:
                    if self.book_profit(pos, live_price):
                        logger.info("Profit booked for order %s.", key)
                except Exception as e:
                    logger.error("Error booking profit for %s: %s", key, e)

            time.sleep(self.check_interval)
