import logging
import config

def setup_logging() -> logging.Logger:
    """
    Configures the root logger using settings from config.
    Returns the configured logger.
    """
    # Resolve log level from string to numeric level (default to INFO if not recognized)
    log_level = getattr(logging, config.LOG_LEVEL.upper(), logging.INFO)
    
    logger = logging.getLogger()
    logger.setLevel(log_level)

    # Clear any pre-existing handlers.
    if logger.hasHandlers():
        logger.handlers.clear()

    # File handler for detailed logging.
    file_handler = logging.FileHandler(config.LOG_FILE)
    file_handler.setLevel(log_level)
    file_formatter = logging.Formatter('%(asctime)s %(levelname)s: %(message)s')
    file_handler.setFormatter(file_formatter)
    
    # Console handler for less detailed output.
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_formatter = logging.Formatter('%(asctime)s %(levelname)s: %(message)s')
    console_handler.setFormatter(console_formatter)
    
    # Add both handlers to the logger.
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger

if __name__ == "__main__":
    logger = setup_logging()
    logger.info("Logging has been configured successfully.")
