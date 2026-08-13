import sys
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from alpaca.common.exceptions import APIError
from alpaca.data.enums import DataFeed, OptionsFeed
from alpaca.data.historical import StockHistoricalDataClient, OptionHistoricalDataClient
from alpaca.data.requests import (
	StockLatestTradeRequest,
	StockBarsRequest,
	OptionLatestTradeRequest,
	OptionBarsRequest,
)
from alpaca.data.timeframe import TimeFrame

from roles.credentials import bootstrap_trading_auth


OPTION_SYMBOL_PATTERN = re.compile(r"^[A-Z]{1,6}\d{6}[CP]\d{8}$")
ET = ZoneInfo("America/New_York")


def _is_option_symbol(symbol: str) -> bool:
	return bool(OPTION_SYMBOL_PATTERN.match(symbol))


def _extract_symbol_bars(bars, symbol: str):
	data = getattr(bars, "data", None)
	if data is None and isinstance(bars, dict):
		data = bars.get("data")

	if isinstance(data, dict):
		symbol_bars = data.get(symbol) or data.get(symbol.upper())
		if symbol_bars is None:
			symbol_bars = next(iter(data.values()), None)
		return list(symbol_bars) if symbol_bars is not None else []

	return list(data) if data is not None else []


def _format_high_low_from_bars(bars_list) -> tuple[str, str]:
	if not bars_list:
		return "N/A", "N/A"

	latest_bar = bars_list[-1]
	bar_high = getattr(latest_bar, "high", None)
	bar_low = getattr(latest_bar, "low", None)
	if bar_high is None and isinstance(latest_bar, dict):
		bar_high = latest_bar.get("high")
	if bar_low is None and isinstance(latest_bar, dict):
		bar_low = latest_bar.get("low")

	high = f"${float(bar_high):.2f}" if bar_high is not None else "N/A"
	low = f"${float(bar_low):.2f}" if bar_low is not None else "N/A"
	return high, low


def _aggregate_high_low_from_bars(bars_list) -> tuple[str, str]:
	if not bars_list:
		return "N/A", "N/A"

	high_val = None
	low_val = None
	for bar in bars_list:
		bar_high = getattr(bar, "high", None)
		bar_low = getattr(bar, "low", None)
		if bar_high is None and isinstance(bar, dict):
			bar_high = bar.get("high")
		if bar_low is None and isinstance(bar, dict):
			bar_low = bar.get("low")

		if bar_high is not None:
			bar_high = float(bar_high)
			high_val = bar_high if high_val is None else max(high_val, bar_high)
		if bar_low is not None:
			bar_low = float(bar_low)
			low_val = bar_low if low_val is None else min(low_val, bar_low)

	high = f"${high_val:.2f}" if high_val is not None else "N/A"
	low = f"${low_val:.2f}" if low_val is not None else "N/A"
	return high, low


def _get_stock_price_line(ticker: str, api_key: str, secret_key: str) -> str:
	client = StockHistoricalDataClient(api_key, secret_key)

	request_params = StockLatestTradeRequest(symbol_or_symbols=ticker, feed=DataFeed.IEX)
	latest_trade = client.get_stock_latest_trade(request_params)
	trade = latest_trade[ticker]

	now_utc = datetime.now(timezone.utc)
	now_et = now_utc.astimezone(ET)
	start_of_day_et = datetime(now_et.year, now_et.month, now_et.day, tzinfo=ET)
	bars_request = StockBarsRequest(
		symbol_or_symbols=ticker,
		timeframe=TimeFrame.Minute,
		start=start_of_day_et.astimezone(timezone.utc),
		end=now_utc,
		limit=10000,
		feed=DataFeed.IEX,
	)
	bars = client.get_stock_bars(bars_request)
	bar_list = _extract_symbol_bars(bars, ticker)

	high, low = _aggregate_high_low_from_bars(bar_list)

	return f"{ticker} | Current: ${float(trade.price):.2f} | High: {high} | Low: {low}"


def _get_option_price_line(ticker: str, api_key: str, secret_key: str) -> str:
	client = OptionHistoricalDataClient(api_key, secret_key)

	request_params = OptionLatestTradeRequest(
		symbol_or_symbols=ticker,
		feed=OptionsFeed.INDICATIVE,
	)
	latest_trade = client.get_option_latest_trade(request_params)
	trade = latest_trade[ticker]

	high = "N/A"
	low = "N/A"
	bars_status = None
	try:
		now_utc = datetime.now(timezone.utc)
		now_et = now_utc.astimezone(ET)
		start_of_day_et = datetime(now_et.year, now_et.month, now_et.day, tzinfo=ET)

		# Prefer today's intraday range for options so High/Low matches current session.
		minute_bars_request = OptionBarsRequest(
			symbol_or_symbols=ticker,
			timeframe=TimeFrame.Minute,
			start=start_of_day_et.astimezone(timezone.utc),
			end=now_utc,
			limit=10000,
			feed=OptionsFeed.INDICATIVE,
		)
		minute_bars = client.get_option_bars(minute_bars_request)
		minute_bar_list = _extract_symbol_bars(minute_bars, ticker)
		high, low = _aggregate_high_low_from_bars(minute_bar_list)

		# Fallback to most recent daily bar when minute bars are unavailable.
		if high == "N/A" or low == "N/A":
			daily_bars_request = OptionBarsRequest(
				symbol_or_symbols=ticker,
				timeframe=TimeFrame.Day,
				start=now_utc - timedelta(days=7),
				end=now_utc,
				limit=1,
				feed=OptionsFeed.INDICATIVE,
			)
			daily_bars = client.get_option_bars(daily_bars_request)
			daily_bar_list = _extract_symbol_bars(daily_bars, ticker)
			high, low = _format_high_low_from_bars(daily_bar_list)
	except APIError as exc:
		# Alpaca option bars require OPRA entitlement even when latest indicative trade is available.
		exc_text = str(exc)
		if "OPRA agreement is not signed" in exc_text:
			bars_status = "OPRA agreement not signed"
		else:
			bars_status = "bars unavailable"
	except Exception:
		bars_status = "bars unavailable"

	if (high == "N/A" or low == "N/A") and bars_status:
		high = f"Unavailable ({bars_status})"
		low = f"Unavailable ({bars_status})"

	return f"{ticker} | Current: ${float(trade.price):.2f} | High: {high} | Low: {low}"

def main() -> int:
	ticker = sys.argv[1].upper() if len(sys.argv) > 1 else "AAPL"

	try:
		credentials, _ = bootstrap_trading_auth("stock_price.py")
	except Exception as exc:
		print(f"Authentication failed: {exc}")
		return 1

	try:
		if _is_option_symbol(ticker):
			line = _get_option_price_line(ticker, credentials.api_key, credentials.secret_key)
		else:
			line = _get_stock_price_line(ticker, credentials.api_key, credentials.secret_key)
	except Exception as exc:
		print(f"Error fetching price for {ticker}: {exc}")
		return 1

	print(line)
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
