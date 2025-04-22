# profit_trailing_rules.py

import config
from typing import Tuple

def fixed_stop(entry: float, size: float) -> Tuple[float, str]:
    """
    Always fall back to a fixed‐offset stop.
    """
    offset = entry * (config.FIXED_STOP_OFFSET_PERCENT / 100)
    trailing = entry - offset if size > 0 else entry + offset
    return trailing, "fixed_stop"

def lock_50_rule(entry: float, size: float, max_profit: float) -> Tuple[float, str]:
    """
    Once TP has been signaled and profit >= threshold,
    lock at 50% of max profit.
    """
    trailing = entry + (max_profit / 2) if size > 0 else entry - (max_profit / 2)
    return trailing, "lock_50"
