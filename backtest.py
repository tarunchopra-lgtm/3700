from __future__ import annotations

import math
from pathlib import Path
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

from roles.credentials import bootstrap_trading_auth


ET = ZoneInfo("America/New_York")


@dataclass
class DayResult:
    date: str
    entry: float
    stop: float
    target1: float
    target2: float
    entered: bool
    outcome: str
    pnl: float


@dataclass
class StrategyConfig:
    program_name: str
    symbol: str
    qty: int
    mode: str  # "previous_close" or "fixed_levels"
    stop_pct: float | None = None
    target1_pct: float | None = None
    target2_pct: float | None = None
    fixed_entry: float | None = None
    fixed_stop: float | None = None
    fixed_target1: float | None = None
    fixed_target2: float | None = None
    require_open_above_prev_close: bool = False


def _print_usage() -> None:
    print("Usage: python backtest.py <DAYS_BACK> <PROGRAM_FILE.py> <PROGRAM_ARGS...>")
    print("")
    print("Supported program adapters:")
    print("  previous_close.py <TICKER> <NUM_STOCKS>")
    print("    Uses previous_close levels: stop=1%, target1=1%, target2=3%.")
    print("    Applies open gate: enter only if today's open > previous close.")
    print("")
    print("  fomo_trade.py <TICKER> <NUM_STOCKS> <ENTRY_PRICE> <STOP_PRICE> <TARGET1_PRICE> <TARGET2_PRICE>")
    print("    Uses fixed absolute entry/stop/targets from supplied args for each tested day.")
    print("")
    print("Examples:")
    print("  python backtest.py 100 previous_close.py INTC 100")
    print("  python backtest.py 60 fomo_trade.py INTC 100 40 39.6 40.4 41.2")


def _parse_percent(value: str, arg_name: str) -> float:
    raw = value.strip().replace("%", "")
    try:
        pct = float(raw)
    except ValueError as exc:
        raise ValueError(f"{arg_name} must be numeric (for example 1 or 1%).") from exc
    if pct <= 0:
        raise ValueError(f"{arg_name} must be greater than 0.")
    return pct / 100.0


def _parse_float(value: str, arg_name: str) -> float:
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{arg_name} must be numeric.") from exc


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


def _bar_value(bar, field: str):
    value = getattr(bar, field, None)
    if value is None and isinstance(bar, dict):
        value = bar.get(field)
    return value


def _first_hit_in_segment(p0: float, p1: float, levels: list[tuple[str, float]]):
    if p1 > p0:
        candidates = [(name, price) for name, price in levels if p0 < price <= p1]
        if not candidates:
            return None
        return min(candidates, key=lambda x: x[1])
    if p1 < p0:
        candidates = [(name, price) for name, price in levels if p1 <= price < p0]
        if not candidates:
            return None
        return max(candidates, key=lambda x: x[1])
    return None


def _simulate_bar_path(o: float, h: float, l: float, c: float):
    # Deterministic intrabar assumption used for OHLC-only paths.
    # Green bar: O->L->H->C, Red bar: O->H->L->C.
    if c >= o:
        return [o, l, h, c]
    return [o, h, l, c]


def _simulate_day(
    minute_bars,
    qty: int,
    entry: float,
    stop: float,
    target1: float,
    target2: float,
) -> tuple[bool, str, float]:
    first_qty = qty // 2
    second_qty = qty - first_qty

    entered = False
    sold_first = False
    remaining = 0
    pnl = 0.0

    for bar in minute_bars:
        o = float(_bar_value(bar, "open"))
        h = float(_bar_value(bar, "high"))
        l = float(_bar_value(bar, "low"))
        c = float(_bar_value(bar, "close"))

        if not entered:
            # Buy LIMIT fill approximation: order is considered filled only if price trades down to entry.
            if l <= entry:
                entered = True
                remaining = qty
            else:
                continue

        path = _simulate_bar_path(o, h, l, c)
        seg_start = path[0]

        for seg_end in path[1:]:
            while True:
                if remaining == 0:
                    break

                if not sold_first:
                    levels = [("STOP", stop), ("T1", target1)]
                else:
                    levels = [("BE", entry), ("T2", target2)]

                hit = _first_hit_in_segment(seg_start, seg_end, levels)
                if hit is None:
                    break

                hit_name, hit_price = hit
                seg_start = hit_price

                if hit_name == "STOP":
                    pnl += remaining * (hit_price - entry)
                    return True, "stopped_before_t1", round(pnl, 2)

                if hit_name == "T1":
                    pnl += first_qty * (hit_price - entry)
                    sold_first = True
                    remaining = second_qty
                    continue

                if hit_name == "BE":
                    pnl += remaining * (hit_price - entry)
                    return True, "t1_then_breakeven", round(pnl, 2)

                if hit_name == "T2":
                    pnl += remaining * (hit_price - entry)
                    return True, "t1_then_t2", round(pnl, 2)

            seg_start = seg_end

    if not entered:
        return False, "no_entry", 0.0

    # End-of-day flattening assumption for backtest accounting.
    last_close = float(_bar_value(minute_bars[-1], "close"))
    pnl += remaining * (last_close - entry)
    if sold_first:
        return True, "t1_then_eod", round(pnl, 2)
    return True, "entered_eod", round(pnl, 2)


def _parse_strategy_config(program_file: str, program_args: list[str]) -> StrategyConfig:
    program_name = Path(program_file).name.lower()

    if program_name == "previous_close.py":
        if len(program_args) != 2:
            raise ValueError("previous_close.py requires arguments: <TICKER> <NUM_STOCKS>")
        symbol = program_args[0].upper()
        try:
            qty = int(program_args[1])
        except ValueError as exc:
            raise ValueError("NUM_STOCKS must be an integer for previous_close.py") from exc
        if qty <= 0:
            raise ValueError("NUM_STOCKS must be greater than 0 for previous_close.py")
        if qty % 2 != 0:
            raise ValueError("NUM_STOCKS must be even for previous_close.py strategy logic")

        return StrategyConfig(
            program_name=program_name,
            symbol=symbol,
            qty=qty,
            mode="previous_close",
            stop_pct=0.01,
            target1_pct=0.01,
            target2_pct=0.03,
            require_open_above_prev_close=True,
        )

    if program_name == "fomo_trade.py":
        if len(program_args) != 6:
            raise ValueError(
                "fomo_trade.py requires arguments: <TICKER> <NUM_STOCKS> <ENTRY_PRICE> <STOP_PRICE> <TARGET1_PRICE> <TARGET2_PRICE>"
            )
        symbol = program_args[0].upper()
        try:
            qty = int(program_args[1])
        except ValueError as exc:
            raise ValueError("NUM_STOCKS must be an integer for fomo_trade.py") from exc
        if qty <= 0:
            raise ValueError("NUM_STOCKS must be greater than 0 for fomo_trade.py")
        if qty % 2 != 0:
            raise ValueError("NUM_STOCKS must be even for fomo_trade.py strategy logic")

        entry = _parse_float(program_args[2], "ENTRY_PRICE")
        stop = _parse_float(program_args[3], "STOP_PRICE")
        target1 = _parse_float(program_args[4], "TARGET1_PRICE")
        target2 = _parse_float(program_args[5], "TARGET2_PRICE")

        if stop >= entry:
            raise ValueError("STOP_PRICE must be lower than ENTRY_PRICE for long trades")
        if target1 <= entry:
            raise ValueError("TARGET1_PRICE must be greater than ENTRY_PRICE")
        if target2 <= target1:
            raise ValueError("TARGET2_PRICE must be greater than TARGET1_PRICE")

        return StrategyConfig(
            program_name=program_name,
            symbol=symbol,
            qty=qty,
            mode="fixed_levels",
            fixed_entry=entry,
            fixed_stop=round(stop, 2),
            fixed_target1=round(target1, 2),
            fixed_target2=round(target2, 2),
        )

    raise ValueError(
        f"Unsupported PROGRAM_FILE '{program_file}'. Supported: previous_close.py, fomo_trade.py"
    )


def _parse_args() -> tuple[int, StrategyConfig]:
    if len(sys.argv) < 4:
        _print_usage()
        raise SystemExit(1)

    try:
        days_back = int(sys.argv[1])
    except ValueError:
        print("Error: first argument DAYS_BACK must be an integer.")
        _print_usage()
        raise SystemExit(1)

    if days_back <= 0:
        print("Error: DAYS_BACK must be greater than 0.")
        _print_usage()
        raise SystemExit(1)

    program_file = sys.argv[2]
    program_args = sys.argv[3:]

    try:
        config = _parse_strategy_config(program_file, program_args)
    except ValueError as exc:
        print(f"Error: {exc}")
        _print_usage()
        raise SystemExit(1)

    return days_back, config


def _compute_day_levels(config: StrategyConfig, previous_close: float) -> tuple[float, float, float, float]:
    if config.mode == "previous_close":
        assert config.stop_pct is not None
        assert config.target1_pct is not None
        assert config.target2_pct is not None
        entry = float(math.ceil(previous_close))
        stop = round(entry * (1.0 - config.stop_pct), 2)
        target1 = round(entry * (1.0 + config.target1_pct), 2)
        target2 = round(entry * (1.0 + config.target2_pct), 2)
        return entry, stop, target1, target2

    assert config.fixed_entry is not None
    assert config.fixed_stop is not None
    assert config.fixed_target1 is not None
    assert config.fixed_target2 is not None
    return config.fixed_entry, config.fixed_stop, config.fixed_target1, config.fixed_target2


def main() -> int:
    days, config = _parse_args()
    symbol = config.symbol

    credentials, _ = bootstrap_trading_auth("backtest.py")
    client = StockHistoricalDataClient(credentials.api_key, credentials.secret_key)

    now_utc = datetime.now(timezone.utc)
    daily_req = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Day,
        start=now_utc - timedelta(days=max(days * 3, 260)),
        end=now_utc,
        limit=max(days * 4, 300),
        feed=DataFeed.IEX,
    )
    daily_bars = _extract_symbol_bars(client.get_stock_bars(daily_req), symbol)
    if len(daily_bars) < days + 1:
        raise RuntimeError(f"Need at least {days + 1} daily bars for {symbol}, got {len(daily_bars)}")

    daily_bars = sorted(daily_bars, key=lambda b: _bar_value(b, "timestamp"))
    window = daily_bars[-(days + 1):]

    results: list[DayResult] = []

    for i in range(1, len(window)):
        prev_bar = window[i - 1]
        day_bar = window[i]

        prev_close = float(_bar_value(prev_bar, "close"))
        day_ts = _bar_value(day_bar, "timestamp")
        day_date = day_ts.astimezone(ET).date()
        day_open = float(_bar_value(day_bar, "open"))

        entry, stop, target1, target2 = _compute_day_levels(config, prev_close)

        if config.require_open_above_prev_close and day_open <= prev_close:
            results.append(
                DayResult(str(day_date), entry, stop, target1, target2, False, "open_not_above_prev_close", 0.0)
            )
            continue

        day_start_et = datetime(day_date.year, day_date.month, day_date.day, tzinfo=ET)
        day_end_et = day_start_et + timedelta(days=1)

        minute_req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Minute,
            start=day_start_et.astimezone(timezone.utc),
            end=day_end_et.astimezone(timezone.utc),
            limit=10000,
            feed=DataFeed.IEX,
        )
        minute_bars = _extract_symbol_bars(client.get_stock_bars(minute_req), symbol)
        minute_bars = sorted(minute_bars, key=lambda b: _bar_value(b, "timestamp"))

        if not minute_bars:
            results.append(
                DayResult(str(day_date), entry, stop, target1, target2, False, "no_intraday_data", 0.0)
            )
            continue

        entered, outcome, pnl = _simulate_day(minute_bars, config.qty, entry, stop, target1, target2)
        results.append(DayResult(str(day_date), entry, stop, target1, target2, entered, outcome, pnl))

    total_pnl = round(sum(r.pnl for r in results), 2)
    gross_notional = round(sum(r.entry * config.qty for r in results if r.entered), 2)
    return_pct_on_traded_capital = (total_pnl / gross_notional * 100.0) if gross_notional else 0.0

    entered_days = sum(1 for r in results if r.entered)
    win_outcomes = {"t1_then_t2", "t1_then_breakeven", "t1_then_eod"}
    loss_outcomes = {"stopped_before_t1"}

    wins = sum(1 for r in results if r.outcome in win_outcomes)
    losses = sum(1 for r in results if r.outcome in loss_outcomes)
    flats = len(results) - wins - losses
    outcomes = Counter(r.outcome for r in results)

    print(f"Symbol: {symbol}")
    print(f"Program adapter: {config.program_name}")
    print(f"Days tested: {len(results)}")
    print(f"Entered days: {entered_days}")
    if config.mode == "previous_close":
        print(
            f"Config: stop={config.stop_pct*100:.2f}% target1={config.target1_pct*100:.2f}% "
            f"target2={config.target2_pct*100:.2f}% qty={config.qty}"
        )
        print("Gate: enter only when today's open > previous close")
    else:
        print(
            f"Config: fixed entry={config.fixed_entry:.2f} stop={config.fixed_stop:.2f} "
            f"target1={config.fixed_target1:.2f} target2={config.fixed_target2:.2f} qty={config.qty}"
        )
    print(f"Wins/Losses/Flat: {wins}/{losses}/{flats}")
    print(f"Total PnL ($): {total_pnl:.2f}")
    print(f"Return on traded notional (%): {return_pct_on_traded_capital:.2f}")
    print("Outcomes:")
    for k, v in sorted(outcomes.items()):
        print(f"  {k}: {v}")

    print("\nLast 10 sessions:")
    for r in results[-10:]:
        print(f"  {r.date} | entry={r.entry:.2f} | {r.outcome} | pnl={r.pnl:.2f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())