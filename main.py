import threading
import logging
from logger import setup_logging
from profit_trailing import ProfitTrailing
from signal_processor import SignalProcessor
from binance_ws import BinanceWebsocket

def main() -> None:
    setup_logging()
    logger = logging.getLogger(__name__)
    logger.info("Starting main application with shared websocket instance.")

    # Create a single shared BinanceWebsocket instance and start it.
    ws_instance = BinanceWebsocket()
    ws_instance.start()

    # Inject the shared websocket into the ProfitTrailing and SignalProcessor modules.
    pt_tracker = ProfitTrailing(ws_instance, check_interval=1)
    sp = SignalProcessor(ws_instance)

    # Start profit trailing in a daemon thread.
    pt_thread = threading.Thread(target=pt_tracker.track, daemon=True)
    pt_thread.start()

    # Run signal processor loop in the main thread.
    sp.process_signals_loop(sleep_interval=5)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("Application interrupted, shutting down...")
