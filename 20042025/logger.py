import logging
import os
import config

# Ensure the log directory exists
log_dir = os.path.dirname(config.LOG_FILE)
if log_dir and not os.path.exists(log_dir):
    os.makedirs(log_dir)

def setup_logging() -> logging.Logger:
    """
    Configures the root logger using settings from config.
    On startup, truncates the log file (deletes older logs) by opening with mode='w'.
    """
    # Resolve log level
    log_level = getattr(logging, config.LOG_LEVEL.upper(), logging.INFO)

    logger = logging.getLogger()
    logger.setLevel(log_level)

    # Clear existing handlers
    if logger.hasHandlers():
        logger.handlers.clear()

    # File handler in write mode to truncate on restart
    file_handler = logging.FileHandler(config.LOG_FILE, mode='w')
    file_handler.setLevel(log_level)
    file_formatter = logging.Formatter('%(asctime)s %(levelname)s: %(message)s')
    file_handler.setFormatter(file_formatter)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_formatter = logging.Formatter('%(asctime)s %(levelname)s: %(message)s')
    console_handler.setFormatter(console_formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger

if __name__ == "__main__":
    logger = setup_logging()
    logger.info("Logging has been configured successfully (log file truncated).")
