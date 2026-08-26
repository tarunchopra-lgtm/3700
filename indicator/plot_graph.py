import argparse
from datetime import datetime, timedelta, timezone
import math
import re
import sys
import matplotlib.pyplot as plt
import pandas as pd
from alpaca.data.enums import DataFeed, OptionsFeed
from alpaca.data.historical import StockHistoricalDataClient, OptionHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, OptionBarsRequest
from alpaca.data.timeframe import TimeFrame
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from roles.credentials import bootstrap_trading_auth


OPTION_SYMBOL_PATTERN = re.compile(r"^[A-Z]{1,6}\d{6}[CP]\d{8}$")


def is_option_symbol(symbol: str) -> bool:
    return bool(OPTION_SYMBOL_PATTERN.match(symbol.upper()))


def parse_arguments():
    """Parses command line arguments for ticker, volume per candle, and count."""
    parser = argparse.ArgumentParser(
        description="Draw a volume-based candle chart using Alpaca data."
    )
    parser.add_argument(
        "--ticker",
        type=str,
        required=True,
        help="Stock ticker symbol (e.g., AAPL)",
    )
    parser.add_argument(
        "--volume",
        type=int,
        required=True,
        help="Fixed volume amount per candle (e.g., 10000)",
    )
    parser.add_argument(
        "--candles",
        type=int,
        default=500,
        help="Number of final volume candles to display (default: 500)",
    )
    parser.add_argument(
        "--trend_line_break",
        action="store_true",
        help="Calculate and report trend line breakout price",
    )
    return parser.parse_args()


def fetch_raw_data(client, symbol, final_candle_count, volume_per_candle, is_option=False):
    """Fetches trade data to build volume-based candles."""
    # Fetch 30 days of minute bar data to build volume-based candles.
    # No time filtering - we just build candles from whatever volume data is available.
    # Timestamps in the output simply indicate WHEN each volume candle formed.
    now_utc = datetime.now(timezone.utc)
    start_date = now_utc - timedelta(days=30)
    end_date = now_utc

    print(
        f"Building {final_candle_count} volume candles ({volume_per_candle:,} per candle) for {symbol}...",
        file=sys.stderr,
    )

    try:
        if is_option:
            # Prefer indicative feed for broader account compatibility.
            request_params = OptionBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=TimeFrame.Minute,
                start=start_date,
                end=end_date,
                limit=50000,
                feed=OptionsFeed.INDICATIVE,
            )
            bars = client.get_option_bars(request_params)
        else:
            request_params = StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=TimeFrame.Minute,
                start=start_date,
                end=end_date,
                limit=50000,
                feed=DataFeed.IEX,
            )
            bars = client.get_stock_bars(request_params)

        df = bars.df
        if "symbol" in df.index.names:
            df = df.reset_index(level=0, drop=True)
        return df.reset_index()
    except Exception as e:
        asset_kind = "option" if is_option else "stock"
        print(f"Error fetching {asset_kind} data from Alpaca: {e}", file=sys.stderr)
        sys.exit(1)


def build_volume_candles(df, volume_target, max_candles):
    """Aggregates trade data into fixed-volume candles.
    
    Each candle represents exactly volume_target volume, independent of time or number of trades.
    """
    volume_candles = []
    current_candle = None

    # Walk through chronological trade data
    for _, row in df.iterrows():
        vol = row["volume"]
        close_p = row["close"]
        high_p = row["high"]
        low_p = row["low"]
        open_p = row["open"]
        time_p = row["timestamp"]

        if current_candle is None:
            # Start a new candle
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
        volume_needed = volume_target - current_candle["volume"]

        if vol >= volume_needed:
            # This bar completes the candle (meets exact volume target)
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
        print(
            f"Not enough trading volume found to build even one {volume_target:,}-volume candle.",
            file=sys.stderr,
        )
        sys.exit(1)

    return candles_df.tail(max_candles).reset_index(drop=True)


def fit_trend_line(highs):
    """Fit a linear trend line to the highs and return slope and intercept."""
    count = len(highs)
    x_sum = sum(range(count))
    y_sum = sum(highs)
    xx_sum = sum(index * index for index in range(count))
    xy_sum = sum(index * value for index, value in enumerate(highs))
    denominator = count * xx_sum - x_sum * x_sum
    if denominator == 0:
        return 0, y_sum / count
    slope = (count * xy_sum - x_sum * y_sum) / denominator
    intercept = (y_sum - slope * x_sum) / count
    return slope, intercept


def calculate_breakout_price(highs, slope, intercept):
    """Calculate the projected trend line value at the next candle index."""
    next_index = len(highs)
    projected_value = intercept + slope * next_index
    # Round up to nearest cent
    return math.ceil(projected_value * 100) / 100


def display_candle_table(df, symbol, volume_target):
    """Display a table of candles with high, low, and other details."""
    print("\n" + "=" * 100)
    print(f"{symbol} Volume Candles Table ({volume_target:,} volume per candle)")
    print("=" * 100)
    print(f"{'#':>4} | {'OPEN':>10} | {'HIGH':>10} | {'LOW':>10} | {'CLOSE':>10} | {'VOLUME':>10} | {'TIMESTAMP':>20}")
    print("-" * 100)
    
    for index, row in df.iterrows():
        print(
            f"{index + 1:>4} | ${row['open']:>9.2f} | ${row['high']:>9.2f} | ${row['low']:>9.2f} | "
            f"${row['close']:>9.2f} | {int(row['volume']):>10,} | {row['timestamp'].strftime('%Y-%m-%d %H:%M:%S'):>20}"
        )
    
    print("=" * 100 + "\n")


def plot_candles(df, symbol, volume_target, is_option=False):
    """Plots the generated volume candles with volume labels and trend line."""
    fig, ax = plt.subplots(figsize=(14, 7))

    # Clean index for x-axis to keep chart spacing consistent
    x = range(len(df))
    
    # Extract highs for trend line
    highs = df["high"].tolist()
    slope, intercept = fit_trend_line(highs)
    breakout_price = calculate_breakout_price(highs, slope, intercept)

    # Draw candlestick bars manually using matplotlib lines
    for i in x:
        open_p = df.loc[i, "open"]
        close_p = df.loc[i, "close"]
        high_p = df.loc[i, "high"]
        low_p = df.loc[i, "low"]
        vol = df.loc[i, "volume"]

        # Color-code based on price movement
        color = "green" if close_p >= open_p else "red"

        # Draw the wick (high to low)
        ax.plot([i, i], [low_p, high_p], color=color, linewidth=1)
        # Draw the real body (open to close)
        ax.plot([i, i], [open_p, close_p], color=color, linewidth=6)
        
        # Add volume label above the candle
        ax.text(i, high_p * 1.002, f"{int(vol):,}", ha="center", va="bottom", fontsize=7, color="gray")

    # Draw trend line connecting the highs
    trend_values = [intercept + slope * i for i in x]
    ax.plot(x, trend_values, color="blue", linewidth=2, linestyle="--", label=f"Trend Line (slope={slope:.6f})")
    
    # Mark the breakout price
    ax.axhline(y=breakout_price, color="orange", linewidth=2, linestyle=":", label=f"Breakout Level: ${breakout_price:.2f}")

    volume_unit = "Contracts" if is_option else "Shares"
    ax.set_title(f"{symbol} Volume Candles - {volume_target:,} {volume_unit} Per Candle")
    ax.set_ylabel("Price ($)")
    ax.set_xlabel("Candle Sequence (Oldest to Newest)")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="best")

    # Sample timestamps for x-axis labels to avoid crowding
    step = max(1, len(df) // 10)
    ax.set_xticks(x[::step])
    ax.set_xticklabels(
        [df.loc[i, "timestamp"].strftime("%Y-%m-%d %H:%M") for i in x[::step]],
        rotation=35,
        ha="right",
    )

    plt.tight_layout()
    plt.show()


def main():
    args = parse_arguments()
    try:
        credentials, _ = bootstrap_trading_auth("plot_graph.py")
    except Exception as e:
        print(f"Authentication failed: {e}", file=sys.stderr)
        sys.exit(1)

    option_mode = is_option_symbol(args.ticker)
    if option_mode:
        client = OptionHistoricalDataClient(
            api_key=credentials.api_key,
            secret_key=credentials.secret_key,
        )
    else:
        client = StockHistoricalDataClient(
            api_key=credentials.api_key,
            secret_key=credentials.secret_key,
        )

    # Fetch data
    raw_df = fetch_raw_data(client, args.ticker, args.candles, args.volume, is_option=option_mode)

    # Build volume bars
    chart_df = build_volume_candles(raw_df, args.volume, args.candles)
    
    # Filter out any candles with timestamps in the future
    if len(chart_df) > 0:
        chart_df['timestamp'] = pd.to_datetime(chart_df['timestamp'], utc=True)
        now_utc = datetime.now(timezone.utc)
        chart_df = chart_df[chart_df['timestamp'] <= now_utc]

    print(f"Displaying the last {len(chart_df)} volume candles ({args.volume:,} per candle).")

    # Display table
    display_candle_table(chart_df, args.ticker, args.volume)
    
    # Calculate trend line and breakout
    highs = chart_df["high"].tolist()
    slope, intercept = fit_trend_line(highs)
    breakout_price = calculate_breakout_price(highs, slope, intercept)
    
    if args.trend_line_break:
        # Just report the breakout price
        print(f"TREND LINE BREAKOUT PRICE: ${breakout_price:.2f}")
    else:
        # Show the graph
        plot_candles(chart_df, args.ticker, args.volume, is_option=option_mode)


if __name__ == "__main__":
    main()

