# additional_factors.py

def candle_range_percent(candle: dict) -> float:
    """
    (high - low) / open * 100
    """
    try:
        high = float(candle.get("high", 0))
        low = float(candle.get("low", 0))
        o = float(candle.get("open", 0))
        return ((high - low) / o) * 100 if o else 0.0
    except:
        return 0.0

def bid_ask_percent(candle: dict) -> float:
    """
    (bidVolume - askVolume) / (bidVolume + askVolume) * 100
    """
    try:
        b = float(candle.get("bidVolume", 0))
        a = float(candle.get("askVolume", 0))
        total = b + a
        return ((b - a) / total) * 100 if total else 0.0
    except:
        return 0.0

def heatmap_flow_percent(candle: dict) -> float:
    """
    Net buy/sell % from heatmap around current price.
    """
    heatmap = candle.get("heatmapOrderBook") or candle.get("heatmap") or []
    try:
        cp = float(candle.get("close", 0))
    except:
        return 0.0
    buys = sells = 0.0
    for i in range(0, len(heatmap), 2):
        try:
            price = float(heatmap[i])
            vol   = float(heatmap[i+1])
            if price < cp:
                buys += vol
            elif price > cp:
                sells += vol
        except:
            continue
    total = buys + sells
    return ((buys - sells) / total) * 100 if total else 0.0

def heatmap_delta_average_percent(candle: dict) -> float:
    """
    Avg heatmapDelta[*][1] / close * 100
    """
    try:
        cp = float(candle.get("close", 0))
        delta = candle.get("heatmapDelta") or []
        vals = [float(delta[i+1]) for i in range(0, len(delta), 2)]
        return (sum(vals)/len(vals) / cp) * 100 if vals and cp else 0.0
    except:
        return 0.0

def heatmap_buy_sell_ratio(candle: dict) -> float:
    """
    (sum(heatmapBuys volumes) - sum(heatmapSells volumes)) / total * 100
    """
    try:
        buys  = candle.get("heatmapBuys") or []
        sells = candle.get("heatmapSells") or []
        tb = sum(float(buys[i+1]) for i in range(0, len(buys), 2))
        ts = sum(float(sells[i+1]) for i in range(0, len(sells), 2))
        total = tb + ts
        return ((tb - ts) / total) * 100 if total else 0.0
    except:
        return 0.0
