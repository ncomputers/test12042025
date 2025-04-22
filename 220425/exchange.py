import time
import ccxt
import config
import logging
from decimal import Decimal, ROUND_DOWN
from ccxt.base.errors import ExchangeError

logger = logging.getLogger(__name__)

def get_active_account(account: str = "MAIN") -> dict:
    """
    Returns the account configuration for the given account if it is active.
    Raises a ValueError if the account is not active or not defined.
    """
    account_config = config.ACCOUNTS.get(account)
    if account_config and account_config.get("active", True):
        return account_config
    raise ValueError(f"Account '{account}' is not active or not configured.")

class DeltaExchangeClient:
    def __init__(self, account: str = "MAIN"):
        """
        Initializes the DeltaExchangeClient with account-specific API credentials.
        """
        self.account = account
        account_config = get_active_account(account)
        api_key = account_config["API_KEY"]
        api_secret = account_config["API_SECRET"]

        # Support both dict and dataclass-like access for DELTA_API_URLS.
        if isinstance(config.DELTA_API_URLS, dict):
            public_url = config.DELTA_API_URLS.get('public')
            private_url = config.DELTA_API_URLS.get('private')
        else:
            public_url = getattr(config.DELTA_API_URLS, 'public', None)
            private_url = getattr(config.DELTA_API_URLS, 'private', None)

        try:
            self.exchange = ccxt.delta({
                'apiKey': api_key,
                'secret': api_secret,
                'urls': {
                    'api': {
                        'public': public_url,
                        'private': private_url,
                    }
                },
                'enableRateLimit': True,
                'adjustForTimeDifference': True,
            })
            logger.debug("DeltaExchangeClient initialized successfully for account %s.", account)
        except Exception as e:
            logger.error("Error initializing DeltaExchangeClient for account %s: %s", account, e)
            raise

        self._market_cache = None
        self._market_cache_time = 0

    def load_markets(self, reload: bool = False) -> dict:
        """
        Loads markets from the exchange. Uses cached markets if fresh.
        """
        current_time = time.time()
        if not reload and self._market_cache and (current_time - self._market_cache_time < config.MARKET_CACHE_TTL):
            logger.debug("Returning cached market data.")
            return self._market_cache
        try:
            markets = self.exchange.load_markets(reload)
            self._market_cache = markets
            self._market_cache_time = current_time
            logger.debug("Markets loaded: %s", list(markets.keys()))
            return markets
        except Exception as e:
            logger.error("Error loading markets: %s", e)
            raise

    def get_tick_size(self, symbol: str) -> Decimal:
        """
        Fetches the tick_size for a given market symbol, with fallback
        to matching product_symbol or info.symbol fields.
        """
        markets = self.load_markets()
        market = markets.get(symbol)
        if not market:
            # fallback: scan for matching info.symbol or info.product_symbol
            for m in markets.values():
                info = m.get('info', {})
                if info.get('symbol') == symbol or info.get('product_symbol') == symbol:
                    market = m
                    break
        if not market:
            raise ExchangeError(f"Market metadata for {symbol} not found.")
        info = market.get('info', {})
        tick = info.get('tick_size') or \
               (market.get('precision', {}) or {}).get('price')
        if tick is None:
            raise ExchangeError(f"Tick size for {symbol} not found in market metadata.")
        return Decimal(str(tick))

    def quantize_price(self, price: float, symbol: str) -> float:
        """
        Quantizes a raw price to the market's tick size (rounding down).
        """
        tick = self.get_tick_size(symbol)
        price_dec = Decimal(str(price))
        quantized = (price_dec // tick) * tick
        return float(quantized)

    def fetch_balance(self) -> dict:
        """
        Fetches and returns the account balance from the exchange.
        """
        try:
            balance = self.exchange.fetch_balance()
            logger.debug("Balance fetched: %s", balance)
            return balance
        except Exception as e:
            logger.error("Error fetching balance: %s", e)
            raise

    def create_limit_order(self, symbol: str, side: str, amount: float, price: float, params: dict = None) -> dict:
        """
        Creates a tick-aligned limit order for the given symbol.
        """
        params = params or {}
        try:
            q_price = self.quantize_price(price, symbol)
            order = self.exchange.create_order(symbol, 'limit', side, amount, q_price, params)
            logger.debug("Limit order created: %s", order)
            return order
        except Exception as e:
            logger.error("Error creating limit order for %s: %s", symbol, e)
            raise

    def cancel_order(self, order_id: any, symbol: str, params: dict = None) -> dict:
        """
        Cancels an order for the specified symbol and order ID.
        """
        try:
            result = self.exchange.cancel_order(order_id, symbol, params or {})
            logger.debug("Order canceled: %s", result)
            return result
        except Exception as e:
            logger.error("Error canceling order %s for %s: %s", order_id, symbol, e)
            raise

    def create_order(self, symbol: str, order_type: str, side: str, amount: float, price: float = None, params: dict = None) -> dict:
        """
        Creates an order of the specified type; if a price is provided, it is quantized.
        """
        params = params or {}
        try:
            if order_type == 'limit' and price is not None:
                price = self.quantize_price(price, symbol)
            order = self.exchange.create_order(symbol, order_type, side, amount, price, params)
            logger.debug("%s order created: %s", order_type.capitalize(), order)
            return order
        except Exception as e:
            logger.error("Error creating %s order for %s: %s", order_type, symbol, e)
            raise

    def modify_bracket_order(self, order_id: any, product_id: any, product_symbol: str, bracket_params: dict) -> dict:
        """
        Modifies a bracket order by quantizing SL/TP prices to tick size.
        """
        # Build request body
        request_body = {
            "id": order_id,
            "product_id": product_id,
            "product_symbol": product_symbol
        }
        request_body.update(bracket_params)

        # Quantize any bracket price fields
        for key in (
            "bracket_stop_loss_limit_price",
            "bracket_stop_loss_price",
            "bracket_take_profit_limit_price",
            "bracket_take_profit_price"
        ):
            if key in request_body:
                raw = float(request_body[key])
                request_body[key] = str(self.quantize_price(raw, product_symbol))

        try:
            if hasattr(self.exchange, 'privatePutOrdersBracket'):
                order = self.exchange.privatePutOrdersBracket(request_body)
            else:
                order = self.exchange.request('orders/bracket', 'PUT', request_body)
            logger.debug("Modified bracket order on exchange: %s", order)
            return order
        except Exception as e:
            logger.error("Error modifying bracket order %s: %s", order_id, e)
            raise

    def fetch_positions(self) -> list:
        """
        Fetches the current open positions from the exchange.
        """
        try:
            if hasattr(self.exchange, 'fetch_positions'):
                positions = self.exchange.fetch_positions()
                logger.debug("Positions fetched using fetch_positions: %s", positions)
                return positions
            else:
                positions = self.exchange.request('positions', 'GET', {})
                logger.debug("Positions fetched using direct request: %s", positions)
                return positions
        except Exception as e:
            logger.error("Error fetching positions: %s", e)
            raise


if __name__ == '__main__':
    # Testing each account.
    for account in ["MAIN"]:
        print(f"--- Testing account: {account} ---")
        try:
            client = DeltaExchangeClient(account=account)
            #try:
                #markets = client.load_markets()
                #print("Markets loaded successfully:", list(markets.keys()))
            #except Exception as e:
                #print("Error loading markets:", e)
            #try:
                #balance = client.fetch_balance()
                #print("Fetched balance:", balance)
            #except Exception as e:
                #print("Error fetching balance:", e)
            try:
                positions = client.fetch_positions()
                print("Fetched positions:", positions)
            except Exception as e:
                print("Error fetching positions:", e)
        except Exception as e:
            print("Error initializing account", account, ":", e)
