import sys
from alpaca.data.historical import CryptoHistoricalDataClient
from alpaca.data.requests import CryptoLatestTradeRequest

from pathlib import Path
import sys

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from roles.credentials import bootstrap_trading_auth

def main() -> int:
	# Handle help flag
	if len(sys.argv) > 1 and sys.argv[1] in ["--help", "-h", "help"]:
		print("""
CRYPTO_PRICE.PY - Get Real-Time Cryptocurrency Prices

SYNTAX:
  python indicator/crypto_price.py [CRYPTO_SYMBOL] [--help]

OPTIONAL:
  CRYPTO_SYMBOL   Cryptocurrency pair (default: BTC/USD)
                  Format: SYMBOL/USD or SYMBOL/USDT
                  Examples: BTC/USD, ETH/USD, SOL/USD
  --help, -h      Show this help message

DESCRIPTION:
  Fetches latest real-time prices for cryptocurrencies
  Supports major crypto pairs against USD
  Shows live market prices from Alpaca crypto data feed
  Useful for monitoring crypto holdings or price alerts

SUPPORTED CRYPTOCURRENCIES:
  - BTC/USD       Bitcoin
  - ETH/USD       Ethereum
  - SOL/USD       Solana
  - DOGE/USD      Dogecoin
  - XRP/USD       Ripple
  - ADA/USD       Cardano
  - And all other crypto pairs available via Alpaca

EXAMPLES:
  python indicator/crypto_price.py
    - Default: show BTC/USD (Bitcoin price)

  python indicator/crypto_price.py BTC/USD
    - Explicit Bitcoin price

  python indicator/crypto_price.py ETH/USD
    - Ethereum price

  python indicator/crypto_price.py SOL/USD
    - Solana price

  python indicator/crypto_price.py BTC/USD --help
    - Show help (flag position doesn't matter)

OUTPUT:
  - Cryptocurrency symbol (e.g., BTC/USD)
  - Latest trade price (formatted as $XXXX.XX)
  - Real-time market data

NOTES:
  - Requires valid Alpaca API credentials with crypto trading approval
  - Cryptocurrency pairs must use /USD format
  - Prices are updated real-time during market hours
  - Data comes directly from Alpaca crypto data feed
  - 24/7 cryptocurrency markets (unlike stock markets)
  - Use for live crypto trading strategy automation
""")
		return 0
	
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

