# market_factors.py

def price_divergence(prices: list, volumes: list) -> float:
    """
    (Δprice / Δvolume) * 100
    """
    if len(prices) < 2 or len(volumes) < 2:
        return 0.0
    dp = prices[-1] - prices[-2]
    dv = volumes[-1] - volumes[-2]
    return (dp / dv) * 100 if dv else 0.0

def volume_accumulation_distribution(prices: list, volumes: list) -> float:
    """
    Sum signed volumes: +volume if price↑, -volume if price↓.
    """
    if len(prices) < 2 or len(volumes) < 2:
        return 0.0
    total = 0.0
    for i in range(1, len(prices)):
        total += volumes[i] if prices[i] > prices[i-1] else -volumes[i]
    return total

def order_book_cluster_analysis(order_book: dict, threshold: float = 0.05) -> dict:
    """
    Find buy/sell clusters where volume ≥ threshold.
    """
    if not order_book:
        return {"buy": [], "sell": []}
    prices = sorted(order_book)
    low, high = prices[0], prices[-1]
    buy = [(p, v) for p, v in order_book.items() if p < low and v >= threshold]
    sell = [(p, v) for p, v in order_book.items() if p > high and v >= threshold]
    return {"buy": buy, "sell": sell}

def support_resistance_levels(order_book: dict, current_price: float, threshold: float = 0.05) -> dict:
    """
    Lists (price, volume) below/above current_price with volume ≥ threshold.
    """
    supp = [(p, v) for p, v in order_book.items() if p < current_price and v >= threshold]
    resi = [(p, v) for p, v in order_book.items() if p > current_price and v >= threshold]
    supp.sort(key=lambda x: x[1], reverse=True)
    resi.sort(key=lambda x: x[1], reverse=True)
    return {"support": supp, "resistance": resi}

def volatility_index(highs: list, lows: list, closes: list) -> float:
    """
    Average (high - low) / close * 100 over all bars.
    """
    if not highs or not lows or not closes or len(highs) != len(lows) != len(closes):
        return 0.0
    vals = [(h - l) / c * 100 if c else 0.0 for h, l, c in zip(highs, lows, closes)]
    return sum(vals) / len(vals)

def market_sentiment_index(price_trend: float, order_imbalance: float, volume_imbalance: float) -> float:
    """
    Average of key trend parameters.
    """
    return (price_trend + order_imbalance + volume_imbalance) / 3

def trend_strength(price_trend: float, volume_trend: float, momentum_trend: float) -> float:
    """
    Average of price, volume, and momentum trends.
    """
    return (price_trend + volume_trend + momentum_trend) / 3
