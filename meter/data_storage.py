# data_storage.py

import redis
import json
import time

class DataStorage:
    """
    Simple Redis-based storage for live metrics.
    """

    def __init__(self, host='localhost', port=6379, db=0):
        self.redis = redis.StrictRedis(host=host, port=port, db=db, decode_responses=True)

    def store(self, key: str, data: dict):
        """
        Push a JSON-serialized dict with timestamp into a Redis list.
        """
        entry = data.copy()
        entry["timestamp"] = time.time()
        self.redis.lpush(key, json.dumps(entry))
