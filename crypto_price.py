import sys
from alpaca.data.historical import CryptoHistoricalDataClient
from alpaca.data.requests import CryptoLatestTradeRequest

from roles.credentials import bootstrap_trading_auth

def main() -> int:
	symbol = sys.argv[1].upper() if len(sys.argv) > 1 else "BTC/USD"

	try:
		credentials, _ = bootstrap_trading_auth("crypto_price.py")
	except Exception as exc:
		print(f"Authentication failed: {exc}")
		return 1

	client = CryptoHistoricalDataClient(credentials.api_key, credentials.secret_key)
	request_params = CryptoLatestTradeRequest(symbol_or_symbols=symbol)
	latest_trade = client.get_crypto_latest_trade(request_params)
	trade = latest_trade[symbol]
	print(f"Latest price for {symbol}: ${trade.price}")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
