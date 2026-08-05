import json
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

from alpaca.common.exceptions import APIError
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestTradeRequest
from alpaca.data.timeframe import TimeFrame

from roles.credentials import CredentialsRole, bootstrap_trading_auth


def _parse_symbols(argv: list[str]) -> list[str]:
    if len(argv) != 2:
        raise ValueError("Usage: python check_alpaca_price.py SYMBOL[,SYMBOL...]")
    symbols = [symbol.strip().upper() for symbol in argv[1].split(",") if symbol.strip()]
    if not symbols:
        raise ValueError("At least one ticker symbol is required")
    return symbols


def _build_data_client(credentials: CredentialsRole, url_override: str, sandbox: bool = False) -> StockHistoricalDataClient:
    # Use the supplied Alpaca market-data endpoint explicitly.
    return StockHistoricalDataClient(
        api_key=credentials.api_key,
        secret_key=credentials.secret_key,
        sandbox=sandbox,
        url_override=url_override,
    )


def _extract_api_error_message(exc: APIError) -> str:
    text = str(exc)
    try:
        payload = json.loads(text)
        return payload.get("message", text)
    except json.JSONDecodeError:
        return text


def _get_latest_trade_prices(data_client: StockHistoricalDataClient, symbols: list[str]) -> dict[str, float | None]:
    request = StockLatestTradeRequest(symbol_or_symbols=symbols)
    response = data_client.get_stock_latest_trade(request)
    prices: dict[str, float | None] = {}

    for symbol in symbols:
        item = None
        if isinstance(response, dict):
            item = response.get(symbol) or response.get(symbol.upper())
        else:
            item = getattr(response, symbol, None)

        if item is None:
            prices[symbol] = None
            continue

        if isinstance(item, dict):
            price = item.get("price") or item.get("trade", {}).get("price")
        else:
            price = getattr(item, "price", None)

        prices[symbol] = float(price) if price is not None else None

    return prices


def _get_previous_close_prices(data_client: StockHistoricalDataClient, symbols: list[str]) -> dict[str, float | None]:
    now = datetime.now(timezone.utc)
    request = StockBarsRequest(
        symbol_or_symbols=symbols,
        timeframe=TimeFrame.Day,
        start=now - timedelta(days=10),
        end=now,
        limit=3,
    )
    bars = data_client.get_stock_bars(request)
    data = getattr(bars, "data", None)
    if data is None and isinstance(bars, dict):
        data = bars.get("data")

    results: dict[str, float | None] = {}
    for symbol in symbols:
        symbol_data = None
        if isinstance(data, dict):
            symbol_data = data.get(symbol) or data.get(symbol.upper())
            if symbol_data is None:
                symbol_data = next(iter(data.values()), None)
        elif data is not None:
            symbol_data = data

        if symbol_data is None:
            results[symbol] = None
            continue

        bar_list = list(symbol_data)
        if not bar_list:
            results[symbol] = None
            continue

        previous_bar = bar_list[1] if len(bar_list) > 1 else bar_list[0]
        previous_close = getattr(previous_bar, "close", None)
        if previous_close is None and isinstance(previous_bar, dict):
            previous_close = previous_bar.get("close")

        results[symbol] = float(previous_close) if previous_close is not None else None

    return results


def main() -> int:
    try:
        symbols = _parse_symbols(sys.argv)
    except ValueError as exc:
        print(exc)
        return 1

    try:
        credentials, _ = bootstrap_trading_auth("check_alpaca_price.py")
    except Exception as exc:
        print(f"Authentication failed: {exc}")
        return 1

    live_url = "https://data.alpaca.markets"
    sandbox_url = "https://data.sandbox.alpaca.markets"
    client = _build_data_client(credentials, live_url)
    base_url_value = getattr(client._base_url, "value", client._base_url)

    endpoint_url = f"{base_url_value}/v2/stocks/trades/latest"
    print(f"Using Alpaca market-data endpoint: {endpoint_url}")
    print("SYMBOL,CURRENT_PRICE,PREVIOUS_CLOSE")

    try:
        latest_prices = _get_latest_trade_prices(client, symbols)
        previous_closes = _get_previous_close_prices(client, symbols)
    except APIError as exc:
        error_msg = _extract_api_error_message(exc)
        if "subscription does not permit querying recent SIP data" in error_msg.lower():
            print("Live Alpaca data subscription unavailable. Falling back to sandbox market-data endpoint.")
            client = _build_data_client(credentials, sandbox_url, sandbox=True)
            fallback_url_value = getattr(client._base_url, "value", client._base_url)
            print(f"Using fallback endpoint: {fallback_url_value}/v2/stocks/trades/latest")
            try:
                latest_prices = _get_latest_trade_prices(client, symbols)
                previous_closes = _get_previous_close_prices(client, symbols)
            except Exception as exc2:
                print(f"Error after sandbox fallback: {exc2}")
                return 1
        else:
            print(f"Error: {error_msg}")
            return 1
    except Exception as exc:
        print(f"Error: {exc}")
        return 1

    for symbol in symbols:
        current_price = latest_prices.get(symbol)
        previous_close = previous_closes.get(symbol)
        print(
            f"{symbol},"
            f"{current_price if current_price is not None else 'N/A'},"
            f"{previous_close if previous_close is not None else 'N/A'}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
