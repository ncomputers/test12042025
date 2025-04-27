import time
import logging

class OrderTracker:
    def __init__(self):
        self.orders = []

    def add_order(self, size, leverage, side):
        current_time = time.time()
        order = {
            'size': size,
            'leverage': leverage,
            'side': side,
            'timestamp': current_time
        }
        self.orders.append(order)
        logging.info(f"Order added: {size} BTC, {leverage}x, Side: {side}, Time: {current_time}")

    def get_largest_order_last_hour(self):
        current_time = time.time()
        one_hour_ago = current_time - 3600
        recent_orders = [order for order in self.orders if order['timestamp'] > one_hour_ago]
        if not recent_orders:
            return None
        return max(recent_orders, key=lambda x: x['size'])

order_tracker = OrderTracker()

def generate_signal(candle_data_5min, candle_data_15min, order_book_data, support_resistance_data, volatility_index, sentiment_index, trend_strength):
    """
    Generate trading signal based on combined market data factors.
    """
    volume_imbalance_5min = candle_data_5min.get("volume", 0)
    volume_imbalance_15min = candle_data_15min.get("volume", 0)
    bid_ask_5min = candle_data_5min.get("bid_ask", 0)
    bid_ask_15min = candle_data_15min.get("bid_ask", 0)
    heatmap_buy_sell_5min = candle_data_5min.get("heatmap_buy_sell", 0)
    heatmap_buy_sell_15min = candle_data_15min.get("heatmap_buy_sell", 0)
    aggregated_5min = candle_data_5min.get("aggregated", 0)
    aggregated_15min = candle_data_15min.get("aggregated", 0)
    ema_50 = candle_data_15min.get("ema50", 0)
    ema_200 = candle_data_15min.get("ema200", 0)
    price = candle_data_15min.get("close", 0)

    price_above_ema = (price > ema_50 and price > ema_200)
    price_below_ema = (price < ema_50 and price < ema_200)

    if (aggregated_15min > 15 and aggregated_5min > 15 and
        volume_imbalance_5min > 60 and volume_imbalance_15min > 60 and
        bid_ask_5min > 5 and bid_ask_15min > 5 and
        heatmap_buy_sell_5min > 60 and heatmap_buy_sell_15min > 60 and
        sentiment_index > 50 and trend_strength > 50 and
        price_above_ema):
        order_tracker.add_order(size=1, leverage=25, side='Buy')
        logging.info("Generated Buy Signal")
        return "Buy"
    elif (aggregated_5min < -15 and aggregated_15min < -15 and
          volume_imbalance_5min < -60 and volume_imbalance_15min < -60 and
          bid_ask_5min < -5 and bid_ask_15min < -5 and
          heatmap_buy_sell_5min < -60 and heatmap_buy_sell_15min < -60 and
          sentiment_index < -50 and trend_strength < -50 and
          price_below_ema):
        order_tracker.add_order(size=1, leverage=25, side='Sell')
        logging.info("Generated Sell Signal")
        return "Sell"

    logging.info("No Trading Signal Generated")
    return "No Signal"

def log_largest_order_last_hour():
    largest_order = order_tracker.get_largest_order_last_hour()
    if largest_order:
        logging.info(f"Largest order in the last hour: {largest_order['size']} BTC, {largest_order['leverage']}x, {largest_order['side']}")
    else:
        logging.info("No orders executed in the last hour.")
