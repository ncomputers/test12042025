import json
import threading
import time
import websocket
import logging

class BinanceWebsocket:
    def __init__(self, stream_url="wss://fstream.binance.com/ws", subscribe_params=["btcusdt@aggTrade"], reconnect_interval=10):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.stream_url = stream_url
        self.subscribe_params = subscribe_params
        self.reconnect_interval = reconnect_interval  # seconds without update before reconnecting
        self.current_price = None
        self.last_update_time = time.time()
        self.ws_app = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

    def _on_message(self, ws, message):
        try:
            data = json.loads(message)
            # Validate that required keys exist.
            if "p" not in data or "q" not in data or "m" not in data:
                return
            price = float(data["p"])
            with self._lock:
                self.current_price = price
                self.last_update_time = time.time()
            self.logger.debug("Received price update: %s", price)
        except Exception as e:
            self.logger.error("Error processing message: %s", e)

    def _on_error(self, ws, error):
        self.logger.error("WebSocket error: %s", error)

    def _on_close(self, ws, close_status_code, close_msg):
        self.logger.info("WebSocket closed: code=%s, message=%s", close_status_code, close_msg)

    def _on_open(self, ws):
        self.logger.info("WebSocket connection opened")
        subscribe_message = {
            "method": "SUBSCRIBE",
            "params": self.subscribe_params,
            "id": 1
        }
        ws.send(json.dumps(subscribe_message))
        self.logger.debug("Sent subscription message: %s", subscribe_message)

    def _start_socket(self):
        self.ws_app = websocket.WebSocketApp(
            self.stream_url,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close
        )
        self.ws_app.on_open = self._on_open
        self.logger.info("Starting WebSocket connection to %s", self.stream_url)
        self.ws_app.run_forever()
        self.logger.info("WebSocket run_forever loop exited")

    def start(self):
        """
        Start the websocket connection and the monitor thread.
        """
        self._stop_event.clear()
        self.socket_thread = threading.Thread(target=self._start_socket, daemon=True)
        self.socket_thread.start()

        self.monitor_thread = threading.Thread(target=self._monitor_connection, daemon=True)
        self.monitor_thread.start()

    def _monitor_connection(self):
        """
        Monitor live price updates and reconnect if no update is received within the reconnection interval.
        """
        while not self._stop_event.is_set():
            time.sleep(3)
            with self._lock:
                time_since_update = time.time() - self.last_update_time
            if time_since_update > self.reconnect_interval:
                self.logger.warning("No price update in last %s seconds. Reconnecting...", self.reconnect_interval)
                try:
                    if self.ws_app:
                        self.ws_app.close()
                except Exception as e:
                    self.logger.error("Error closing websocket: %s", e)
                # Restart socket in a new thread.
                self.socket_thread = threading.Thread(target=self._start_socket, daemon=True)
                self.socket_thread.start()

    def stop(self):
        """
        Stop the websocket connection and monitoring.
        """
        self._stop_event.set()
        if self.ws_app:
            self.ws_app.close()
        if self.socket_thread.is_alive():
            self.socket_thread.join(timeout=5)
        if self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=5)
        self.logger.info("Stopped BinanceWebsocket.")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ws = BinanceWebsocket()
    ws.start()
    try:
        while True:
            with ws._lock:
                if ws.current_price is not None:
                    print(f"Latest BTC/USDT price: {ws.current_price}")
            time.sleep(2)
    except KeyboardInterrupt:
        ws.stop()
