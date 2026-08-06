#!/usr/bin/env python3
"""Inspect this week's call option for a ticker or current stock positions.

Usage:
    python current_week_option.py [TICKER]

If TICKER is provided, the script uses the ticker's current price as the
reference price. If no ticker is provided, it inspects the account's current
stock positions and uses each position's buy price as the reference price.
For each reference price, it finds the nearest call option expiring this week
and prints the option's latest price, volume, and open interest.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date, timedelta

from alpaca.data import TimeFrame
from alpaca.data.enums import DataFeed, OptionsFeed
from alpaca.data.historical import OptionHistoricalDataClient, StockHistoricalDataClient
from alpaca.data.requests import OptionBarsRequest, OptionLatestTradeRequest, StockLatestTradeRequest
from alpaca.trading.enums import AssetStatus, ContractType
from alpaca.trading.requests import GetOptionContractsRequest

from roles.credentials import bootstrap_trading_auth


@dataclass(frozen=True)
class ReferenceSymbol:
    symbol: str
    reference_price: float
    label: str


def format_money(value: object) -> str:
    try:
        return f"${float(value):.2f}"
    except (TypeError, ValueError):
        return "N/A"


def format_number(value: object) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "N/A"
    if number.is_integer():
        return str(int(number))
    return f"{number:.2f}"


def get_weekly_expiration(today: date | None = None) -> date:
    if today is None:
        today = date.today()
    days_until_friday = (4 - today.weekday()) % 7
    return today + timedelta(days=days_until_friday)


def build_reference_list(trading_client) -> list[ReferenceSymbol]:
    positions = list(trading_client.get_all_positions())
    references: list[ReferenceSymbol] = []
    for position in positions:
        asset_class = getattr(position, "asset_class", "")
        asset_class_value = getattr(asset_class, "value", asset_class)
        asset_class_text = str(asset_class_value).lower()
        symbol = str(getattr(position, "symbol", "")).upper()
        if not symbol or not any(token in asset_class_text for token in ("us_equity", "stock")):
            continue

        try:
            buy_price = float(getattr(position, "avg_entry_price", 0) or 0)
        except (TypeError, ValueError):
            continue

        if buy_price <= 0:
            continue

        references.append(ReferenceSymbol(symbol=symbol, reference_price=buy_price, label="buy price"))

    return references


def get_current_underlying_price(symbol: str, stock_data_client: StockHistoricalDataClient) -> float:
    trade = stock_data_client.get_stock_latest_trade(
        StockLatestTradeRequest(symbol_or_symbols=symbol, feed=DataFeed.IEX)
    )
    latest_trade = trade.get(symbol) if isinstance(trade, dict) else trade
    price = getattr(latest_trade, "price", None)
    if price is None:
        raise RuntimeError(f"No live price available for {symbol}")
    return float(price)


def get_option_contracts_for_week(trading_client, symbol: str, expiration: date):
    request = GetOptionContractsRequest(
        underlying_symbols=[symbol],
        expiration_date=expiration,
        type=ContractType.CALL,
        status=AssetStatus.ACTIVE,
        limit=1000,
    )
    response = trading_client.get_option_contracts(request)
    contracts = getattr(response, "option_contracts", None)
    if contracts is None and isinstance(response, dict):
        contracts = response.get("option_contracts", [])
    return list(contracts or [])


def pick_nearest_contract(contracts, target_price: float):
    return min(
        contracts,
        key=lambda contract: (
            abs(float(getattr(contract, "strike_price", 0.0)) - target_price),
            float(getattr(contract, "strike_price", 0.0)),
        ),
    )


def get_option_price_volume(option_data_client: OptionHistoricalDataClient, contract_symbol: str):
    latest_trade_response = option_data_client.get_option_latest_trade(
        OptionLatestTradeRequest(symbol_or_symbols=contract_symbol, feed=OptionsFeed.INDICATIVE)
    )
    latest_trade = latest_trade_response.get(contract_symbol) if isinstance(latest_trade_response, dict) else latest_trade_response
    option_price = getattr(latest_trade, "price", None)

    bars_response = option_data_client.get_option_bars(
        OptionBarsRequest(
            symbol_or_symbols=contract_symbol,
            timeframe=TimeFrame.Day,
            limit=10,
        )
    )
    bars_data = getattr(bars_response, "data", {})
    bars = bars_data.get(contract_symbol, []) if isinstance(bars_data, dict) else []
    last_bar = bars[-1] if bars else None
    volume = getattr(last_bar, "volume", None)

    return option_price, volume


def print_contract_summary(
    symbol: str,
    reference_price: float,
    expiration: date,
    contract,
    option_price: object,
    volume: object,
    open_interest: object,
) -> None:
    strike_price = float(getattr(contract, "strike_price", 0.0))
    contract_symbol = getattr(contract, "symbol", symbol)

    print(f"\n{symbol}")
    print(f"  Reference price: {format_money(reference_price)}")
    print(f"  Expiration:      {expiration.isoformat()}")
    print(f"  Contract:        {contract_symbol}")
    print(f"  Strike:          {format_money(strike_price)}")
    print(f"  Option price:    {format_money(option_price)}")
    print(f"  Volume:          {format_number(volume)}")
    print(f"  Open interest:   {format_number(open_interest)}")


def main() -> None:
    try:
        credentials, trading_client = bootstrap_trading_auth("current_week_option.py")
    except Exception as exc:
        print(f"Authentication failed: {exc}")
        raise SystemExit(1)

    stock_data_client = StockHistoricalDataClient(credentials.api_key, credentials.secret_key)
    option_data_client = OptionHistoricalDataClient(credentials.api_key, credentials.secret_key)

    expiration = get_weekly_expiration()

    if len(sys.argv) > 1:
        ticker = sys.argv[1].strip().upper()
        try:
            reference_price = get_current_underlying_price(ticker, stock_data_client)
        except Exception as exc:
            print(f"Error fetching current price for {ticker}: {exc}")
            raise SystemExit(1)

        references = [ReferenceSymbol(symbol=ticker, reference_price=reference_price, label="current price")]
    else:
        references = build_reference_list(trading_client)
        if not references:
            print("No current stock positions found.")
            return

    print(f"Current week expiration: {expiration.isoformat()}")

    for reference in references:
        try:
            contracts = get_option_contracts_for_week(trading_client, reference.symbol, expiration)
            if not contracts:
                print(f"\n{reference.symbol}")
                print(f"  No active call contracts found for {expiration.isoformat()}")
                continue

            selected_contract = pick_nearest_contract(contracts, reference.reference_price)
            option_price, volume = get_option_price_volume(option_data_client, selected_contract.symbol)
            full_contract = trading_client.get_option_contract(selected_contract.symbol)
            open_interest = getattr(full_contract, "open_interest", None)

            print_contract_summary(
                reference.symbol,
                reference.reference_price,
                expiration,
                full_contract,
                option_price,
                volume,
                open_interest,
            )
        except Exception as exc:
            print(f"\n{reference.symbol}")
            print(f"  Error: {exc}")


if __name__ == "__main__":
    main()