import argparse
from datetime import datetime, timedelta
import re
import sys
import matplotlib.pyplot as plt
import pandas as pd
from alpaca.data.enums import DataFeed, OptionsFeed
from alpaca.data.historical import StockHistoricalDataClient, OptionHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, OptionBarsRequest
from alpaca.data.timeframe import TimeFrame
from pathlib import Path
import sys

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
    return parser.parse_args()


def fetch_raw_data(client, symbol, final_candle_count, volume_per_candle, is_option=False):
    """Fetches enough 1-minute bar data to build the requested volume candles."""
    # Estimate how much data we need. We look back up to 30 days to ensure
    # we get enough total volume to build the requested number of candles.
    end_date = datetime.now()
    start_date = end_date - timedelta(days=30)

    print(
        f"Fetching 1-minute bars for {symbol} to build volume candles...",
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
                feed=OptionsFeed.INDICATIVE,
            )
            bars = client.get_option_bars(request_params)
        else:
            request_params = StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=TimeFrame.Minute,
                start=start_date,
                end=end_date,
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
    """Aggregates 1-minute bars into custom candles based on a fixed volume threshold."""
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
        print(
            "Not enough trading volume found to build even one candle.",
            file=sys.stderr,
        )
        sys.exit(1)

    return candles_df.tail(max_candles).reset_index(drop=True)


def plot_candles(df, symbol, volume_target, is_option=False):
    """Plots the generated custom volume bars."""
    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(12, 8), sharex=True, gridspec_kw={"height_ratios": [3, 1]}
    )

    # Clean index for x-axis to keep chart spacing consistent
    x = range(len(df))

    # Draw candlestick bars manually using matplotlib lines
    for i in x:
        open_p = df.loc[i, "open"]
        close_p = df.loc[i, "close"]
        high_p = df.loc[i, "high"]
        low_p = df.loc[i, "low"]

        # Color-code based on price movement
        color = "green" if close_p >= open_p else "red"

        # Draw the wick (high to low)
        ax1.plot([i, i], [low_p, high_p], color=color, linewidth=1)
        # Draw the real body (open to close)
        ax1.plot([i, i], [open_p, close_p], color=color, linewidth=6)

    volume_unit = "Contracts" if is_option else "Shares"
    ax1.set_title(f"{symbol} Custom Volume Chart ({volume_target:,} {volume_unit} Per Candle)")
    ax1.set_ylabel("Price ($)")
    ax1.grid(True, alpha=0.3)

    # Bottom Plot: Verification that volume is fixed
    ax2.bar(x, df["volume"], color="purple", width=0.6)
    ax2.set_ylabel("Volume")
    ax2.set_xlabel("Candle Sequence (Oldest to Newest)")
    ax2.grid(True, alpha=0.3)

    # Sample timestamps for x-axis labels to avoid crowding
    step = max(1, len(df) // 10)
    ax2.set_xticks(x[::step])
    ax2.set_xticklabels(
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

    print(f"Displaying the last {len(chart_df)} volume candles.")

    # Plot
    plot_candles(chart_df, args.ticker, args.volume, is_option=option_mode)


if __name__ == "__main__":
    main()

