# Trading Programs Documentation

Last updated: 2026-08-18

## Strategies

| Program | What It Does | Usage |
|---|---|---|
| `current_week_option.py` | Shows the nearest current-week call and put contracts with midpoint, volume, and open interest for a ticker or held stocks. | `python strategies/current_week_option.py [TICKER]` |
| `current_week_option_function_call.py` | Prints the nearest active current-week call symbol for a ticker and optional reference price. | `python strategies/current_week_option_function_call.py <TICKER> [REFERENCE_PRICE]` |
| `current_week_option_function_put.py` | Prints the nearest active current-week put symbol for a ticker and optional reference price. | `python strategies/current_week_option_function_put.py <TICKER> [REFERENCE_PRICE]` |
| `daily-stat.py` | Records daily account equity in `balance.txt` without same-day overwrites and reports change from the previous record. | `python strategies/daily-stat.py [--schedule]` |
| `find_daily_trend.py` | Scans ticker-list files for stocks breaking above a descending 15-day high trend today. | `python strategies/find_daily_trend.py [LIST_FILE ...]` |
| `find_vol.py` | Tests 0.1%-1.0% average-volume candle sizes and recommends a qualified size for `long_trend.py`. | `python strategies/find_vol.py <TICKER>` |
| `fomo_market.py` | Derives levels from current price and launches `fomo_trade.py`, refreshing them daily after 2:00 PM Pacific. | `python strategies/fomo_market.py <TICKER> <NUM_STOCKS>` |
| `fomo_trade.py` | Manages long limit entry, stop, partial target, final target, and optional option midpoint/single-entry behavior. | `python strategies/fomo_trade.py <TICKER> <NUM_STOCKS> <ENTRY> <STOP> <TARGET1> <TARGET2> [--refresh-option-midpoint] [--single-entry]` |
| `gocall.py` | Buys weekly calls for held stocks, halves the option at a 50-share underlying reduction, and exits when the stock closes. | `python strategies/gocall.py <SIZE>` |
| `long_trend.py` | Trades volume-candle breaks above descending resistance, sells half at 1R, and trails the remainder under the 10-candle low trend. | `python strategies/long_trend.py <SYMBOL> <QUANTITY> <VOLUME_PER_CANDLE>` |
| `optimize.py` | Grid-searches fixed FOMO entry, stop, and target ranges by repeatedly running the historical backtest. | `python strategies/optimize.py <DAYS_BACK> <TICKER> <NUM_STOCKS> <ENTRY_RANGE> <STOP_RANGE> <TARGET1_RANGE> <TARGET2_RANGE>` |
| `optimize_long_trend.py` | Compares `long_trend.py` performance across 0.1%-1.0% average-volume candle sizes. | `python strategies/optimize_long_trend.py <TICKER>` |
| `orders_menu.py` | Opens an interactive menu to inspect positions/orders and cancel one or all open orders. | `python strategies/orders_menu.py` |
| `position_options.py` | Runs the weekly-option report for every current stock position. | `python strategies/position_options.py` |
| `previous_close.py` | Derives entry, stop, and targets from previous close and launches a daily-refreshing FOMO trade. | `python strategies/previous_close.py <TICKER> <NUM_STOCKS>` |
| `risk_management.py` | Continuously manages all positions with a 5% stop, 1R partial target, breakeven logic, and re-entry. | `python strategies/risk_management.py` |
| `spy_option.py` | Watches ATR-based SPY levels and launches fresh-midpoint weekly call or put trades on each re-armed trigger. | `python strategies/spy_option.py [ATR_RANGE_MULTIPLIER]` |
| `status.py` | Displays account balance, orders, fills, and positions with optional filtering and interactive market-close actions. | `python strategies/status.py [SYMBOL]` |
| `uptrend.py` | Reports each ticker's latest daily descending-trend break and subsequent open or exited trade result. | `python strategies/uptrend.py <TICKER> [TICKER ...]` |

## Indicators

| Program | What It Does | Usage |
|---|---|---|
| `atr.py` | Prints a stock's 14-day daily Average True Range. | `python indicator/atr.py <TICKER>` |
| `backtest.py` | Backtests supported previous-close or fixed-level FOMO strategies over historical daily and minute bars. | `python indicator/backtest.py <DAYS_BACK> <PROGRAM_FILE.py> <PROGRAM_ARGS...>` |
| `backtest_previous_close_intc_daily.py` | Runs the fixed INTC previous-close strategy simulation over 100 daily candles. | `python indicator/backtest_previous_close_intc_daily.py` |
| `crypto_price.py` | Prints the latest Alpaca crypto trade price, defaulting to `BTC/USD`. | `python indicator/crypto_price.py [SYMBOL]` |
| `daily_pl.py` | FIFO-matches today's fills, prints realized P/L by symbol and total, and saves JSON state. | `python indicator/daily_pl.py [OUTPUT_FILE]` |
| `demand.py` | Finds a recent 2x-range hourly demand pattern, calculates levels, and launches `fomo_trade.py`. | `python indicator/demand.py <TICKER>` |
| `email_status.py` | Polls positions and orders every minute and emails detected additions, reductions, closures, or status changes. | `python indicator/email_status.py` |
| `max_loss.py` | Calculates today's realized maximum loss and prints it as `MAX_LOSS_TODAY=<amount>`. | `python indicator/max_loss.py` |
| `plot.py` | Draws stock or crypto daily/volume charts using the reusable plot role. | `python indicator/plot.py [SYMBOL] [daily|vol] [LOOKBACK_CANDLES] [VOLUME_PER_CANDLE]` |
| `plot_graph.py` | Builds fixed-volume stock or option candles from minute bars and displays a price/volume chart. | `python indicator/plot_graph.py --ticker <TICKER> --volume <VOLUME> [--candles <COUNT>]` |
| `stock_alert.py` | Polls `alert_data.txt` each minute and emails when a stock comes within 0.5% of its target. | `python indicator/stock_alert.py` |
| `stock_price.py` | Prints current stock/option price and today's available high and low. | `python indicator/stock_price.py [TICKER]` |
| `vol_finder.py` | Prints 14-day average IEX volume and its 1% volume-candle size. | `python indicator/vol_finder.py <TICKER>` |
| `zone_finder.py` | Finds the latest 2x-range displacement among 500 volume candles and reports its supply/demand zone. | `python indicator/zone_finder.py <TICKER>` |
