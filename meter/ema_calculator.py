import redis
import logging

class EMACalculator:
    def __init__(self, period: int, redis_key: str, redis_host: str = 'localhost', redis_port: int = 6379, redis_db: int = 0):
        self.period = period
        self.redis_key = redis_key
        self.redis = redis.StrictRedis(host=redis_host, port=redis_port, db=redis_db, decode_responses=True)
        self.multiplier = 2 / (period + 1)
        self.initialized = False
        self.current_ema = None

    def update(self, price: float) -> float:
        """
        Update EMA based on the new price using the standard formula.
        """
        try:
            if not self.initialized:
                stored = self.redis.get(self.redis_key)
                if stored is not None:
                    try:
                        self.current_ema = float(stored)
                    except ValueError:
                        logging.warning("Stored EMA not valid; initializing with current price.")
                        self.current_ema = price
                else:
                    self.current_ema = price
                self.initialized = True
            else:
                self.current_ema = (price - self.current_ema) * self.multiplier + self.current_ema

            self.redis.set(self.redis_key, self.current_ema)
            logging.debug(f"Updated EMA ({self.redis_key}): {self.current_ema}")
            return self.current_ema
        except Exception as e:
            logging.error(f"Error updating EMA ({self.redis_key}): {e}")
            return price  # fallback to current price if error occurs
