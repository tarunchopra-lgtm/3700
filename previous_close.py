from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from alpaca.common.enums import Sort
from alpaca.common.exceptions import APIError
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestTradeRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderClass, OrderSide, OrderType, TimeInForce
from alpaca.trading.requests import MarketOrderRequest, StopLossRequest

import socket

from roles.credentials import CredentialsRole, bootstrap_trading_auth


def _ensure_symbol(symbol: str) -> str:
    sanitized = symbol.strip().upper()
    if not sanitized:
        raise ValueError("Ticker symbol cannot be empty")
    return sanitized


def _build_data_client(credentials: CredentialsRole) -> StockHistoricalDataClient:
    # Force the paper trading base URL for all Alpaca calls, including market data.
    # For now, this uses the same paper endpoint for everything.
    client = StockHistoricalDataClient(
        api_key=credentials.api_key,
        secret_key=credentials.secret_key,
        sandbox=credentials.paper,
        url_override="https://paper-api.alpaca.markets",
    )
    client._session.trust_env = False
    client._session.proxies = {}
    return client


def _manual_market_data(symbol: str) -> tuple[float, float]:
    print("Paper API market-data endpoint not available; using manual fallback.")
    while True:
        value = input(f"Enter current price for {symbol}: ").strip()
        try:
            current_price = float(value)
            break
        except ValueError:
            print("Invalid price. Please enter a numeric value.")
    while True:
        value = input(f"Enter previous close price for {symbol}: ").strip()
        try:
            previous_close = float(value)
            break
        except ValueError:
            print("Invalid price. Please enter a numeric value.")
    return previous_close, current_price


def _fetch_market_data(data_client: StockHistoricalDataClient, symbol: str) -> tuple[float, float]:
    print("  data request: latest trade")
    trade_request = StockLatestTradeRequest(symbol_or_symbols=symbol)
    base_url_value = getattr(data_client._base_url, "value", data_client._base_url)
    trade_url = f"{base_url_value}/v2/stocks/trades/latest"
    print(f"    latest trade URL: {trade_url}")
    print(f"    latest trade api_key prefix: {data_client._api_key[:4] if getattr(data_client, '_api_key', None) else 'None'}")
    print(f"    latest trade request params: {trade_request.to_request_fields()}")
    print(f"    latest trade auth headers: {data_client._get_default_headers()}")
    try:
        trade_response = data_client.get_stock_latest_trade(trade_request)
    except APIError as exc:
        status = getattr(exc, "status_code", None)
        if status == 404 or "Not Found" in str(exc):
            return _manual_market_data(symbol)
        raise
    print(f"    latest trade response type: {type(trade_response)}")

    if isinstance(trade_response, dict):
        current_price = trade_response.get("price") or trade_response.get("trade", {}).get("price")
    else:
        current_price = getattr(trade_response, "price", None)

    if current_price is None:
        raise RuntimeError(f"Unable to read current price for {symbol}")

    now = datetime.now(timezone.utc)
    print("  data request: daily bars")
    bars_request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Day,
        start=now - timedelta(days=10),
        end=now,
        limit=2,
        sort=Sort.DESC,
    )
    print(f"    bars request: {bars_request}")
    try:
        bars = data_client.get_stock_bars(bars_request)
    except APIError as exc:
        status = getattr(exc, "status_code", None)
        if status == 404 or "Not Found" in str(exc):
            return _manual_market_data(symbol)
        raise
    print(f"    bars response type: {type(bars)}")

    data = getattr(bars, "data", None)
    if data is None and isinstance(bars, dict):
        data = bars.get("data")

    if not data:
        raise RuntimeError(f"No bar data returned for {symbol}")

    symbol_bars = data.get(symbol) or data.get(symbol.upper())
    if symbol_bars is None and isinstance(data, dict):
        symbol_bars = next(iter(data.values()), None)

    if symbol_bars is None:
        raise RuntimeError(f"No bars found for {symbol}")

    bar_list = list(symbol_bars)
    if not bar_list:
        raise RuntimeError(f"No bars found for {symbol}")

    previous_bar = bar_list[1] if len(bar_list) > 1 else bar_list[0]
    previous_close = getattr(previous_bar, "close", None)
    if previous_close is None and isinstance(previous_bar, dict):
        previous_close = previous_bar.get("close")

    if previous_close is None:
        raise RuntimeError(f"Unable to read previous close for {symbol}")

    return float(previous_close), float(current_price)


def _resolve_order_side(current_price: float, previous_close: float) -> OrderSide | None:
    if current_price > previous_close:
        return OrderSide.BUY
    if current_price < previous_close:
        return OrderSide.SELL
    return None


def _build_bracket_order(symbol: str, side: OrderSide, stop_price: float) -> MarketOrderRequest:
    return MarketOrderRequest(
        symbol=symbol,
        qty=1,
        side=side,
        type=OrderType.MARKET,
        time_in_force=TimeInForce.CLS,
        order_class=OrderClass.BRACKET,
        stop_loss=StopLossRequest(stop_price=stop_price),
    )


def _print_debug_info(credentials: CredentialsRole, trading_client: TradingClient, data_client: StockHistoricalDataClient) -> None:
    print("Debug info:")
    print(f"  env HTTP_PROXY={os.environ.get('HTTP_PROXY')}")
    print(f"  env HTTPS_PROXY={os.environ.get('HTTPS_PROXY')}")
    print(f"  env http_proxy={os.environ.get('http_proxy')}")
    print(f"  env https_proxy={os.environ.get('https_proxy')}")
    print(f"  env ALL_PROXY={os.environ.get('ALL_PROXY')}")
    print(f"  env all_proxy={os.environ.get('all_proxy')}")
    print(f"  env FTP_PROXY={os.environ.get('FTP_PROXY')}")
    print(f"  env ftp_proxy={os.environ.get('ftp_proxy')}")
    print(f"  api_key prefix={credentials.api_key[:4]} (len={len(credentials.api_key)})")
    print(f"  paper mode={credentials.paper}")
    if hasattr(trading_client, "_base_url"):
        trading_base_url = getattr(trading_client._base_url, "value", trading_client._base_url)
        print(f"  trading base_url={trading_base_url}")
    if hasattr(data_client, "_base_url"):
        data_base_url = getattr(data_client._base_url, "value", data_client._base_url)
        print(f"  data base_url={data_base_url}")
    if hasattr(data_client, "_session"):
        print(f"  data client trust_env={data_client._session.trust_env}")
        print(f"  data client proxies={data_client._session.proxies}")
    if hasattr(trading_client, "_session"):
        print(f"  trading client trust_env={trading_client._session.trust_env}")
        print(f"  trading client proxies={trading_client._session.proxies}")
    if hasattr(trading_client, "_base_url"):
        try:
            trading_host = getattr(trading_client._base_url, "value", trading_client._base_url).split("://", 1)[1]
            trading_host = trading_host.split("/", 1)[0]
            print(f"  resolve trading host: {trading_host}")
            for family, _, _, _, sockaddr in socket.getaddrinfo(trading_host, 443, proto=socket.IPPROTO_TCP):
                print(f"    {family} -> {sockaddr[0]}")
        except Exception as exc:
            print(f"  trading host resolve error: {exc}")
    if hasattr(data_client, "_base_url"):
        try:
            data_host = getattr(data_client._base_url, "value", data_client._base_url).split("://", 1)[1]
            data_host = data_host.split("/", 1)[0]
            print(f"  resolve data host: {data_host}")
            for family, _, _, _, sockaddr in socket.getaddrinfo(data_host, 443, proto=socket.IPPROTO_TCP):
                print(f"    {family} -> {sockaddr[0]}")
        except Exception as exc:
            print(f"  data host resolve error: {exc}")
    if hasattr(trading_client, "get_clock"):
        try:
            clock = trading_client.get_clock()
            print(
                f"  market clock: is_open={getattr(clock, 'is_open', None)}, "
                f"next_open={getattr(clock, 'next_open', None)}, "
                f"next_close={getattr(clock, 'next_close', None)}"
            )
        except Exception as exc:
            print(f"  market clock error: {exc}")
    else:
        print("  trading client does not expose get_clock")


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python previous_close.py <TICKER>")
        return 1

    symbol = _ensure_symbol(sys.argv[1])
    try:
        credentials, trading_client = bootstrap_trading_auth("previous_close.py")
    except Exception as exc:
        print(f"Authentication failed: {exc}")
        return 1

    trading_client._session.trust_env = False

    for proxy_var in [
        "HTTP_PROXY",
        "http_proxy",
        "HTTPS_PROXY",
        "https_proxy",
        "ALL_PROXY",
        "all_proxy",
        "FTP_PROXY",
        "ftp_proxy",
        "NO_PROXY",
        "no_proxy",
    ]:
        os.environ.pop(proxy_var, None)

    data_client = _build_data_client(credentials)
    _print_debug_info(credentials, trading_client, data_client)

    try:
        print("Fetching market data from Alpaca...")
        previous_close, current_price = _fetch_market_data(data_client, symbol)
    except requests.exceptions.HTTPError as exc:
        print("Market data fetch failed due to an HTTP error.")
        print(f"Market data fetch failed: {exc}")
        if hasattr(data_client, '_session'):
            session = getattr(data_client, '_session')
            if hasattr(session, 'headers'):
                print(f"  data client session headers: {session.headers}")
        return 1
    except requests.exceptions.ProxyError as exc:
        print("Market data fetch failed due to a proxy error.")
        print(f"Network proxy issue: {exc}")
        return 1
    except requests.exceptions.ConnectionError as exc:
        print("Market data fetch failed due to a connection error.")
        if any(term in str(exc) for term in ["NameResolutionError", "Failed to resolve", "nodename nor servname"]):
            print("  DNS resolution failed for Alpaca endpoints. Ensure this Python process can resolve data.sandbox.alpaca.markets and paper-api.alpaca.markets.")
        print(f"Network connection issue: {exc}")
        return 1
    except APIError as exc:
        print("Market data fetch failed due to an Alpaca API error.")
        print(f"Alpaca API error: {exc}")
        return 1
    except Exception as exc:
        print("Market data fetch failed due to an unexpected error.")
        print(f"Error: {exc}")
        return 1

    print(f"Symbol: {symbol}")
    print(f"Previous close: {previous_close}")
    print(f"Current price: {current_price}")

    order_side = _resolve_order_side(current_price, previous_close)
    if order_side is None:
        print("Price is unchanged from previous close; no order will be submitted.")
        return 0

    if order_side == OrderSide.BUY:
        stop_price = round(current_price - 10.0, 2)
    else:
        stop_price = round(current_price + 10.0, 2)

    print(f"Placing a market {order_side.value} order for 1 share with stop loss at {stop_price}")
    order_request = _build_bracket_order(symbol, order_side, stop_price)

    try:
        order = trading_client.submit_order(order_request)
    except APIError as exc:
        print("Order submission failed due to an Alpaca API error.")
        print(f"Alpaca API error: {exc}")
        return 1
    except requests.exceptions.ProxyError as exc:
        print("Order submission failed due to a proxy error.")
        print(f"Network proxy issue: {exc}")
        return 1
    except requests.exceptions.ConnectionError as exc:
        print("Order submission failed due to a connection error.")
        print(f"Network connection issue: {exc}")
        return 1
    except Exception as exc:
        print("Order submission failed due to an unexpected error.")
        print(f"Error: {exc}")
        return 1

    print(f"Order submitted: id={getattr(order, 'id', None)} status={getattr(order, 'status', None)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
