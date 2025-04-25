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
    setup_logging()
    logger = logging.getLogger(__name__)
    logger.info("Starting main application for %s", config.SYMBOL)

    ws = BinanceWebsocket()
    ws.start()

    pt_tracker = ProfitTrailing(
        ws_instance=ws,
        check_interval=getattr(config, 'PROFIT_CHECK_INTERVAL', 1)
    )
    sp = SignalProcessor(
        ws_instance=ws,
        profit_trailing=pt_tracker
    )

    # 1) Fetch the very first signal
    initial_signal = sp.fetch_signal()
    if initial_signal:
        logger.info("Priming zone limits from initial signal")
        raw_supply = initial_signal.get("supply_zone", {}).get("min")
        raw_demand = initial_signal.get("demand_zone", {}).get("max")
        try:
            pt_tracker.set_zone_limits(
                supply_max=float(raw_supply) if raw_supply else None,
                demand_max=float(raw_demand) if raw_demand else None
            )
        except Exception as e:
            logger.warning("Failed to prime zones: %s", e)

        # 2) Immediately call process_signal to force-close any opposite position
        #    because process_signal always runs the "close opposite" step first.
        sp.process_signal(initial_signal)

        # 3) Mark it as seen so the loop won’t redo it
        sp.last_signal = initial_signal

    # Now start the ongoing loops:

    sp_thread = threading.Thread(
        target=sp.process_signals_loop,
        kwargs={'sleep_interval': getattr(config, 'SIGNAL_POLL_INTERVAL', 5)},
        daemon=True
    )
    sp_thread.start()

    pt_thread = threading.Thread(
        target=pt_tracker.track,
        daemon=True
    )
    pt_thread.start()

    stop_event = threading.Event()
    def shutdown(signum, frame):
        logger.info("Shutdown signal received, stopping…")
        ws.stop()
        stop_event.set()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    stop_event.wait()
    logger.info("Application stopped.")

if __name__ == '__main__':
    main()
