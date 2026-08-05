import sys
from datetime import datetime, timedelta, timezone

from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestTradeRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame

from roles.credentials import bootstrap_trading_auth

def main() -> int:
	ticker = sys.argv[1].upper() if len(sys.argv) > 1 else "AAPL"

	try:
		credentials, _ = bootstrap_trading_auth("stock_price.py")
	except Exception as exc:
		print(f"Authentication failed: {exc}")
		return 1

	client = StockHistoricalDataClient(credentials.api_key, credentials.secret_key)
	request_params = StockLatestTradeRequest(symbol_or_symbols=ticker, feed=DataFeed.IEX)
	latest_trade = client.get_stock_latest_trade(request_params)
	trade = latest_trade[ticker]

	now = datetime.now(timezone.utc)
	bars_request = StockBarsRequest(
		symbol_or_symbols=ticker,
		timeframe=TimeFrame.Day,
		start=now - timedelta(days=7),
		end=now,
		limit=1,
		feed=DataFeed.IEX,
	)
	bars = client.get_stock_bars(bars_request)
	data = getattr(bars, "data", None)
	if data is None and isinstance(bars, dict):
		data = bars.get("data")

	symbol_bars = None
	if isinstance(data, dict):
		symbol_bars = data.get(ticker) or data.get(ticker.upper())
		if symbol_bars is None:
			symbol_bars = next(iter(data.values()), None)
	elif data is not None:
		symbol_bars = data

	high = "N/A"
	low = "N/A"
	if symbol_bars:
		bar_list = list(symbol_bars)
		if bar_list:
			latest_bar = bar_list[-1]
			bar_high = getattr(latest_bar, "high", None)
			bar_low = getattr(latest_bar, "low", None)
			if bar_high is None and isinstance(latest_bar, dict):
				bar_high = latest_bar.get("high")
			if bar_low is None and isinstance(latest_bar, dict):
				bar_low = latest_bar.get("low")
			high = f"${float(bar_high):.2f}" if bar_high is not None else "N/A"
			low = f"${float(bar_low):.2f}" if bar_low is not None else "N/A"

	print(f"{ticker} | Current: ${float(trade.price):.2f} | High: {high} | Low: {low}")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
