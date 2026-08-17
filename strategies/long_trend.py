#!/usr/bin/env python3
"""Trade a long breakout above descending resistance built from volume candles.

Usage:
    python strategies/long_trend.py <TICKER> <SHARES> <VOLUME_PER_CANDLE>

Example:
    python strategies/long_trend.py MU 10 1000
"""

from __future__ import annotations

import sys
import time
import math
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestTradeRequest, StockTradesRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from roles.credentials import bootstrap_trading_auth


ENTRY_LOOKBACK = 15
EXIT_LOOKBACK = 10
CHECK_INTERVAL_SECONDS = 30
INITIAL_HISTORY_HOURS = 6
MAX_STORED_CANDLES = 100


@dataclass(frozen=True)
class VolumeCandle:
    timestamp: Any
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class TrendLine:
    slope: float
    intercept: float
    projected: float

    def value_at(self, candle_index: int) -> float:
        return self.intercept + self.slope * candle_index


class VolumeCandleBuilder:
    def __init__(self, volume_target: int) -> None:
        self.volume_target = float(volume_target)
        self.completed: deque[VolumeCandle] = deque(maxlen=MAX_STORED_CANDLES)
        self._timestamp = None
        self._open: float | None = None
        self._high: float | None = None
        self._low: float | None = None
        self._close: float | None = None
        self._volume = 0.0

    def add_trade(self, timestamp: Any, price: float, size: float) -> None:
        remaining = float(size)
        while remaining > 0:
            if self._open is None:
                self._timestamp = timestamp
                self._open = price
                self._high = price
                self._low = price

            self._high = max(float(self._high), price)
            self._low = min(float(self._low), price)
            self._close = price

            accepted = min(remaining, self.volume_target - self._volume)
            self._volume += accepted
            remaining -= accepted

            if self._volume >= self.volume_target:
                self.completed.append(
                    VolumeCandle(
                        timestamp=self._timestamp,
                        open=float(self._open),
                        high=float(self._high),
                        low=float(self._low),
                        close=float(self._close),
                        volume=self._volume,
                    )
                )
                self._timestamp = None
                self._open = None
                self._high = None
                self._low = None
                self._close = None
                self._volume = 0.0


def _extract_trade_rows(response, symbol: str) -> list[tuple[Any, float, float]]:
    frame = response.df
    if frame.empty:
        return []

    if "symbol" in frame.index.names:
        try:
            frame = frame.xs(symbol, level="symbol")
        except KeyError:
            return []

    frame = frame.sort_index()
    return [
        (timestamp, float(row["price"]), float(row["size"]))
        for timestamp, row in frame.iterrows()
        if float(row["size"]) > 0
    ]


def _fetch_trades(
    data_client: StockHistoricalDataClient,
    symbol: str,
    start: datetime,
    end: datetime,
) -> list[tuple[Any, float, float]]:
    response = data_client.get_stock_trades(
        StockTradesRequest(
            symbol_or_symbols=symbol,
            start=start,
            end=end,
            feed=DataFeed.IEX,
        )
    )
    return _extract_trade_rows(response, symbol)


def _latest_price(data_client: StockHistoricalDataClient, symbol: str) -> float:
    response = data_client.get_stock_latest_trade(
        StockLatestTradeRequest(symbol_or_symbols=symbol, feed=DataFeed.IEX)
    )
    return float(response[symbol].price)


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
        return None


def _position_quantity(position) -> int:
    return int(round(float(getattr(position, "qty", 0))))


def _order_status(trading_client, order_id: str | None) -> str:
    if order_id is None:
        return ""
    try:
        order = trading_client.get_order_by_id(order_id)
        status = getattr(order, "status", "")
        return (status.value if hasattr(status, "value") else str(status)).upper()
    except Exception:
        return "UNKNOWN"


def _submit_market_order(trading_client, symbol: str, quantity: int, side: OrderSide) -> str:
    response = trading_client.submit_order(
        order_data=MarketOrderRequest(
            symbol=symbol,
            qty=quantity,
            side=side,
            time_in_force=TimeInForce.DAY,
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


def _parse_arguments() -> tuple[str, int, int]:
    if len(sys.argv) != 4:
        raise ValueError(
            "Usage: python strategies/long_trend.py <TICKER> <SHARES> <VOLUME_PER_CANDLE>"
        )

    symbol = sys.argv[1].strip().upper()
    try:
        quantity = int(sys.argv[2])
        volume_target = int(sys.argv[3])
    except ValueError as exc:
        raise ValueError("SHARES and VOLUME_PER_CANDLE must be whole numbers") from exc

    if not symbol or quantity <= 0 or volume_target <= 0:
        raise ValueError("TICKER must be set and numeric arguments must be greater than zero")
    return symbol, quantity, volume_target


def main() -> int:
    try:
        symbol, quantity, volume_target = _parse_arguments()
    except ValueError as exc:
        print(exc)
        return 1

    try:
        credentials, trading_client = bootstrap_trading_auth("long_trend.py")
    except Exception as exc:
        print(f"Authentication failed: {exc}")
        return 1

    data_client = StockHistoricalDataClient(credentials.api_key, credentials.secret_key)
    builder = VolumeCandleBuilder(volume_target)
    history_end = datetime.now(timezone.utc)
    history_start = history_end - timedelta(hours=INITIAL_HISTORY_HOURS)

    try:
        initial_trades = _fetch_trades(data_client, symbol, history_start, history_end)
    except Exception as exc:
        print(f"Could not load initial trades for {symbol}: {exc}")
        return 1

    if not initial_trades:
        print(f"No IEX trades returned for {symbol} in the last {INITIAL_HISTORY_HOURS} hours")
        return 1

    for timestamp, price, size in initial_trades:
        builder.add_trade(timestamp, price, size)
    last_trade_timestamp = initial_trades[-1][0]

    print(
        f"Watching {symbol}: quantity={quantity}, volume candle={volume_target:,} shares, "
        f"entry lookback={ENTRY_LOOKBACK}, exit lookback={EXIT_LOOKBACK}, "
        f"refresh={CHECK_INTERVAL_SECONDS}s"
    )
    print("Entry requires a negative 15-candle high slope and price above projected resistance.")
    print("Press Ctrl+C to stop.\n")

    initial_stop: float | None = None
    entry_order_id: str | None = None
    exit_order_id: str | None = None

    try:
        while True:
            cycle_end = datetime.now(timezone.utc)
            try:
                new_trades = _fetch_trades(
                    data_client,
                    symbol,
                    last_trade_timestamp + timedelta(microseconds=1),
                    cycle_end,
                )
                for timestamp, price, size in new_trades:
                    builder.add_trade(timestamp, price, size)
                if new_trades:
                    last_trade_timestamp = new_trades[-1][0]

                current_price = _latest_price(data_client, symbol)
                candles = list(builder.completed)
                if len(candles) < ENTRY_LOOKBACK:
                    print(
                        f"[{datetime.now().strftime('%H:%M:%S')}] Waiting for completed candles: "
                        f"{len(candles)}/{ENTRY_LOOKBACK}"
                    )
                    time.sleep(CHECK_INTERVAL_SECONDS)
                    continue

                entry_candles = candles[-ENTRY_LOOKBACK:]
                entry_trend = _fit_trend([candle.high for candle in entry_candles])
                breakout_price = _first_cent_above(entry_trend.projected)
                exit_trend = None
                if len(candles) >= EXIT_LOOKBACK:
                    exit_trend = _fit_trend([candle.low for candle in candles[-EXIT_LOOKBACK:]])

                position = _find_position(trading_client, symbol)
                position_qty = _position_quantity(position) if position is not None else 0
                if position_qty < 0:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] Existing SHORT position found; no action taken")
                    time.sleep(CHECK_INTERVAL_SECONDS)
                    continue

                _render_chart(
                    entry_candles,
                    entry_trend,
                    breakout_price,
                    exit_trend if position_qty > 0 else None,
                    current_price,
                )
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
                        initial_stop = min(candle.low for candle in entry_candles)
                    dynamic_exit = exit_trend.projected if exit_trend is not None else initial_stop
                    exit_level = max(initial_stop, dynamic_exit)
                    print(
                        f"POSITION RISK: Hard stop (15-candle lowest low)=${initial_stop:.2f} | "
                        f"Low trend line (10 candles)=${dynamic_exit:.4f} | "
                        f"SELL if price below=${exit_level:.2f}"
                    )
                    if exit_order_id is not None:
                        status = _order_status(trading_client, exit_order_id)
                        print(f"Exit order {exit_order_id} status={status}")
                        if status in {"CANCELED", "EXPIRED", "REJECTED"}:
                            exit_order_id = None
                    if current_price < exit_level and exit_order_id is None:
                        exit_order_id = _submit_market_order(
                            trading_client, symbol, position_qty, OrderSide.SELL
                        )
                        print(
                            f"[EXIT] {symbol} ${current_price:.2f} below ${exit_level:.2f}; "
                            f"market sell submitted id={exit_order_id}"
                        )
                else:
                    if exit_order_id is not None:
                        print(f"[FLAT] Exit order {exit_order_id} completed; ready for another setup")
                        exit_order_id = None
                        initial_stop = None

                    if entry_order_id is not None:
                        status = _order_status(trading_client, entry_order_id)
                        print(f"Entry order {entry_order_id} status={status}")
                        if status in {"CANCELED", "EXPIRED", "REJECTED"}:
                            entry_order_id = None

                    descending = entry_trend.slope < 0
                    breakout = current_price >= breakout_price
                    if descending and breakout and entry_order_id is None:
                        initial_stop = min(candle.low for candle in entry_candles)
                        entry_order_id = _submit_market_order(
                            trading_client, symbol, quantity, OrderSide.BUY
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