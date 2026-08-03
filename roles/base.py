"""Base role class for all trading roles."""

from alpaca.data.historical import StockHistoricalDataClient, CryptoHistoricalDataClient

from .credentials import bootstrap_trading_auth


class BaseRole:
    """Base class for all trading roles"""
    
    def __init__(self, symbol):
        self.symbol = symbol
        self.is_crypto = "/" in symbol  # BTC/USD has slash, AAPL doesn't
        credentials, self.trading_client = bootstrap_trading_auth(self.__class__.__name__)
        self.credentials = {
            "API_KEY": credentials.api_key,
            "API_SECRET": credentials.secret_key,
            "PAPER": credentials.paper,
        }
        
        if self.is_crypto:
            self.data_client = CryptoHistoricalDataClient(
                self.credentials["API_KEY"],
                self.credentials["API_SECRET"]
            )
        else:
            self.data_client = StockHistoricalDataClient(
                self.credentials["API_KEY"],
                self.credentials["API_SECRET"]
            )
