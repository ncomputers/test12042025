import threading
import logging
from logger import setup_logging
from profit_trailing import ProfitTrailing
from signal_processor import SignalProcessor
from binance_ws import BinanceWebsocket

def main() -> None:
    # Set up centralized logging
    setup_logging()
    logger = logging.getLogger(__name__)
    logger.info("Starting main application with shared websocket instance.")

    # Create and start a shared BinanceWebsocket instance
    ws_instance = BinanceWebsocket()
    ws_instance.start()

    # Create the ProfitTrailing instance for trailing stop management
    pt_tracker = ProfitTrailing(ws_instance, check_interval=1)
    
    # Create the SignalProcessor, injecting both the shared websocket and the ProfitTrailing instance
    sp = SignalProcessor(ws_instance, profit_trailing=pt_tracker)

    # Start the profit trailing tracking in its own (daemon) thread
    pt_thread = threading.Thread(target=pt_tracker.track, daemon=True)
    pt_thread.start()

    # Run the signal processor loop (this will keep running in the main thread)
    sp.process_signals_loop(sleep_interval=5)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("Application interrupted, shutting down...")
