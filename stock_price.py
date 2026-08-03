import sys
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestTradeRequest

from roles.credentials import bootstrap_trading_auth

def main() -> int:
	ticker = sys.argv[1].upper() if len(sys.argv) > 1 else "AAPL"

	try:
		credentials, _ = bootstrap_trading_auth("stock_price.py")
	except Exception as exc:
		print(f"Authentication failed: {exc}")
		return 1

	client = StockHistoricalDataClient(credentials.api_key, credentials.secret_key)
	request_params = StockLatestTradeRequest(symbol_or_symbols=ticker)
	latest_trade = client.get_stock_latest_trade(request_params)
	trade = latest_trade[ticker]
	print(f"Latest price for {ticker}: ${trade.price}")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
