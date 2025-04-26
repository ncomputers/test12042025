# timeframe_processor.py

from additional_factors import (
    candle_range_percent,
    bid_ask_percent,
    heatmap_flow_percent,
    heatmap_delta_average_percent,
    heatmap_buy_sell_ratio
)

def simple_price_trend_percent(candle: dict) -> float:
    try:
        o, c = float(candle.get("open", 0)), float(candle.get("close", 0))
        return ((c - o) / o) * 100 if o else 0.0
    except:
        return 0.0

# timeframe_processor.py (only vwap_trend_percent changed)

# timeframe_processor.py (vwap only)

def vwap_trend_percent(candle_data: dict) -> float:
    close = float(candle_data.get("close", 0))
    vwap  = float(candle_data.get("vwap", 0))
    if abs(vwap) < 1e-6:
        return 0.0
    pct = ((close - vwap) / vwap) * 100
    # clamp to ±100%
    return pct if abs(pct) <= 100 else 0.0



def volume_imbalance_percent(candle: dict) -> float:
    try:
        buy = float(candle.get("buyVolume", 0))
        sell = float(candle.get("sellVolume", 0))
        total = buy + sell
        return ((buy - sell) / total) * 100 if total else 0.0
    except:
        return 0.0

def process_timeframe_without_rsi(candle: dict) -> dict:
    """
    Returns per‐TF metrics:
      price, vwap, volume, bid_ask, heatmap, heatmap_delta, heatmap_buy_sell, overall
    """
    price_pct      = simple_price_trend_percent(candle)
    vwap_pct       = vwap_trend_percent(candle)
    volume_pct     = volume_imbalance_percent(candle)
    bid_ask_pct    = bid_ask_percent(candle)
    heatmap_pct    = heatmap_flow_percent(candle)
    delta_pct      = heatmap_delta_average_percent(candle)
    buy_sell_pct   = heatmap_buy_sell_ratio(candle)

    factors = {
        "price": price_pct,
        "vwap": vwap_pct,
        "volume": volume_pct,
        "bid_ask": bid_ask_pct,
        "heatmap": heatmap_pct,
        "heatmap_delta": delta_pct,
        "heatmap_buy_sell": buy_sell_pct
    }

    overall = sum(factors.values()) / len(factors) if factors else 0.0

    return {"factors": factors, "overall": overall}
