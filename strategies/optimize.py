from __future__ import annotations

import itertools
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


MAX_COMBINATIONS = 250000


WINS_LOSSES_FLAT_RE = re.compile(r"Wins/Losses/Flat:\s*(\d+)\s*/\s*(\d+)\s*/\s*(\d+)")
ENTERED_DAYS_RE = re.compile(r"Entered days:\s*(\d+)")
TOTAL_PNL_RE = re.compile(r"Total PnL \(\$\):\s*([-+]?\d+(?:\.\d+)?)")
RETURN_PCT_RE = re.compile(r"Return on traded notional \(%\):\s*([-+]?\d+(?:\.\d+)?)")


@dataclass
class BacktestResult:
    days: int
    ticker: str
    qty: int
    entry: float
    stop: float
    target1: float
    target2: float
    wins: int
    losses: int
    flats: int
    entered_days: int
    total_pnl: float
    return_pct: float
    win_pct: float


def _print_usage() -> None:
    print(
        "Usage: python optimize.py <DAYS_BACK 1-100> <TICKER> <NUM_STOCKS> "
        "<ENTRY_PRICE-RANGE> <STOP_PRICE-RANGE> <TARGET1_PRICE-RANGE> <TARGET2_PRICE-RANGE>"
    )
    print("Example: python optimize.py 50 MU 100 850-5 800-5 860-5 880-5")
    print("Range format is BASE-RANGE using whole numbers.")
    print("For example 850-5 means values 845 to 855.")


def _parse_range_values(raw: str, arg_name: str) -> list[int]:
    match = re.fullmatch(r"(\d+)-(\d+)", raw.strip())
    if not match:
        raise ValueError(f"{arg_name} must use whole-number BASE-RANGE format, e.g. 850-5")

    base = int(match.group(1))
    spread = int(match.group(2))
    if base <= 0:
        raise ValueError(f"{arg_name} base must be greater than 0")

    low = max(base - spread, 1)
    high = base + spread
    return list(range(low, high + 1))


def _parse_args() -> tuple[int, str, int, list[int], list[int], list[int], list[int]]:
    if len(sys.argv) != 8:
        _print_usage()
        raise SystemExit(1)

    try:
        days_back = int(sys.argv[1])
    except ValueError:
        print("Error: DAYS_BACK must be an integer.")
        _print_usage()
        raise SystemExit(1)

    if days_back < 1 or days_back > 100:
        print("Error: DAYS_BACK must be between 1 and 100.")
        _print_usage()
        raise SystemExit(1)

    ticker = sys.argv[2].upper().strip()
    if not ticker:
        print("Error: TICKER cannot be empty.")
        _print_usage()
        raise SystemExit(1)

    try:
        qty = int(sys.argv[3])
    except ValueError:
        print("Error: NUM_STOCKS must be an integer.")
        _print_usage()
        raise SystemExit(1)

    if qty <= 0:
        print("Error: NUM_STOCKS must be greater than 0.")
        _print_usage()
        raise SystemExit(1)

    try:
        entry_values = _parse_range_values(sys.argv[4], "ENTRY_PRICE-RANGE")
        stop_values = _parse_range_values(sys.argv[5], "STOP_PRICE-RANGE")
        target1_values = _parse_range_values(sys.argv[6], "TARGET1_PRICE-RANGE")
        target2_values = _parse_range_values(sys.argv[7], "TARGET2_PRICE-RANGE")
    except ValueError as exc:
        print(f"Error: {exc}")
        _print_usage()
        raise SystemExit(1)

    raw_combinations = len(entry_values) * len(stop_values) * len(target1_values) * len(target2_values)
    if raw_combinations > MAX_COMBINATIONS:
        print(
            f"Error: range grid is too large ({raw_combinations} combinations). "
            f"Reduce spreads to keep combinations <= {MAX_COMBINATIONS}."
        )
        _print_usage()
        raise SystemExit(1)

    return days_back, ticker, qty, entry_values, stop_values, target1_values, target2_values


def _build_candidates(
    entry_values: list[int],
    stop_values: list[int],
    target1_values: list[int],
    target2_values: list[int],
) -> list[tuple[float, float, float, float]]:
    candidates: list[tuple[float, float, float, float]] = []
    for entry, stop, target1, target2 in itertools.product(
        entry_values, stop_values, target1_values, target2_values
    ):
        if stop >= entry or target1 <= entry or target2 <= target1:
            continue
        candidates.append((float(entry), float(stop), float(target1), float(target2)))

    # Remove duplicates after rounding.
    return sorted(set(candidates))


def _run_backtest(days: int, ticker: str, qty: int, entry: float, stop: float, target1: float, target2: float) -> BacktestResult | None:
    backtest_path = Path(__file__).resolve().parent.parent / "indicator" / "backtest.py"
    cmd = [
        sys.executable,
        str(backtest_path),
        str(days),
        "fomo_trade.py",
        ticker,
        str(qty),
        f"{entry:.2f}",
        f"{stop:.2f}",
        f"{target1:.2f}",
        f"{target2:.2f}",
    ]

    completed = subprocess.run(cmd, capture_output=True, text=True)
    if completed.returncode != 0:
        return None

    output = completed.stdout

    wlf_match = WINS_LOSSES_FLAT_RE.search(output)
    entered_match = ENTERED_DAYS_RE.search(output)
    pnl_match = TOTAL_PNL_RE.search(output)
    ret_match = RETURN_PCT_RE.search(output)
    if not (wlf_match and entered_match and pnl_match and ret_match):
        return None

    wins = int(wlf_match.group(1))
    losses = int(wlf_match.group(2))
    flats = int(wlf_match.group(3))
    entered_days = int(entered_match.group(1))
    total_pnl = float(pnl_match.group(1))
    return_pct = float(ret_match.group(1))

    denominator = wins + losses
    win_pct = (wins / denominator * 100.0) if denominator > 0 else 0.0

    return BacktestResult(
        days=days,
        ticker=ticker,
        qty=qty,
        entry=entry,
        stop=stop,
        target1=target1,
        target2=target2,
        wins=wins,
        losses=losses,
        flats=flats,
        entered_days=entered_days,
        total_pnl=total_pnl,
        return_pct=return_pct,
        win_pct=win_pct,
    )


def main() -> int:
    days_back, ticker, qty, entry_values, stop_values, target1_values, target2_values = _parse_args()

    print(f"Ticker: {ticker}")
    print(f"Days back: {days_back}")
    print(f"Quantity: {qty}")
    print(f"Entry values: {entry_values[0]} to {entry_values[-1]} ({len(entry_values)} values)")
    print(f"Stop values: {stop_values[0]} to {stop_values[-1]} ({len(stop_values)} values)")
    print(f"Target1 values: {target1_values[0]} to {target1_values[-1]} ({len(target1_values)} values)")
    print(f"Target2 values: {target2_values[0]} to {target2_values[-1]} ({len(target2_values)} values)")

    candidates = _build_candidates(entry_values, stop_values, target1_values, target2_values)
    print(f"Testing {len(candidates)} parameter combinations...")

    results: list[BacktestResult] = []
    for i, (entry, stop, target1, target2) in enumerate(candidates, start=1):
        result = _run_backtest(days_back, ticker, qty, entry, stop, target1, target2)
        if result is not None:
            results.append(result)
        if i % 25 == 0:
            print(f"  Progress: {i}/{len(candidates)} combinations checked")

    if not results:
        print("No successful backtest results were parsed.")
        return 1

    # Rank by win%, then total pnl, then return%, then entered days.
    results.sort(key=lambda r: (r.win_pct, r.total_pnl, r.return_pct, r.entered_days), reverse=True)
    best = results[0]

    print("\nBest configuration")
    print(f"  Entry:    {best.entry:.2f}")
    print(f"  Stop:     {best.stop:.2f}")
    print(f"  Target 1: {best.target1:.2f}")
    print(f"  Target 2: {best.target2:.2f}")
    print(f"  Wins/Losses/Flats: {best.wins}/{best.losses}/{best.flats}")
    print(f"  Entered days: {best.entered_days}")
    print(f"  Win %: {best.win_pct:.2f}")
    print(f"  Total PnL ($): {best.total_pnl:.2f}")
    print(f"  Return on traded notional (%): {best.return_pct:.2f}")

    print("\nTop 5 configurations")
    for rank, r in enumerate(results[:5], start=1):
        print(
            f"  {rank}. E={r.entry:.2f} S={r.stop:.2f} T1={r.target1:.2f} T2={r.target2:.2f} "
            f"| W/L/F={r.wins}/{r.losses}/{r.flats} | Entered={r.entered_days} "
            f"| Win%={r.win_pct:.2f} | PnL=${r.total_pnl:.2f} | Ret%={r.return_pct:.2f}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())