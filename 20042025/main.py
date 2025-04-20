import threading
import logging
import signal
import sys
from logger import setup_logging
from binance_ws import BinanceWebsocket
from profit_trailing import ProfitTrailing
from signal_processor import SignalProcessor
import config


def main() -> None:
    # Set up logging (truncates old logs on restart)
    setup_logging()
    logger = logging.getLogger(__name__)
    logger.info("Starting main application for %s", config.SYMBOL)

    # Shared WebSocket for live price data
    ws = BinanceWebsocket()
    ws.start()

    # Profit trailing manager
    pt_tracker = ProfitTrailing(ws_instance=ws, check_interval=getattr(config, 'PROFIT_CHECK_INTERVAL', 1))
    pt_thread = threading.Thread(target=pt_tracker.track, daemon=True)
    pt_thread.start()

    # Signal processor
    sp = SignalProcessor(ws_instance=ws, profit_trailing=pt_tracker)
    sp_thread = threading.Thread(
        target=sp.process_signals_loop,
        kwargs={'sleep_interval': getattr(config, 'SIGNAL_POLL_INTERVAL', 5)},
        daemon=True
    )
    sp_thread.start()

    # Graceful shutdown handler
    def shutdown(signum, frame):
        logger.info("Shutdown signal received, stopping application...")
        ws.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Keep the main thread alive on all platforms
    stop_event = threading.Event()
    try:
        stop_event.wait()
    except (KeyboardInterrupt, SystemExit):
        shutdown(None, None)


if __name__ == '__main__':
    main()