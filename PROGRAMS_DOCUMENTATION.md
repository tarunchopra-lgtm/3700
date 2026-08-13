# Trading Programs Documentation

Last updated: 2026-08-08

This document lists the Python programs in the workspace root and provides:
- program name
- what it does
- usage syntax

It is written in Markdown so it can be pasted into Google Docs and maintained there.

## Quick Index

- atr.py
- crypto_price.py
- current_week_option.py
- current_week_option_function_call.py
- current_week_option_function_put.py
- daily_pl.py
- demand.py
- fomo_market.py
- fomo_trade.py
- orders_menu.py
- plot.py
- position_options.py
- previous_close.py
- risk_management.py
- spy_option.py
- spy_options.py
- status.py
- stock_price.py

## Program Reference

| Program | What It Does | Usage Syntax | Notes |
|---|---|---|---|
| `atr.py` | Prints a single 14-day daily ATR value for a stock ticker. | `python atr.py <TICKER>` | Silent credential loading; prints only the ATR number. |
| `crypto_price.py` | Prints latest crypto trade price from Alpaca market data. | `python crypto_price.py [SYMBOL]` | Default symbol is `BTC/USD`. |
| `current_week_option.py` | Shows nearest this-week CALL and PUT option contracts for a ticker, including mid price, volume, and open interest. | `python current_week_option.py [TICKER]` | Without a ticker, it scans current stock positions and uses buy price as reference. |
| `current_week_option_function_call.py` | Returns only the nearest this-week CALL option symbol. | `python current_week_option_function_call.py <TICKER> [REFERENCE_PRICE]` | Prints only the option symbol. Optional `REFERENCE_PRICE` overrides live stock price. |
| `current_week_option_function_put.py` | Returns only the nearest this-week PUT option symbol. | `python current_week_option_function_put.py <TICKER> [REFERENCE_PRICE]` | Prints only the option symbol. Optional `REFERENCE_PRICE` overrides live stock price. |
| `daily_pl.py` | Computes today's realized P/L from filled orders and saves a JSON state file for daily tracking. | `python daily_pl.py [OUTPUT_FILE]` | Prints per-symbol trade count, total realized P/L, and average P/L per trade. |
| `demand.py` | Scans the last 200 hourly candles for a demand pattern, computes buy/stop/targets, and launches `fomo_trade.py`. | `python demand.py <TICKER>` | Pattern requires a 2x average-range candle preceded by a smaller-than-average candle. Rejects stale/far-away setups. |
| `fomo_market.py` | Wrapper that computes entry, stop, and targets from current market price and launches `fomo_trade.py`. | `python fomo_market.py <TICKER> <NUM_STOCKS>` | Refreshes levels daily after 2:00 PM PT and restarts the child process. |
| `fomo_trade.py` | Core trade runner for stocks, crypto, and options. Monitors price, manages stop/targets, and can re-enter after stop-out. | `python fomo_trade.py <TICKER> <NUM_STOCKS> <ENTRY_PRICE> <STOP_PRICE> <TARGET1_PRICE> <TARGET2_PRICE>` | Uses `ENTRY_PRICE` as the trigger line and submits market buys when price crosses it. `NUM_STOCKS` must be even. |
| `orders_menu.py` | Interactive order management menu for open orders and current positions. | `python orders_menu.py` | Supports cancel-one and cancel-all order actions. |
| `plot.py` | Plots stock or crypto charts using daily candles or volume-based candles. | `python plot.py [SYMBOL] [CHART_TYPE] [LOOKBACK_CANDLES] [VOLUME_PER_CANDLE]` | `CHART_TYPE` is `daily` or `vol`. If `vol`, `VOLUME_PER_CANDLE` is required. |
| `position_options.py` | Runs `current_week_option.py` once for every stock position in the account. | `python position_options.py` | Useful for scanning all held stock positions for this-week options. |
| `previous_close.py` | Wrapper that computes entry, stop, and targets from previous day close and launches `fomo_trade.py`. | `python previous_close.py <TICKER> <NUM_STOCKS>` | Uses ceiling of previous close for entry and refreshes daily after 2:00 PM PT. |
| `risk_management.py` | Continuous risk-management daemon for all open positions. | `python risk_management.py` | Monitors stop, breakeven, and re-entry logic across positions. Runs continuously. |
| `spy_option.py` | SPY-specific options strategy runner using ATR, current/open/reference levels, and automatic CALL/PUT trigger monitoring. | `python spy_option.py [SYMBOL]` | Weekend-safe: uses most recent trading-day data when current session data is unavailable. |
| `spy_options.py` | Alias entrypoint for `spy_option.py`. | `python spy_options.py [SYMBOL]` | Same behavior as `spy_option.py`. |
| `status.py` | Shows account balance, open orders, today's fills, and current positions with close-position actions. | `python status.py [SYMBOL]` | Optional symbol filter. Includes close-one and close-all market actions. |
| `stock_price.py` | Prints latest stock or option price plus high/low summary. | `python stock_price.py [TICKER]` | Stocks use intraday minute bars for today's high/low. Option symbols use option market data and fall back to `N/A` high/low if bars are unavailable. |

## Monday Plan

Planned Monday strategy split so results can be reviewed independently by symbol:

| Strategy | Program | Planned Symbol | Notes |
|---|---|---|---|
| FOMO manual trigger strategy | `fomo_trade.py` | `MU` | Keep MU isolated for direct FOMO trade management. |
| Previous-close setup | `previous_close.py` | `INTC` | Uses previous close to compute entry/stop/targets. |
| SPY options strategy | `spy_options.py` | `SPY` | Dedicated SPY options workflow so option P/L is isolated. |

This separation keeps all three strategies on different symbols, which makes end-of-day review and P/L analysis easier.

## Usage Examples

```bash
python status.py
python status.py MU

python fomo_trade.py MU 2 780 770 800 900
python previous_close.py INTC 2
python fomo_market.py ETH/USD 2

python current_week_option.py SPY
python current_week_option_function_call.py SPY
python current_week_option_function_put.py SPY 770
python position_options.py

python stock_price.py SPY
python stock_price.py SPY260814C00770000
python crypto_price.py BTC/USD
python atr.py SPY
python daily_pl.py
python demand.py BTC/USD
python spy_options.py
python plot.py BTC/USD daily 20
python plot.py MU vol 50 1000
```

## Maintenance Template

Use this table format when adding or updating scripts:

| Program | What It Does | Usage Syntax | Notes |
|---|---|---|---|
| `example.py` | One-line purpose. | `python example.py <ARG1> [ARG2]` | Defaults, constraints, and special behavior. |
