"""Empirical intraday high/low estimates from Alpaca daily bars.

The live prediction uses today's regular-session open and only completed
historical sessions. It never submits, cancels, or modifies an order.
"""

from __future__ import annotations

import argparse
import csv
import logging
import math
import os
import random
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from statistics import mean, median, stdev
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from alpaca.common.exceptions import APIError
from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from dotenv import load_dotenv


SYMBOL = "SPY"
LOOKBACK_DAYS = 100
MARKET_TIMEZONE = "America/New_York"
OUTPUT_FILE = "ohlc_prediction.csv"
RECENT_WEIGHTING = True
REGIME_ADJUSTMENT = True
MONTE_CARLO_RUNS = 10_000
ET = ZoneInfo(MARKET_TIMEZONE)
HIGH_LEVELS = (0.0025, 0.005, 0.0075, 0.01, 0.0125, 0.015, 0.02, 0.025, 0.03)
LOW_LEVELS = tuple(-value for value in HIGH_LEVELS)
PERCENTILES = (0.10, 0.25, 0.50, 0.75, 0.90)
LOGGER = logging.getLogger("day_trading")


@dataclass(frozen=True)
class Config:
    symbol: str
    output_file: Path
    backtest_date: date | None = None
    backtest_days: int = 1
    monte_carlo: bool = False


def load_config(argv: list[str]) -> Config:
    parser = argparse.ArgumentParser(description="Alpaca OHLC probability predictor")
    parser.add_argument("symbol", nargs="?", default=SYMBOL, help="Ticker symbol")
    parser.add_argument("--output", default=OUTPUT_FILE)
    parser.add_argument("--backtest-date", type=date.fromisoformat, help="YYYY-MM-DD")
    parser.add_argument("--backtest-days", type=int, default=1)
    parser.add_argument("--monte-carlo", action="store_true")
    args = parser.parse_args(argv)
    symbol = args.symbol.strip().upper()
    if not symbol or not symbol.replace(".", "").isalnum():
        raise ValueError("Invalid ticker symbol")
    if args.backtest_days < 1:
        raise ValueError("--backtest-days must be at least 1")
    return Config(symbol, Path(args.output), args.backtest_date, args.backtest_days, args.monte_carlo)


def create_alpaca_client() -> StockHistoricalDataClient:
    """Load credentials from env/credentials and create a data-only client."""
    root = Path(__file__).resolve().parent.parent
    credentials_file = root / "env" / "credentials"
    if credentials_file.exists():
        load_dotenv(credentials_file, override=False)
    api_key = (os.getenv("APCA_API_KEY_ID") or os.getenv("ALPACA_API_KEY") or "").strip()
    secret_key = (
        os.getenv("APCA_API_SECRET_KEY")
        or os.getenv("ALPACA_API_SECRET")
        or os.getenv("ALPACA_SECRET_KEY")
        or ""
    ).strip()
    if not api_key or not secret_key:
        raise RuntimeError("Missing Alpaca credentials in env/credentials")
    return StockHistoricalDataClient(api_key, secret_key)


def _response_bars(response: Any, symbol: str) -> list[Any]:
    data = getattr(response, "data", response)
    if isinstance(data, dict):
        bars = data.get(symbol) or data.get(symbol.upper())
        if bars is None and data:
            bars = next(iter(data.values()))
        return list(bars or [])
    return list(data or [])


def _value(bar: Any, name: str) -> Any:
    value = getattr(bar, name, None)
    if value is None and isinstance(bar, dict):
        value = bar.get(name)
    return value


def _bar_date(bar: Any) -> date:
    timestamp = _value(bar, "timestamp")
    if isinstance(timestamp, datetime):
        return timestamp.astimezone(ET).date() if timestamp.tzinfo else timestamp.date()
    return datetime.fromisoformat(str(timestamp).replace("Z", "+00:00")).astimezone(ET).date()


def validate_data(bar: Any) -> dict[str, Any] | None:
    try:
        values = {name: float(_value(bar, name)) for name in ("open", "high", "low", "close", "volume")}
        if not all(math.isfinite(value) for value in values.values()) or values["open"] <= 0:
            return None
        if not (
            values["high"] >= values["open"] >= values["low"]
            and values["high"] >= values["low"]
            and values["low"] <= values["close"] <= values["high"]
        ):
            return None
        values["date"] = _bar_date(bar)
        return values
    except (TypeError, ValueError, KeyError):
        return None


def get_bars(client: StockHistoricalDataClient, symbol: str, end_date: date, count: int) -> list[dict[str, Any]]:
    start = end_date - timedelta(days=max(180, count * 3))
    end = datetime.combine(end_date, time.min, tzinfo=ET).astimezone(timezone.utc)
    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Day,
        start=datetime.combine(start, time.min, tzinfo=ET).astimezone(timezone.utc),
        end=end,
        limit=1000,
        feed=DataFeed.IEX,
    )
    bars = []
    for raw_bar in _response_bars(client.get_stock_bars(request), symbol):
        if _bar_date(raw_bar) < end_date:
            clean = validate_data(raw_bar)
            if clean is not None:
                bars.append(clean)
    unique = {item["date"]: item for item in bars}
    result = sorted(unique.values(), key=lambda item: item["date"])
    if len(result) < count:
        raise RuntimeError(f"Only {len(result)} valid completed trading days were returned; need {count}")
    return result[-count:]


def get_today_open(client: StockHistoricalDataClient, symbol: str) -> tuple[date, float]:
    now = datetime.now(timezone.utc).astimezone(ET)
    start = datetime.combine(
        now.date() - timedelta(days=7), time.min, tzinfo=ET
    ).astimezone(timezone.utc)
    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Day,
        start=start,
        end=datetime.now(timezone.utc),
        feed=DataFeed.IEX,
    )
    candidates = []
    for raw_bar in _response_bars(client.get_stock_bars(request), symbol):
        clean = validate_data(raw_bar)
        if clean is not None and clean["date"] <= now.date():
            candidates.append(clean)
    if not candidates:
        raise RuntimeError(
            "No recent regular-session Open is available; the symbol may be invalid "
            "or Alpaca market data may be unavailable."
        )
    session = max(candidates, key=lambda bar: bar["date"])
    if session["date"] != now.date():
        LOGGER.warning(
            "No Open for %s yet; using the latest completed regular session (%s)",
            now.date(), session["date"],
        )
    return session["date"], session["open"]


def calculate_features(bars: list[dict[str, Any]]) -> list[dict[str, float]]:
    features: list[dict[str, float]] = []
    previous_close: float | None = None
    for bar in bars:
        opening, high, low, close = bar["open"], bar["high"], bar["low"], bar["close"]
        true_range = high - low if previous_close is None else max(high - low, abs(high - previous_close), abs(low - previous_close))
        features.append({
            "high_return": high / opening - 1,
            "low_return": low / opening - 1,
            "close_return": close / opening - 1,
            "daily_range": (high - low) / opening,
            "body_return": (close - opening) / opening,
            "true_range": true_range,
            "close": close,
            "open": opening,
        })
        previous_close = close
    for index, item in enumerate(features):
        item["atr"] = mean(row["true_range"] for row in features[max(0, index - 13): index + 1])
        returns = [row["close_return"] for row in features[max(0, index - 19): index + 1]]
        item["volatility"] = stdev(returns) if len(returns) > 1 else 0.0
    return features


def _weights(size: int) -> list[float]:
    if not RECENT_WEIGHTING:
        return [1.0] * size
    return [math.exp(0.02 * index) for index in range(size)]


def weighted_quantile(values: list[float], quantile: float, weights: list[float]) -> float:
    pairs = sorted(zip(values, weights), key=lambda pair: pair[0])
    cutoff = quantile * sum(weight for _, weight in pairs)
    running = 0.0
    for value, weight in pairs:
        running += weight
        if running >= cutoff:
            return value
    return pairs[-1][0]


def distribution(features: list[dict[str, float]], key: str) -> tuple[list[float], list[float]]:
    values = [item[key] for item in features]
    weights = _weights(len(values))
    if REGIME_ADJUSTMENT:
        recent_vol = mean(item["volatility"] for item in features[-20:])
        full_vol = mean(item["volatility"] for item in features)
        if full_vol > 0 and recent_vol > 0:
            scale = min(1.25, max(0.80, math.sqrt(recent_vol / full_vol)))
            centre = weighted_quantile(values, 0.50, weights)
            values = [centre + (value - centre) * scale for value in values]
    return values, weights


def calculate_statistics(features: list[dict[str, float]]) -> dict[str, Any]:
    high_values, weights = distribution(features, "high_return")
    low_values, _ = distribution(features, "low_return")
    result: dict[str, Any] = {"high_values": high_values, "low_values": low_values, "weights": weights}
    for period in (20, 50, 100):
        subset = features[-period:]
        result[period] = {
            "high_mean": mean(item["high_return"] for item in subset),
            "high_median": median(item["high_return"] for item in subset),
            "low_mean": mean(item["low_return"] for item in subset),
            "low_median": median(item["low_return"] for item in subset),
            "volatility": mean(item["volatility"] for item in subset),
            "atr": mean(item["atr"] for item in subset),
        }
    return result


def predict(open_price: float, statistics: dict[str, Any]) -> dict[str, Any]:
    high, low, weights = statistics["high_values"], statistics["low_values"], statistics["weights"]
    high_percentiles = {p: weighted_quantile(high, p, weights) for p in PERCENTILES}
    low_percentiles = {p: weighted_quantile(low, p, weights) for p in PERCENTILES}
    return {
        "expected_high": open_price * (1 + high_percentiles[0.50]),
        "expected_low": open_price * (1 + low_percentiles[0.50]),
        "high_percentiles": {p: open_price * (1 + value) for p, value in high_percentiles.items()},
        "low_percentiles": {p: open_price * (1 + value) for p, value in low_percentiles.items()},
        "high_probabilities": {level: sum(w for value, w in zip(high, weights) if value >= level) / sum(weights) for level in HIGH_LEVELS},
        "low_probabilities": {level: sum(w for value, w in zip(low, weights) if value <= level) / sum(weights) for level in LOW_LEVELS},
    }


def run_monte_carlo(open_price: float, statistics: dict[str, Any], runs: int = MONTE_CARLO_RUNS) -> dict[str, dict[float, float]]:
    high, low, weights = statistics["high_values"], statistics["low_values"], statistics["weights"]
    high_draws = random.choices(high, weights=weights, k=runs)
    low_draws = random.choices(low, weights=weights, k=runs)
    return {
        "high": {level: sum(value >= level for value in high_draws) / runs for level in HIGH_LEVELS},
        "low": {level: sum(value <= level for value in low_draws) / runs for level in LOW_LEVELS},
    }


def _money(value: float) -> str:
    return f"${value:,.2f}"


def print_results(symbol: str, session_date: date, open_price: float, bars: list[dict[str, Any]], statistics: dict[str, Any], prediction: dict[str, Any], monte_carlo: dict[str, dict[float, float]] | None = None) -> None:
    print("=" * 60)
    print("ALPACA OHLC PROBABILITY PREDICTOR")
    print("=" * 60)
    print(f"Symbol: {symbol}\nDate: {session_date}\nHistorical Trading Days: {len(bars)}\nToday's Open: {_money(open_price)}")
    print("\nPREDICTED TODAY'S RANGE")
    print(f"Expected High: { _money(prediction['expected_high'])}\nExpected Low : { _money(prediction['expected_low'])}")
    print("\nPercentile High / Low (price levels)")
    for percentile in (0.10, 0.25, 0.50, 0.75, 0.90):
        print(f"{percentile:.0%}: {_money(prediction['high_percentiles'][percentile])} / {_money(prediction['low_percentiles'][percentile])}")
    print("\nPROBABILITY OF TODAY'S HIGH REACHING LEVEL")
    print("Target       Probability")
    for level in HIGH_LEVELS:
        print(f"+{level:.2%}       {prediction['high_probabilities'][level]:.1%}")
    print("\nPROBABILITY OF TODAY'S LOW REACHING LEVEL")
    print("Target       Probability")
    for level in LOW_LEVELS:
        print(f"{level:.2%}       {prediction['low_probabilities'][level]:.1%}")
    print("\nHISTORICAL STATISTICS")
    for period in (20, 50, 100):
        stats = statistics[period]
        print(f"{period}-Day Median High/Open: {stats['high_median']:.2%} | Median Low/Open: {stats['low_median']:.2%} | Volatility: {stats['volatility']:.2%}")
    print(f"14-Day ATR: {_money(statistics[100]['atr'])}")
    if monte_carlo:
        print("\nMONTE CARLO VS EMPIRICAL (10,000 draws)")
        for level in HIGH_LEVELS:
            print(f"High +{level:.2%}: empirical {prediction['high_probabilities'][level]:.1%}, Monte Carlo {monte_carlo['high'][level]:.1%}")
    print("\nProbabilities are empirical estimates from historical behavior, not guarantees.")


def save_results(config: Config, session_date: date, open_price: float, statistics: dict[str, Any], prediction: dict[str, Any]) -> None:
    row: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(), "symbol": config.symbol, "today_open": open_price,
        "expected_high": prediction["expected_high"], "expected_low": prediction["expected_low"],
        "high_p25": prediction["high_percentiles"][0.25], "high_p50": prediction["high_percentiles"][0.50], "high_p75": prediction["high_percentiles"][0.75], "high_p90": prediction["high_percentiles"][0.90],
        "low_p10": prediction["low_percentiles"][0.10], "low_p25": prediction["low_percentiles"][0.25], "low_p50": prediction["low_percentiles"][0.50], "low_p75": prediction["low_percentiles"][0.75],
        "atr": statistics[100]["atr"], "volatility_20": statistics[20]["volatility"], "volatility_100": statistics[100]["volatility"],
    }
    for level in HIGH_LEVELS:
        row[f"high_probability_{level * 100:.2f}"] = prediction["high_probabilities"][level]
    for level in LOW_LEVELS:
        row[f"low_probability_{abs(level) * 100:.2f}"] = prediction["low_probabilities"][level]
    with config.output_file.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
    LOGGER.info("Saved prediction for %s to %s", session_date, config.output_file)


def run_backtest(client: StockHistoricalDataClient, config: Config) -> None:
    target_date = config.backtest_date or datetime.now(ET).date()
    all_bars = get_bars(client, config.symbol, target_date + timedelta(days=1), LOOKBACK_DAYS + config.backtest_days)
    results = []
    for index in range(LOOKBACK_DAYS, len(all_bars)):
        history = all_bars[index - LOOKBACK_DAYS:index]
        target = all_bars[index]
        stats = calculate_statistics(calculate_features(history))
        estimate = predict(target["open"], stats)
        actual_high_return = target["high"] / target["open"] - 1
        actual_low_return = target["low"] / target["open"] - 1
        in_50 = prediction_in_range(target["high"], target["low"], estimate, 0.25, 0.75)
        in_75 = prediction_in_range(target["high"], target["low"], estimate, 0.10, 0.90)
        results.append((abs(estimate["expected_high"] - target["high"]), abs(estimate["expected_low"] - target["low"]), in_50, in_75))
    print(f"Backtest: {config.symbol}, {len(results)} sessions, using only the prior {LOOKBACK_DAYS} sessions")
    print(f"High MAE: {_money(mean(item[0] for item in results))} | Low MAE: {_money(mean(item[1] for item in results))}")
    print(f"50% central range coverage: {mean(item[2] for item in results):.1%} | 80% range coverage: {mean(item[3] for item in results):.1%}")


def prediction_in_range(actual_high: float, actual_low: float, prediction: dict[str, Any], low_percentile: float, high_percentile: float) -> bool:
    high_in_range = prediction["high_percentiles"][low_percentile] <= actual_high <= prediction["high_percentiles"][high_percentile]
    low_in_range = prediction["low_percentiles"][low_percentile] <= actual_low <= prediction["low_percentiles"][high_percentile]
    return high_in_range and low_in_range


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        config = load_config(sys.argv[1:])
        client = create_alpaca_client()
        if config.backtest_date:
            run_backtest(client, config)
            return 0
        session_date, open_price = get_today_open(client, config.symbol)
        bars = get_bars(client, config.symbol, session_date, LOOKBACK_DAYS)
        statistics = calculate_statistics(calculate_features(bars))
        prediction = predict(open_price, statistics)
        monte_carlo = run_monte_carlo(open_price, statistics) if config.monte_carlo else None
        print_results(config.symbol, session_date, open_price, bars, statistics, prediction, monte_carlo)
        return 0
    except (APIError, OSError, RuntimeError, ValueError) as exc:
        LOGGER.error("%s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())