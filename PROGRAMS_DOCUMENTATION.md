# Trading Programs Documentation

Last updated: 2026-08-06

This document lists the Python programs in the workspace root and provides:
- Program name
- What it does
- Usage syntax

It is written in Markdown so you can paste it directly into Google Docs and keep it maintained there.

## Quick Index

- crypto_price.py
- current_week_option.py
- fomo_market.py
- fomo_trade.py
- orders_menu.py
- plot.py
- position_options.py
- previous_close.py
- risk_management.py
- status.py
- stock_price.py

## Program Reference

| Program | What It Does | Usage Syntax | Notes |
|---|---|---|---|
| `crypto_price.py` | Prints latest crypto trade price from Alpaca data API. | `python crypto_price.py [SYMBOL]` | Default symbol is `BTC/USD` when no argument is provided. |
| `current_week_option.py` | Finds this week's nearest call option for a ticker (or for each stock position) and prints option price, volume, and open interest. | `python current_week_option.py [TICKER]` | With `TICKER`, uses current stock price as reference. Without `TICKER`, uses each current stock position's buy price. Uses `OptionsFeed.INDICATIVE` for option trade lookup. |
| `fomo_market.py` | Wrapper that computes strategy levels from current market price, launches `fomo_trade.py`, and refreshes daily after 2:00 PM PT. | `python fomo_market.py <TICKER> <NUM_STOCKS>` | `NUM_STOCKS` must be a positive even integer. |
| `fomo_trade.py` | Core strategy runner: entry/stop/target management with 50/50 scale-out and automatic re-entry logic. | `python fomo_trade.py <TICKER> <NUM_STOCKS> <ENTRY_PRICE> <STOP_PRICE> <TARGET1_PRICE> <TARGET2_PRICE>` | `NUM_STOCKS` must be even. Long-trade validation enforced: `STOP < ENTRY < TARGET1 < TARGET2`. |
| `orders_menu.py` | Interactive menu to view/cancel open orders and show current open positions. | `python orders_menu.py` | Includes single-order cancel and cancel-all actions. |
| `plot.py` | Plots stock charts using either daily candles or volume-based candles. | `python plot.py [SYMBOL] [CHART_TYPE] [LOOKBACK_CANDLES] [VOLUME_PER_CANDLE]` | `CHART_TYPE`: `daily` or `vol`. If `vol`, `VOLUME_PER_CANDLE` is required. |
| `position_options.py` | Wrapper that scans current stock positions and runs `current_week_option.py` for each ticker. | `python position_options.py` | Useful for one-command options scan of all held stock symbols. |
| `previous_close.py` | Wrapper that computes strategy levels from previous day close, launches `fomo_trade.py`, and refreshes daily after 2:00 PM PT. | `python previous_close.py <TICKER> <NUM_STOCKS>` | Entry is ceiling of previous close. `NUM_STOCKS` must be positive. |
| `risk_management.py` | Continuous risk manager for all open positions (stop, targets, breakeven, re-entry handling). | `python risk_management.py` | Runs in a loop every 30 seconds until interrupted. |
| `status.py` | Shows account balance, open orders, today's fills, and current positions; includes close-position actions. | `python status.py [SYMBOL]` | Optional symbol filter; interactive close-one/close-all market actions. |
| `stock_price.py` | Prints latest stock price with daily high/low summary. | `python stock_price.py [TICKER]` | Default ticker is `AAPL`. Uses IEX feed for stock data calls. |

## Usage Examples

```bash
python status.py
python status.py MU

python fomo_trade.py ETH/USD 10 1908 1890 1927 1965
python previous_close.py AVGO 10
python fomo_market.py INTC 10

python current_week_option.py AVGO
python current_week_option.py
python position_options.py

python stock_price.py AVGO
python crypto_price.py ETH/USD
python plot.py MU daily 20
python plot.py MU vol 50 1000
```

## Maintenance Template (for Google Docs)

Use this table format when adding or updating scripts:

| Program | What It Does | Usage Syntax | Notes |
|---|---|---|---|
| `example.py` | One-line purpose. | `python example.py <ARG1> [ARG2]` | Defaults, constraints, and special behavior. |
