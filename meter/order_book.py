import logging

class OrderBookImbalance:
    def __init__(self, strong_buy_threshold: float = 0.5, strong_sell_threshold: float = -0.5):
        self.strong_buy = strong_buy_threshold
        self.strong_sell = strong_sell_threshold

    def calculate_imbalance(self, order_book_data: list) -> dict:
        """
        Calculate the order book imbalance and return the trend signal.
        """
        buy_amount = sum(x['Amount'] for x in order_book_data if x['Amount'] > 0)
        sell_amount = abs(sum(x['Amount'] for x in order_book_data if x['Amount'] < 0))
        total = buy_amount + sell_amount
        imbalance = (buy_amount - sell_amount) / total if total else 0.0

        if imbalance >= self.strong_buy:
            sig = {"imbalance": imbalance, "trend": "up", "signal": "strong_buy"}
        elif imbalance <= self.strong_sell:
            sig = {"imbalance": imbalance, "trend": "down", "signal": "strong_sell"}
        else:
            sig = {"imbalance": imbalance, "trend": "neutral"}

        logging.info(f"Order Book Imbalance: {sig}")
        return sig
