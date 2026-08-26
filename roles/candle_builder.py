"""Shared volume candle building utilities for trading strategies."""

from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Any
import pandas as pd


@dataclass(frozen=True)
class VolumeCandle:
    """A candle built from fixed volume accumulation."""
    timestamp: Any
    open: float
    high: float
    low: float
    close: float
    volume: float


def build_volume_candles_from_bars(df: pd.DataFrame, volume_target: float, max_candles: int) -> pd.DataFrame:
    """Aggregates 1-minute bars into custom candles based on a fixed volume threshold.
    
    Args:
        df: DataFrame with columns: timestamp, open, high, low, close, volume
        volume_target: Target volume per candle
        max_candles: Maximum number of candles to return
        
    Returns:
        DataFrame with volume candles (open, high, low, close, volume, timestamp)
    """
    volume_candles = []
    current_candle = None

    # Walk through chronological 1-minute data
    for _, row in df.iterrows():
        vol = row["volume"]
        close_p = row["close"]
        high_p = row["high"]
        low_p = row["low"]
        open_p = row["open"]
        time_p = row["timestamp"]

        if current_candle is None:
            current_candle = {
                "open": open_p,
                "high": high_p,
                "low": low_p,
                "close": close_p,
                "volume": 0,
                "timestamp": time_p,
            }

        # Update high and low for current candle tracking
        current_candle["high"] = max(current_candle["high"], high_p)
        current_candle["low"] = min(current_candle["low"], low_p)
        current_candle["close"] = close_p

        # Accumulate volume
        remaining_needed = volume_target - current_candle["volume"]

        if vol >= remaining_needed:
            # This minute bar fulfills the volume candle target
            current_candle["volume"] = volume_target
            volume_candles.append(current_candle)

            # Reset for next candle
            current_candle = None
        else:
            # Just add the volume and keep going
            current_candle["volume"] += vol

    # Convert aggregated candles to DataFrame and take the most recent ones
    candles_df = pd.DataFrame(volume_candles)
    if candles_df.empty:
        return pd.DataFrame()

    return candles_df.tail(max_candles).reset_index(drop=True)
