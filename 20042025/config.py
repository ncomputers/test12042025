import os
from dotenv import load_dotenv
from dataclasses import dataclass

# Load environment variables from .env file if present.
load_dotenv()


ORDER_INFO_KEY = os.getenv("ORDER_INFO_KEY", "order_info")

# ---- Trading defaults ----
TRADING_SYMBOL = os.getenv("TRADING_SYMBOL", "BTCUSD")
TRADING_QUANTITY = float(os.getenv("TRADING_QUANTITY", "1"))

# ---- Offset percentages ----
ORDER_ENTRY_OFFSET_PERCENT = float(os.getenv("ORDER_ENTRY_OFFSET_PERCENT", "0.06"))
ORDER_SL_OFFSET_PERCENT = float(os.getenv("ORDER_SL_OFFSET_PERCENT", "0.60"))
ORDER_TP_OFFSET_PERCENT = float(os.getenv("ORDER_TP_OFFSET_PERCENT", "3.61"))
FIXED_STOP_OFFSET_PERCENT = float(os.getenv("FIXED_STOP_OFFSET_PERCENT", "0.60"))

@dataclass(frozen=True)
class NgrokConfig:
    domain: str = os.getenv("NGROK_DOMAIN", "https://octopus-absolute-frequently.ngrok-free.app")

@dataclass(frozen=True)
class DeltaApiUrls:
    public: str = os.getenv("DELTA_PUBLIC_URL", "https://api.india.delta.exchange")
    private: str = os.getenv("DELTA_PRIVATE_URL", "https://api.india.delta.exchange")

@dataclass(frozen=True)
class TradingParameters:
    default_order_type: str = 'limit'
    trailing_stop_percent: float = 2.0  # 2% trailing stop
    basket_order_enabled: bool = True

@dataclass(frozen=True)
class LoggingConfig:
    log_file: str = os.getenv('LOG_FILE', 'trading.log')
    log_level: str = os.getenv('LOG_LEVEL', 'DEBUG')

@dataclass(frozen=True)
class RedisConfig:
    host: str = os.getenv('REDIS_HOST', 'localhost')
    port: int = int(os.getenv('REDIS_PORT', '6379'))
    db: int = int(os.getenv('REDIS_DB', '0'))

@dataclass(frozen=True)
class MarketDataConfig:
    cache_ttl: int = int(os.getenv('MARKET_CACHE_TTL', '300'))

@dataclass(frozen=True)
class DatabaseConfig:
    uri: str = os.getenv('DATABASE_URI', 'sqlite:///trading.db')

@dataclass(frozen=True)
class ProfitTrailingConfig:
    start_trailing_profit_pct: float = 0.005  # trailing starts at 0.5% profit
    levels: list = (
        {"min_profit_pct": 0.005, "trailing_stop_offset": 0.001, "book_fraction": 1.0},
        {"min_profit_pct": 0.01,  "trailing_stop_offset": 0.006, "book_fraction": 1.0},
        {"min_profit_pct": 0.015, "trailing_stop_offset": 0.012, "book_fraction": 1.0},
        {"min_profit_pct": 0.02,  "trailing_stop_offset": None, "book_fraction": 0.9}
    )
    fixed_stop_loss_pct: float = 0.005
    trailing_unit: str = "percent"

# Multi-account configuration
_ACCOUNTS = {
    "MAIN": {
        "active": os.getenv("MAIN_ACTIVE", "True").lower() == "true",
        "API_KEY": os.getenv("DELTA_API_KEY", "sUABSFPLpe5QNVJuKsOL6O0r5TiUoP"),
        "API_SECRET": os.getenv("DELTA_API_SECRET", "Q6Fo1NcOtNIxJZ9IPRUxROcSZ4vQdI31hDVPaoOvJnYfPt5wQLaNb6WMnNOy"),
        "REDIS_KEY": os.getenv("REDIS_KEY_MAIN", "signal_MAIN")
    },
    "V1": {
        "active": os.getenv("V1_ACTIVE", "False").lower() == "true",
        "API_KEY": os.getenv("DELTA_API_KEY_V1", "woi6K2SqYM4pxucKKSyiWHC4otjhCG"),
        "API_SECRET": os.getenv("DELTA_API_SECRET_V1", "SbQPy3H8WArxN5SWguou3hgp9y1preJRgWkaEjTcwEgADLLqe55UlGBhBWS1"),
        "REDIS_KEY": os.getenv("REDIS_KEY_V1", "signal_V1")
    },
    "V2": {
        "active": os.getenv("V2_ACTIVE", "False").lower() == "true",
        "API_KEY": os.getenv("DELTA_API_KEY_V2", "fGQsRZrluE94QnGNQen8z90pPW1I5s"),
        "API_SECRET": os.getenv("DELTA_API_SECRET_V2", "FXdGYpi7uUqez7NslLUao4QsIiTWIdcrnjv2AHrJbCZt4WBpgBc5EDXKC01w"),
        "REDIS_KEY": os.getenv("REDIS_KEY_V2", "signal_V2")
    }
}

# Global aliases and legacy names
ACCOUNTS = _ACCOUNTS
NGROK_DOMAIN = NgrokConfig().domain
DELTA_API_URLS = DeltaApiUrls()
FIXED_OFFSET = int(os.getenv('FIXED_OFFSET', '100'))
MISSING_PRICE_OFFSET = int(os.getenv('MISSING_PRICE_OFFSET', '100'))
DEFAULT_ORDER_TYPE = TradingParameters().default_order_type
TRAILING_STOP_PERCENT = TradingParameters().trailing_stop_percent
BASKET_ORDER_ENABLED = TradingParameters().basket_order_enabled
LOG_FILE = LoggingConfig().log_file
LOG_LEVEL = LoggingConfig().log_level
REDIS_HOST = RedisConfig().host
REDIS_PORT = RedisConfig().port
REDIS_DB = RedisConfig().db
MARKET_CACHE_TTL = MarketDataConfig().cache_ttl
DATABASE_URI = DatabaseConfig().uri
PROFIT_TRAILING_CONFIG = ProfitTrailingConfig().__dict__

# Expose trading defaults
SYMBOL = TRADING_SYMBOL
QUANTITY = TRADING_QUANTITY

# Redis key for fetching signals
REDIS_KEY = ACCOUNTS["MAIN"]["REDIS_KEY"]
