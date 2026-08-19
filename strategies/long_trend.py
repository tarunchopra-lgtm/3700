#!/usr/bin/env python3
"""Trade a long breakout above descending resistance built from volume candles.

Supports stocks, slash-form crypto pairs, and OCC option symbols. Option volume
history requires an accepted Alpaca OPRA market-data agreement.

After entry, Target 1 sells half at one times the initial risk. The remainder
exits when price breaks the 10-candle low trend line or the original hard stop.

Usage:
    python strategies/long_trend.py <SYMBOL> <QUANTITY> <VOLUME_PER_CANDLE>

Examples:
    python strategies/long_trend.py MU 10 1000
    python strategies/long_trend.py BTC/USD 0.01 0.25
    python strategies/long_trend.py SPY260821C00650000 2 100
"""

from __future__ import annotations

import sys
import time
import math
import re
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
    CryptoTradesRequest,
    OptionLatestTradeRequest,
    OptionTradesRequest,
    StockLatestTradeRequest,
    StockTradesRequest,
)
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
OPTION_SYMBOL_PATTERN = re.compile(r"^[A-Z]{1,6}\d{6}[CP]\d{8}$")


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
    def __init__(self, volume_target: float) -> None:
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
            normalized = _normalize_symbol(symbol)
            matching = [value for value in frame.index.unique("symbol") if _normalize_symbol(value) == normalized]
            if not matching:
                return []
            frame = frame.xs(matching[0], level="symbol")

    frame = frame.sort_index()
    return [
        (timestamp, float(row["price"]), float(row["size"]))
        for timestamp, row in frame.iterrows()
        if float(row["size"]) > 0
    ]


def _fetch_trades(
    data_client,
    asset_kind: str,
    symbol: str,
    start: datetime,
    end: datetime,
) -> list[tuple[Any, float, float]]:
    if asset_kind == "crypto":
        response = data_client.get_crypto_trades(
            CryptoTradesRequest(symbol_or_symbols=symbol, start=start, end=end)
        )
    elif asset_kind == "option":
        try:
            response = data_client.get_option_trades(
                OptionTradesRequest(symbol_or_symbols=symbol, start=start, end=end)
            )
        except APIError as exc:
            if "OPRA agreement" in str(exc):
                raise RuntimeError(
                    "Option volume history requires accepting Alpaca's OPRA market-data agreement"
                ) from exc
            raise
    else:
        response = data_client.get_stock_trades(
            StockTradesRequest(
                symbol_or_symbols=symbol,
                start=start,
                end=end,
                feed=DataFeed.IEX,
            )
        )
    return _extract_trade_rows(response, symbol)


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


def _parse_arguments() -> tuple[str, float | int, float]:
    if len(sys.argv) != 4:
        raise ValueError(
            "Usage: python strategies/long_trend.py <SYMBOL> <QUANTITY> <VOLUME_PER_CANDLE>"
        )

    symbol = sys.argv[1].strip().upper()
    if "-" in symbol and not OPTION_SYMBOL_PATTERN.fullmatch(symbol):
        symbol = symbol.replace("-", "/")
    try:
        raw_quantity = float(sys.argv[2])
        volume_target = float(sys.argv[3])
    except ValueError as exc:
        raise ValueError("QUANTITY and VOLUME_PER_CANDLE must be numeric") from exc

    asset_kind = _asset_kind(symbol)
    if not symbol or raw_quantity <= 0 or volume_target <= 0:
        raise ValueError("TICKER must be set and numeric arguments must be greater than zero")
    if asset_kind != "crypto" and (not raw_quantity.is_integer() or not volume_target.is_integer()):
        raise ValueError("Stock and option quantity/volume must be whole numbers")
    quantity = raw_quantity if asset_kind == "crypto" else int(raw_quantity)
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

    asset_kind = _asset_kind(symbol)
    data_client = _build_data_client(credentials, asset_kind)
    builder = VolumeCandleBuilder(volume_target)
    history_end = datetime.now(timezone.utc)
    history_start = history_end - timedelta(hours=INITIAL_HISTORY_HOURS)

    try:
        initial_trades = _fetch_trades(data_client, asset_kind, symbol, history_start, history_end)
    except Exception as exc:
        print(f"Could not load initial trades for {symbol}: {exc}")
        return 1

    if not initial_trades:
        print(f"No {asset_kind} trades returned for {symbol} in the last {INITIAL_HISTORY_HOURS} hours")
        return 1

    for timestamp, price, size in initial_trades:
        builder.add_trade(timestamp, price, size)
    last_trade_timestamp = initial_trades[-1][0]

    print(
        f"Watching {symbol} ({asset_kind}): quantity={quantity:g}, "
        f"volume candle={volume_target:g} {'contracts' if asset_kind == 'option' else 'units' if asset_kind == 'crypto' else 'shares'}, "
        f"entry lookback={ENTRY_LOOKBACK}, exit lookback={EXIT_LOOKBACK}, "
        f"refresh={CHECK_INTERVAL_SECONDS}s"
    )
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
                new_trades = _fetch_trades(
                    data_client,
                    asset_kind,
                    symbol,
                    last_trade_timestamp + timedelta(microseconds=1),
                    cycle_end,
                )
                for timestamp, price, size in new_trades:
                    builder.add_trade(timestamp, price, size)
                if new_trades:
                    last_trade_timestamp = new_trades[-1][0]

                current_price = _latest_price(data_client, asset_kind, symbol)
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
                position_qty = _position_quantity(position, asset_kind) if position is not None else 0
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
                        initial_stop = min(candle.low for candle in entry_candles)
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