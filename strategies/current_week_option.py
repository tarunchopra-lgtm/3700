#!/usr/bin/env python3
"""Inspect this week's call and put options for a ticker or current stock positions.

Usage:
    python current_week_option.py [TICKER]

If TICKER is provided, the script uses the ticker's current price as the
reference price. If no ticker is provided, it inspects the account's current
stock positions and uses each position's buy price as the reference price.
For each reference price, it finds the nearest call and put options expiring
this week and prints each option's latest price, volume, and open interest.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date, timedelta

from alpaca.data import TimeFrame
from alpaca.data.enums import DataFeed, OptionsFeed
from alpaca.data.historical import OptionHistoricalDataClient, StockHistoricalDataClient
from alpaca.data.requests import OptionBarsRequest, OptionLatestQuoteRequest, StockLatestTradeRequest
from alpaca.trading.enums import AssetStatus, ContractType
from alpaca.trading.requests import GetOptionContractsRequest

from pathlib import Path
import sys

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

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


def get_option_contracts_for_week(trading_client, symbol: str, expiration: date, contract_type: ContractType):
    request = GetOptionContractsRequest(
        underlying_symbols=[symbol],
        expiration_date=expiration,
        type=contract_type,
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
    latest_quote_response = option_data_client.get_option_latest_quote(
        OptionLatestQuoteRequest(symbol_or_symbols=contract_symbol, feed=OptionsFeed.INDICATIVE)
    )
    latest_quote = latest_quote_response.get(contract_symbol) if isinstance(latest_quote_response, dict) else latest_quote_response
    bid_price = getattr(latest_quote, "bid_price", None)
    ask_price = getattr(latest_quote, "ask_price", None)

    option_price = None
    if bid_price is not None and ask_price is not None:
        option_price = (float(bid_price) + float(ask_price)) / 2.0
    elif bid_price is not None:
        option_price = float(bid_price)
    elif ask_price is not None:
        option_price = float(ask_price)

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
    option_side: str,
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

    print(f"  {option_side} Contract:      {contract_symbol}")
    print(f"  {option_side} Strike:        {format_money(strike_price)}")
    print(f"  {option_side} Mid Price:     {format_money(option_price)}")
    print(f"  {option_side} Volume:        {format_number(volume)}")
    print(f"  {option_side} Open Interest: {format_number(open_interest)}")


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
        print(f"\n{reference.symbol}")
        print(f"  Reference price: {format_money(reference.reference_price)}")
        print(f"  Expiration:      {expiration.isoformat()}")

        try:
            call_contracts = get_option_contracts_for_week(
                trading_client,
                reference.symbol,
                expiration,
                ContractType.CALL,
            )
            if not call_contracts:
                print(f"  No active CALL contracts found for {expiration.isoformat()}")
            else:
                selected_call_contract = pick_nearest_contract(call_contracts, reference.reference_price)
                call_price, call_volume = get_option_price_volume(option_data_client, selected_call_contract.symbol)
                full_call_contract = trading_client.get_option_contract(selected_call_contract.symbol)
                call_open_interest = getattr(full_call_contract, "open_interest", None)
                print_contract_summary(
                    "CALL",
                    reference.symbol,
                    reference.reference_price,
                    expiration,
                    full_call_contract,
                    call_price,
                    call_volume,
                    call_open_interest,
                )

            put_contracts = get_option_contracts_for_week(
                trading_client,
                reference.symbol,
                expiration,
                ContractType.PUT,
            )
            if not put_contracts:
                print(f"  No active PUT contracts found for {expiration.isoformat()}")
            else:
                selected_put_contract = pick_nearest_contract(put_contracts, reference.reference_price)
                put_price, put_volume = get_option_price_volume(option_data_client, selected_put_contract.symbol)
                full_put_contract = trading_client.get_option_contract(selected_put_contract.symbol)
                put_open_interest = getattr(full_put_contract, "open_interest", None)
                print_contract_summary(
                    "PUT",
                    reference.symbol,
                    reference.reference_price,
                    expiration,
                    full_put_contract,
                    put_price,
                    put_volume,
                    put_open_interest,
                )
        except Exception as exc:
            print(f"  Error: {exc}")


if __name__ == "__main__":
    main()
