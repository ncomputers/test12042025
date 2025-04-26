# demand_supply_zones.py

from collections import defaultdict

MIN_ZONE_VOLUME_THRESHOLD = 0.10  # 10% of peak

def aggregate_volumes(order_book_data):
    demand, supply = defaultdict(float), defaultdict(float)
    for item in order_book_data:
        p = item.get("Price")
        a = item.get("Amount", 0)
        if p is None: 
            continue
        if a > 0:
            demand[p] += a
        elif a < 0:
            supply[p] += abs(a)
    return demand, supply

def compute_zone_boundaries(volume_dict):
    """
    Select all buckets ≥ threshold×peak; if none, fall back to the single peak bucket.
    Returns (low_price, high_price, total_volume).
    """
    if not volume_dict:
        return None
    # find peak
    peak_price = max(volume_dict, key=lambda p: volume_dict[p])
    peak_vol   = volume_dict[peak_price]
    thresh     = peak_vol * MIN_ZONE_VOLUME_THRESHOLD

    # select any bucket ≥ threshold
    selected = [p for p, vol in volume_dict.items() if vol >= thresh]
    if not selected:
        # fallback: single peak bucket
        return peak_price, peak_price, peak_vol

    total = sum(volume_dict[p] for p in selected)
    return min(selected), max(selected), total

def calculate_zones(order_book_data):
    demand, supply = aggregate_volumes(order_book_data)
    return {
        "demand_zone": compute_zone_boundaries(demand),
        "supply_zone": compute_zone_boundaries(supply)
    }
