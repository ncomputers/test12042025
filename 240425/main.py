# main.py

import threading
import logging
import signal
from logger import setup_logging
from binance_ws import BinanceWebsocket
from profit_trailing import ProfitTrailing
from signal_processor import SignalProcessor
import config

def main() -> None:
    # 1) Configure logging
    setup_logging()
    logger = logging.getLogger(__name__)
    logger.info("Starting main application for %s", config.SYMBOL)

    # 2) Start shared price feed
    ws = BinanceWebsocket()
    ws.start()

    # 3) Instantiate ProfitTrailing and SignalProcessor
    pt_tracker = ProfitTrailing(
        ws_instance=ws,
        check_interval=getattr(config, 'PROFIT_CHECK_INTERVAL', 1)
    )
    sp = SignalProcessor(
        ws_instance=ws,
        profit_trailing=pt_tracker
    )

    # 4) Prime only the zone limits (no order placement here)
    initial_signal = sp.fetch_signal()
    if initial_signal:
        logger.info("Priming zone limits from initial signal")
        raw_supply_min = initial_signal.get("supply_zone", {}).get("min")
        raw_demand_max = initial_signal.get("demand_zone", {}).get("max")
        try:
            pt_tracker.set_zone_limits(
                supply_max=float(raw_supply_min) if raw_supply_min else None,
                demand_max=float(raw_demand_max) if raw_demand_max else None
            )
        except Exception as e:
            logger.warning("Failed to prime zones: %s", e)
        # Mark as seen so the loop won’t re-place it
        sp.last_signal = initial_signal

    # 5) Start the signal‐processing thread
    sp_thread = threading.Thread(
        target=sp.process_signals_loop,
        kwargs={'sleep_interval': getattr(config, 'SIGNAL_POLL_INTERVAL', 5)},
        daemon=True
    )
    sp_thread.start()

    # 6) Start the profit‐trailing thread
    pt_thread = threading.Thread(
        target=pt_tracker.track,
        daemon=True
    )
    pt_thread.start()

    # 7) Handle graceful shutdown
    stop_event = threading.Event()

    def shutdown(signum, frame):
        logger.info("Shutdown signal received, stopping application...")
        ws.stop()
        stop_event.set()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # 8) Wait here until shutdown() sets the event
    stop_event.wait()
    logger.info("Application shutdown complete.")

if __name__ == '__main__':
    main()
