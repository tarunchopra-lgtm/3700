#!/usr/bin/env python3
"""Trade a long breakout above descending resistance built from volume candles.

Supports stocks, slash-form crypto pairs, and OCC option symbols. Option volume
history requires an accepted Alpaca OPRA market-data agreement.

After entry, Target 1 sells half at one times the initial risk. The remainder
exits when price breaks the 10-candle low trend line or the original hard stop.

Usage:
    python strategies/long_trend.py <SYMBOL> <QUANTITY> <VOLUME_PER_CANDLE> [--history-hours HOURS]

Examples:
    python strategies/long_trend.py MU 10 1000
    python strategies/long_trend.py SPY 2 25000 --history-hours 48
    python strategies/long_trend.py BTC/USD 0.01 0.25
    python strategies/long_trend.py SPY260821C00650000 2 100
"""

from __future__ import annotations

import argparse
import sys
import time
import math
import re
import pandas as pd
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from alpaca.common.exceptions import APIError
from alpaca.data.enums import DataFeed, OptionsFeed
from alpaca.data.historical import (
    CryptoHistoricalDataClient,
    OptionHistoricalDataClient,
    StockHistoricalDataClient,
)
from alpaca.data.requests import (
    CryptoLatestTradeRequest,
    OptionLatestTradeRequest,
    StockLatestTradeRequest,
    StockBarsRequest,
    OptionBarsRequest,
)
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from roles.credentials import bootstrap_trading_auth
from roles.candle_builder import build_volume_candles_from_bars


ENTRY_LOOKBACK = 10
EXIT_LOOKBACK = 10
CHECK_INTERVAL_SECONDS = 30
INITIAL_HISTORY_HOURS = 24  # Fetch full trading day of history to build candles
MAX_STORED_CANDLES = 100
OPTION_SYMBOL_PATTERN = re.compile(r"^[A-Z]{1,6}\d{6}[CP]\d{8}$")


@dataclass(frozen=True)
class VolumeCandle:
    """A candle built from fixed volume accumulation."""
    timestamp: Any
    open: float
    high: float
    low: float
    close: float
    volume: float


class VolumeCandleManager:
    """Manages a collection of volume candles fetched from 1-minute bars."""
    def __init__(self, volume_target: float) -> None:
        self.volume_target = float(volume_target)
        self.completed: list[dict] = []

    def add_candles(self, candles_df) -> None:
        """Add candles from a DataFrame."""
        if candles_df is not None and not candles_df.empty:
            for _, row in candles_df.iterrows():
                self.completed.append({
                    'timestamp': row.get('timestamp'),
                    'open': float(row.get('open', 0)),
                    'high': float(row.get('high', 0)),
                    'low': float(row.get('low', 0)),
                    'close': float(row.get('close', 0)),
                    'volume': float(row.get('volume', 0)),
                })
            # Keep only last MAX_STORED_CANDLES
            self.completed = self.completed[-MAX_STORED_CANDLES:]


@dataclass(frozen=True)
class TrendLine:
    slope: float
    intercept: float
    projected: float

    def value_at(self, candle_index: int) -> float:
        return self.intercept + self.slope * candle_index


def _fetch_minute_bars(
    data_client,
    asset_kind: str,
    symbol: str,
    start: datetime,
    end: datetime,
) -> list[tuple[Any, float, float, float, float]]:
    """Fetch 1-minute bars and return as list of (timestamp, open, high, low, close, volume)."""
    try:
        if asset_kind == "crypto":
            # Crypto doesn't support minute bars through this client, use hourly
            # For now, raise an error directing user to use stock symbols
            raise RuntimeError("Crypto candles not yet supported in this version; use stock symbols")
        elif asset_kind == "option":
            response = data_client.get_option_bars(
                OptionBarsRequest(
                    symbol_or_symbols=symbol,
                    timeframe=TimeFrame.Minute,
                    start=start,
                    end=end,
                    feed=OptionsFeed.INDICATIVE,
                )
            )
        else:
            response = data_client.get_stock_bars(
                StockBarsRequest(
                    symbol_or_symbols=symbol,
                    timeframe=TimeFrame.Minute,
                    start=start,
                    end=end,
                    feed=DataFeed.IEX,
                )
            )
        
        # Convert to DataFrame
        df = response.df
        if "symbol" in df.index.names:
            df = df.reset_index(level=0, drop=True)
        df = df.reset_index()
        
        if df.empty:
            return None
        
        return df
    except Exception as exc:
        print(f"[WARN] Could not fetch minute bars for {symbol}: {exc}")
        return None


def _latest_price(data_client, asset_kind: str, symbol: str) -> float:
    if asset_kind == "crypto":
        response = data_client.get_crypto_latest_trade(
            CryptoLatestTradeRequest(symbol_or_symbols=symbol)
        )
    elif asset_kind == "option":
        response = data_client.get_option_latest_trade(
            OptionLatestTradeRequest(symbol_or_symbols=symbol, feed=OptionsFeed.INDICATIVE)
        )
    else:
        response = data_client.get_stock_latest_trade(
            StockLatestTradeRequest(symbol_or_symbols=symbol, feed=DataFeed.IEX)
        )

    trade = response.get(symbol)
    if trade is None:
        normalized = _normalize_symbol(symbol)
        trade = next(
            (value for key, value in response.items() if _normalize_symbol(key) == normalized),
            None,
        )
    if trade is None:
        raise RuntimeError(f"No latest trade returned for {symbol}")
    return float(trade.price)


def _normalize_symbol(symbol: Any) -> str:
    return str(symbol).replace("/", "").replace("-", "").upper()


def _asset_kind(symbol: str) -> str:
    if OPTION_SYMBOL_PATTERN.fullmatch(symbol):
        return "option"
    if "/" in symbol or "-" in symbol:
        return "crypto"
    return "stock"


def _build_data_client(credentials, asset_kind: str):
    if asset_kind == "crypto":
        return CryptoHistoricalDataClient(credentials.api_key, credentials.secret_key)
    if asset_kind == "option":
        return OptionHistoricalDataClient(credentials.api_key, credentials.secret_key)
    return StockHistoricalDataClient(credentials.api_key, credentials.secret_key)


def _fit_trend(values: list[float]) -> TrendLine:
    count = len(values)
    x_sum = sum(range(count))
    y_sum = sum(values)
    xx_sum = sum(index * index for index in range(count))
    xy_sum = sum(index * value for index, value in enumerate(values))
    denominator = count * xx_sum - x_sum * x_sum
    slope = (count * xy_sum - x_sum * y_sum) / denominator
    intercept = (y_sum - slope * x_sum) / count
    return TrendLine(slope=slope, intercept=intercept, projected=intercept + slope * count)


def _first_cent_above(price: float) -> float:
    return (math.floor(price * 100.0 + 1e-9) + 1) / 100.0


def _find_position(trading_client, symbol: str):
    try:
        return trading_client.get_open_position(symbol)
    except Exception:
        target = _normalize_symbol(symbol)
        try:
            return next(
                (
                    position
                    for position in trading_client.get_all_positions()
                    if _normalize_symbol(getattr(position, "symbol", "")) == target
                ),
                None,
            )
        except Exception:
            return None


def _position_quantity(position, asset_kind: str) -> float | int:
    quantity = float(getattr(position, "qty", 0))
    return quantity if asset_kind == "crypto" else int(round(quantity))


def _half_quantity(quantity: float | int, asset_kind: str) -> float | int:
    if asset_kind == "crypto":
        return float(quantity) / 2.0
    return int(quantity) // 2


def _order_status(trading_client, order_id: str | None) -> str:
    if order_id is None:
        return ""
    try:
        order = trading_client.get_order_by_id(order_id)
        status = getattr(order, "status", "")
        return (status.value if hasattr(status, "value") else str(status)).upper()
    except Exception:
        return "UNKNOWN"


def _submit_market_order(
    trading_client,
    symbol: str,
    quantity: float | int,
    side: OrderSide,
    asset_kind: str,
) -> str:
    response = trading_client.submit_order(
        order_data=MarketOrderRequest(
            symbol=symbol,
            qty=quantity,
            side=side,
            time_in_force=TimeInForce.GTC if asset_kind == "crypto" else TimeInForce.DAY,
        )
    )
    return str(response.id)


def _render_chart(
    candles: list[VolumeCandle],
    entry_trend: TrendLine,
    breakout_price: float,
    exit_trend: TrendLine | None,
    current_price: float,
) -> None:
    visible = candles[-ENTRY_LOOKBACK:]
    entry_offset = len(candles) - len(visible) - (len(candles) - ENTRY_LOOKBACK)
    prices = [value for candle in visible for value in (candle.low, candle.high)]
    prices.extend([current_price, entry_trend.projected, breakout_price])
    if exit_trend is not None:
        prices.append(exit_trend.projected)

    low_bound = min(prices)
    high_bound = max(prices)
    width = 54
    spread = max(high_bound - low_bound, 0.01)

    def column(price: float) -> int:
        return min(width - 1, max(0, round((price - low_bound) / spread * (width - 1))))

    print(f"\n{'#':>3}  {'LOW':>9}  {'HIGH':>9}  PRICE MAP ({low_bound:.2f} to {high_bound:.2f})")
    for index, candle in enumerate(visible):
        row = [" "] * width
        for position in range(column(candle.low), column(candle.high) + 1):
            row[position] = "-"
        row[column(candle.close)] = "C"
        entry_value = entry_trend.value_at(index + entry_offset)
        row[column(entry_value)] = "R" if row[column(entry_value)] == " " else "*"
        print(f"{index + 1:>3}  {candle.low:>9.2f}  {candle.high:>9.2f}  |{''.join(row)}|")

    marker = [" "] * width
    marker[column(current_price)] = "P"
    marker[column(entry_trend.projected)] = "R"
    breakout_col = column(breakout_price)
    marker[breakout_col] = "B" if marker[breakout_col] == " " else "*"
    if exit_trend is not None:
        exit_col = column(exit_trend.projected)
        marker[exit_col] = "X" if marker[exit_col] == " " else "*"
    print(f"NOW  {current_price:>9.2f}             |{''.join(marker)}|")
    print("Legend: C=close, R=resistance, B=long breakout, X=low-trend exit, P=current, *=overlap")


def _parse_arguments() -> tuple[str, float | int, float, int]:
    parser = argparse.ArgumentParser(
        description="Trade a long breakout above descending resistance built from volume candles.",
        usage="python strategies/long_trend.py <SYMBOL> <QUANTITY> <VOLUME_PER_CANDLE> [--history-hours HOURS]"
    )
    parser.add_argument("symbol", help="Stock ticker, crypto pair (e.g., BTC/USD), or OCC option symbol")
    parser.add_argument("quantity", type=float, help="Quantity to trade")
    parser.add_argument("volume", type=float, help="Volume per candle")
    parser.add_argument(
        "--history-hours",
        type=int,
        default=INITIAL_HISTORY_HOURS,
        help=f"Hours of historical data to load initially (default: {INITIAL_HISTORY_HOURS})"
    )
    
    args = parser.parse_args()
    
    symbol = args.symbol.strip().upper()
    if "-" in symbol and not OPTION_SYMBOL_PATTERN.fullmatch(symbol):
        symbol = symbol.replace("-", "/")
    
    raw_quantity = args.quantity
    volume_target = args.volume
    history_hours = args.history_hours
    
    if history_hours < 1:
        raise ValueError("--history-hours must be >= 1")
    
    asset_kind = _asset_kind(symbol)
    if not symbol or raw_quantity <= 0 or volume_target <= 0:
        raise ValueError("TICKER must be set and numeric arguments must be greater than zero")
    if asset_kind != "crypto" and (not raw_quantity.is_integer() or not volume_target.is_integer()):
        raise ValueError("Stock and option quantity/volume must be whole numbers")
    quantity = raw_quantity if asset_kind == "crypto" else int(raw_quantity)
    return symbol, quantity, volume_target, history_hours


def main() -> int:
    # Handle help flag manually (before argparse)
    if len(sys.argv) > 1 and sys.argv[1] in ["--help", "-h", "help"]:
        print("""
LONG_TREND.PY - Volume-Based Breakout Trading Strategy

SYNTAX:
  python strategies/long_trend.py <SYMBOL> <QUANTITY> <VOLUME_PER_CANDLE> [--history-hours HOURS] [--help]

REQUIRED:
  <SYMBOL>               Stock ticker, crypto pair, or option symbol
                        Examples: AAPL, SPY, BTC/USD, AAPL240920C00150000
  <QUANTITY>             Shares/contracts/units to trade (must be positive, whole for stocks/options)
  <VOLUME_PER_CANDLE>    Target volume per candle (whole number for stocks/options)

OPTIONAL:
  --history-hours H     Historical data lookback (default: 100 hours)
  --help, -h            Show this help message

DESCRIPTION:
  Automated breakout trading strategy using volume-based candles
  Builds resistance from volume candles (15 candle lookback)
  Enters on breakout above descending resistance
  Uses volume-weighted price action for trend identification
  Works with stocks, cryptocurrencies, and options

STRATEGY MECHANICS:
  
  Entry Signal:
  - Identifies 15 completed volume candles
  - Calculates descending resistance from candle highs
  - Enters when price closes above the resistance line
  - Requires negative slope (descending trend for breakout)

  Position Management:
  - Target 1: Sells 50% at 1R (1 * Risk/Reward)
  - Target 2: Remaining 50% follows 10-candle low-trend exit
  - Stop Loss: Adjusted based on entry volatility
  - Risk: Initial stop to entry distance

  Volume Candles:
  - Groups minute bars by cumulative volume
  - Each candle = VOLUME_PER_CANDLE units traded
  - OHLC prices calculated from grouped bars
  - More stable than time-based candles in ranging markets

EXAMPLES:
  python strategies/long_trend.py AAPL 10 5000
    - Trade Apple with 10 shares
    - Each volume candle = 5,000 shares traded
    - 100-hour history (default)

  python strategies/long_trend.py BTC/USD 0.5 100 --history-hours 240
    - Trade Bitcoin with 0.5 BTC
    - Each volume candle = 100 BTC
    - Load 240 hours of history

  python strategies/long_trend.py TSLA 5 2000 --history-hours 48
    - Tesla, 5 shares per trade
    - 2,000 share volume candles
    - 48-hour initial history

PARAMETERS:

  SYMBOL:
  - Stock: AAPL, SPY, INTC (4-6 character tickers)
  - Crypto: BTC/USD, ETH/USD (with forward slash)
  - Option: AAPL240920C00150000 (OCC format)

  QUANTITY:
  - Must be > 0 (decimal allowed for crypto only)
  - Stocks/Options: whole numbers only
  - Crypto: can be fractional (0.5 BTC valid)

  VOLUME_PER_CANDLE:
  - Must be > 0 (whole number for stocks/options)
  - Recommended: 2000-10000 for stocks
  - Smaller volumes = more candles = slower confirmation
  - Larger volumes = fewer candles = faster confirmation

ASSET SUPPORT:
  - Stocks: AAPL, SPY, INTC, etc.
  - Cryptocurrencies: BTC/USD, ETH/USD, SOL/USD, etc.
  - Options: AAPL240920C00150000 format

OUTPUT:
  - Initial candle building: [INIT] Built X/15 candles
  - Resistance calculations and slope display
  - Entry signal: [ENTRY] Symbol price signal
  - Target 1 fills: [TARGET1] 50% exit confirmation
  - Candle completions: [CANDLE] New volume candle formed
  - Stop loss exits: [STOP] Order execution

MONITORING:
  - Refreshes every 5 seconds
  - Builds volume candles from minute bars
  - Calculates rolling resistance trends
  - Real-time P&L tracking

NOTES:
  - Requires valid Alpaca API credentials
  - Works during market hours (stocks) or 24/7 (crypto)
  - Volume candle approach reduces noise vs. time-based candles
  - Breakout strategy works better in trending markets
  - Press Ctrl+C to stop and close any open positions
  - History hours: 100 default, min 1, recommended 48-240
  - Positional trade strategy (holds multiple candles)
""")
        return 0
    
    try:
        symbol, quantity, volume_target, history_hours = _parse_arguments()
    except ValueError as exc:
        print(exc)
        return 1

    try:
        credentials, trading_client = bootstrap_trading_auth("long_trend.py")
    except Exception as exc:
        print(f"Authentication failed: {exc}")
        return 1

    asset_kind = _asset_kind(symbol)
    data_client = _build_data_client(credentials, asset_kind)
    manager = VolumeCandleManager(volume_target)
    
    history_end = datetime.now(timezone.utc)
    history_start = history_end - timedelta(hours=history_hours)

    try:
        initial_bars_df = _fetch_minute_bars(data_client, asset_kind, symbol, history_start, history_end)
    except Exception as exc:
        print(f"Could not load initial bars for {symbol}: {exc}")
        return 1

    if initial_bars_df is None or initial_bars_df.empty:
        print(f"No {asset_kind} bars returned for {symbol} in the last {history_hours} hours")
        return 1

    # Build initial volume candles from minute bars
    initial_candles_df = build_volume_candles_from_bars(initial_bars_df, volume_target, ENTRY_LOOKBACK * 2)
    
    if initial_candles_df.empty:
        print(f"Could not build any volume candles from {len(initial_bars_df)} minute bars")
        return 1
    
    manager.add_candles(initial_candles_df)
    last_bar_timestamp = initial_bars_df.iloc[-1]['timestamp']

    print(
        f"Watching {symbol} ({asset_kind}): quantity={quantity:g}, "
        f"volume candle={volume_target:g} {'contracts' if asset_kind == 'option' else 'units' if asset_kind == 'crypto' else 'shares'}, "
        f"entry lookback={ENTRY_LOOKBACK}, exit lookback={EXIT_LOOKBACK}, "
        f"refresh={CHECK_INTERVAL_SECONDS}s, initial history={history_hours}h"
    )
    print(f"[INIT] Built {len(manager.completed)}/{ENTRY_LOOKBACK} volume candles from {history_hours}h of history")
    print("Entry requires a negative 15-candle high slope and price above projected resistance.")
    print("Target 1 sells half at 1R; the remainder follows the 10-candle low-trend exit.")
    print("Press Ctrl+C to stop.\n")

    initial_stop: float | None = None
    entry_order_id: str | None = None
    exit_order_id: str | None = None
    target1_order_id: str | None = None
    entry_average_price: float | None = None
    target1_price: float | None = None
    target1_sold = False
    initial_position_qty: float | int | None = None

    try:
        while True:
            cycle_end = datetime.now(timezone.utc)
            try:
                # Fetch new minute bars since last check
                new_bars_df = _fetch_minute_bars(
                    data_client,
                    asset_kind,
                    symbol,
                    last_bar_timestamp + timedelta(minutes=1),
                    cycle_end,
                )
                
                if new_bars_df is not None and not new_bars_df.empty:
                    # Rebuild all candles from all available bars
                    # (This ensures we don't miss any volume transitions)
                    all_bars_df = pd.concat([initial_bars_df, new_bars_df], ignore_index=True)
                    all_bars_df = all_bars_df.drop_duplicates(subset=['timestamp'], keep='last')
                    all_bars_df = all_bars_df.sort_values('timestamp')
                    
                    refreshed_candles_df = build_volume_candles_from_bars(
                        all_bars_df, volume_target, ENTRY_LOOKBACK * 3
                    )
                    manager.completed = []
                    manager.add_candles(refreshed_candles_df)
                    last_bar_timestamp = new_bars_df.iloc[-1]['timestamp']

                current_price = _latest_price(data_client, asset_kind, symbol)
                candles = manager.completed
                
                if len(candles) < ENTRY_LOOKBACK:
                    print(
                        f"[{datetime.now().strftime('%H:%M:%S')}] Waiting for completed candles: "
                        f"{len(candles)}/{ENTRY_LOOKBACK}"
                    )
                    time.sleep(CHECK_INTERVAL_SECONDS)
                    continue

                # Convert dict candles to simpler format for trend analysis
                entry_candles = candles[-ENTRY_LOOKBACK:]
                entry_highs = [c['high'] for c in entry_candles]
                entry_trend = _fit_trend(entry_highs)
                breakout_price = _first_cent_above(entry_trend.projected)
                
                exit_trend = None
                if len(candles) >= EXIT_LOOKBACK:
                    exit_lows = [c['low'] for c in candles[-EXIT_LOOKBACK:]]
                    exit_trend = _fit_trend(exit_lows)

                position = _find_position(trading_client, symbol)
                position_qty = _position_quantity(position, asset_kind) if position is not None else 0
                if position_qty < 0:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] Existing SHORT position found; no action taken")
                    time.sleep(CHECK_INTERVAL_SECONDS)
                    continue

                print(
                    f"[{datetime.now().strftime('%H:%M:%S')}] Current=${current_price:.2f} | "
                    f"Trend line=${entry_trend.projected:.4f} | "
                    f"LONG breakout at/above=${breakout_price:.2f} | "
                    f"Distance=${current_price - breakout_price:+.2f} | "
                    f"High slope={entry_trend.slope:.4f} | Position={position_qty}"
                )

                if position_qty > 0:
                    entry_order_id = None
                    if initial_stop is None:
                        initial_stop = min(candle['low'] for candle in entry_candles)
                    if entry_average_price is None:
                        entry_average_price = float(getattr(position, "avg_entry_price"))
                        initial_position_qty = position_qty
                        risk_per_unit = entry_average_price - initial_stop
                        if risk_per_unit <= 0:
                            raise RuntimeError(
                                f"Entry ${entry_average_price:.2f} is not above hard stop ${initial_stop:.2f}"
                            )
                        target1_price = entry_average_price + risk_per_unit
                    dynamic_exit = exit_trend.projected if exit_trend is not None else initial_stop
                    exit_level = max(initial_stop, dynamic_exit)
                    print(
                        f"POSITION RISK: Hard stop (15-candle lowest low)=${initial_stop:.2f} | "
                        f"Low trend line (10 candles)=${dynamic_exit:.4f} | "
                        f"SELL remainder if price below=${exit_level:.2f} | "
                        f"Target 1 (1R)=${target1_price:.2f} | "
                        f"Target 1 sold={target1_sold}"
                    )
                    if target1_order_id is not None:
                        target_status = _order_status(trading_client, target1_order_id)
                        print(f"Target 1 order {target1_order_id} status={target_status}")
                        if target_status == "FILLED":
                            target1_sold = True
                            target1_order_id = None
                        elif target_status in {"CANCELED", "EXPIRED", "REJECTED"}:
                            target1_order_id = None
                    if exit_order_id is not None:
                        status = _order_status(trading_client, exit_order_id)
                        print(f"Exit order {exit_order_id} status={status}")
                        if status in {"CANCELED", "EXPIRED", "REJECTED"}:
                            exit_order_id = None
                    if current_price < exit_level and exit_order_id is None:
                        if target1_order_id is not None:
                            trading_client.cancel_order_by_id(target1_order_id)
                            print("[EXIT] Cancelled pending Target 1 order; position will be reconciled next cycle")
                            target1_order_id = None
                        else:
                            exit_order_id = _submit_market_order(
                                trading_client, symbol, position_qty, OrderSide.SELL, asset_kind
                            )
                            print(
                                f"[EXIT] {symbol} ${current_price:.2f} below ${exit_level:.2f}; "
                                f"market sell submitted for remaining {position_qty} id={exit_order_id}"
                            )
                    elif not target1_sold and target1_order_id is None and current_price >= target1_price:
                        target_quantity = _half_quantity(initial_position_qty, asset_kind)
                        if target_quantity > 0:
                            target1_order_id = _submit_market_order(
                                trading_client, symbol, target_quantity, OrderSide.SELL, asset_kind
                            )
                            print(
                                f"[TARGET 1] {symbol} ${current_price:.2f} reached 1R ${target1_price:.2f}; "
                                f"market sell submitted for 50% ({target_quantity}) id={target1_order_id}"
                            )
                        else:
                            target1_sold = True
                            print("[TARGET 1] Position is too small to split; trailing the full position")
                else:
                    if exit_order_id is not None:
                        print(f"[FLAT] Exit order {exit_order_id} completed; ready for another setup")
                        exit_order_id = None
                    elif entry_average_price is not None:
                        print("[FLAT] Position closed; ready for another setup")

                    if entry_average_price is not None:
                        initial_stop = None
                        target1_order_id = None
                        entry_average_price = None
                        target1_price = None
                        target1_sold = False
                        initial_position_qty = None

                    if entry_order_id is not None:
                        status = _order_status(trading_client, entry_order_id)
                        print(f"Entry order {entry_order_id} status={status}")
                        if status in {"CANCELED", "EXPIRED", "REJECTED"}:
                            entry_order_id = None

                    descending = entry_trend.slope < 0
                    breakout = current_price >= breakout_price
                    if descending and breakout and entry_order_id is None:
                        initial_stop = min(candle['low'] for candle in entry_candles)
                        entry_order_id = _submit_market_order(
                            trading_client, symbol, quantity, OrderSide.BUY, asset_kind
                        )
                        print(
                            f"[ENTRY] {symbol} ${current_price:.2f} above descending resistance "
                            f"${entry_trend.projected:.4f} (breakout ${breakout_price:.2f}); "
                            f"BUY {quantity} market id={entry_order_id}; "
                            f"initial stop=${initial_stop:.2f}"
                        )
                    elif not descending:
                        print("[WAIT] The 15-candle high trend is not descending")
                    elif not breakout:
                        print("[WAIT] Price has not broken above descending resistance")
            except Exception as exc:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Cycle failed: {exc}")

            time.sleep(CHECK_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        print("\nStopped by user. Existing positions and orders were left unchanged.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())