#!/usr/bin/env python3
"""Find the newest 2x-range displacement candle and its origin zone.

Usage:
    python indicator/zone_finder.py <TICKER>
"""

from __future__ import annotations

import sys
from pathlib import Path
from zoneinfo import ZoneInfo

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from alpaca.data.historical import StockHistoricalDataClient

from indicator.plot_graph import build_volume_candles, fetch_raw_data
from indicator.vol_finder import calculate_average_daily_volume, calculate_volume_per_candle
from roles.credentials import bootstrap_trading_auth


LOOKBACK_CANDLES = 500
DISPLACEMENT_MULTIPLE = 2.0
ET = ZoneInfo("America/New_York")


def _format_timestamp(timestamp) -> str:
    if timestamp is None:
        return "N/A"
    if getattr(timestamp, "tzinfo", None) is not None:
        timestamp = timestamp.astimezone(ET)
    return timestamp.strftime("%Y-%m-%d %H:%M:%S %Z")


def find_latest_zone(candles):
    if len(candles) < LOOKBACK_CANDLES:
        raise RuntimeError(
            f"Need {LOOKBACK_CANDLES} completed volume candles, got {len(candles)}"
        )

    candles = candles.tail(LOOKBACK_CANDLES).reset_index(drop=True).copy()
    candles["range"] = candles["high"] - candles["low"]
    average_range = float(candles["range"].mean())
    if average_range <= 0:
        raise RuntimeError("Average candle range is zero")

    for index in range(len(candles) - 1, 0, -1):
        signal = candles.iloc[index]
        signal_range = float(signal["range"])
        if signal_range < DISPLACEMENT_MULTIPLE * average_range:
            continue

        origin = candles.iloc[index - 1]
        bullish = float(signal["close"]) >= float(signal["open"])
        zone_low = float(origin["low"])
        zone_high = float(origin["high"])
        return {
            "average_range": average_range,
            "signal": signal,
            "signal_range": signal_range,
            "multiple": signal_range / average_range,
            "origin": origin,
            "zone_type": "DEMAND" if bullish else "SUPPLY",
            "zone_low": zone_low,
            "zone_high": zone_high,
            "entry_level": zone_high if bullish else zone_low,
            "stop_level": zone_low if bullish else zone_high,
        }

    return None


def main() -> int:
    if len(sys.argv) != 2 or not sys.argv[1].strip():
        print("Usage: python indicator/zone_finder.py <TICKER>")
        return 1

    symbol = sys.argv[1].strip().upper()

    try:
        credentials, _ = bootstrap_trading_auth("zone_finder.py")
        data_client = StockHistoricalDataClient(credentials.api_key, credentials.secret_key)
        average_daily_volume, _ = calculate_average_daily_volume(data_client, symbol)
        volume_per_candle = calculate_volume_per_candle(average_daily_volume)
        raw_data = fetch_raw_data(
            data_client,
            symbol,
            LOOKBACK_CANDLES,
            volume_per_candle,
        )
        candles = build_volume_candles(raw_data, volume_per_candle, LOOKBACK_CANDLES)
        zone = find_latest_zone(candles)
    except SystemExit:
        return 1
    except Exception as exc:
        print(f"Could not find zone for {symbol}: {exc}")
        return 1

    if zone is None:
        print(
            f"No candle >= {DISPLACEMENT_MULTIPLE:.1f}x the average price range "
            f"in the last {LOOKBACK_CANDLES} completed candles for {symbol}."
        )
        return 0

    signal = zone["signal"]
    origin = zone["origin"]
    print(f"\n{symbol} {zone['zone_type']} ZONE")
    print(f"1% volume candle: {volume_per_candle:,} shares")
    print(f"500-candle average range: ${zone['average_range']:.4f}")
    print(
        f"2x candle: {_format_timestamp(signal['timestamp'])} | "
        f"range=${zone['signal_range']:.4f} ({zone['multiple']:.2f}x)"
    )
    print(f"Origin candle: {_format_timestamp(origin['timestamp'])}")
    print(f"Zone low: ${zone['zone_low']:.4f}")
    print(f"Zone high: ${zone['zone_high']:.4f}")
    print(f"Entry level: ${zone['entry_level']:.4f}")
    print(f"Stop level: ${zone['stop_level']:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
