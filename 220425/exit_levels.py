# exit_levels.py

import math

# Defines what percentage of the original position to exit at each TP level
LEVEL_PCTS = {
    1: 0.50,  # TP  → exit 50% of original size
    2: 0.25,  # TP1 → exit 25% of original size
    3: 0.25,  # TP2 → exit 25% of original size
}

def compute_exit_amount(original_size: float, exited_so_far: float, level: int) -> float:
    """
    Calculate how much quantity to exit at a given take‑profit level.

    Parameters:
      original_size   – the starting position size (e.g. 10 lots)
      exited_so_far   – total quantity already exited on previous TPs
      level           – 1 for first TP, 2 for second, 3 for third, etc.

    Returns:
      quantity_to_exit (floored to nearest whole lot, never exceeding remaining)
    """
    pct = LEVEL_PCTS.get(level, 0)
    target_qty = original_size * pct
    # floor to nearest lower whole lot
    qty = math.floor(target_qty)
    remaining = original_size - exited_so_far
    # never exit more than what's left
    return max(0, min(qty, remaining))
