# Trading Programs Documentation

Last updated: 2026-09-10

---

## INDICATOR PROGRAMS

### `atr.py`
**What it does:** Calculates and prints the 14-day Average True Range (ATR) for a stock or crypto asset. ATR measures volatility and is useful for setting stop loss levels.

**Syntax:**
```bash
python indicator/atr.py <TICKER>
```

**Examples:**
```bash
python indicator/atr.py NVDA          # Stock ATR
python indicator/atr.py BTC/USD       # Crypto ATR
python indicator/atr.py ETH/USD
```

**Test Scenarios:**
1. **High-volatility stock:** `python indicator/atr.py NVDA` → Should show ATR > 5% of price
2. **Stable stock:** `python indicator/atr.py JNJ` → Should show ATR < 2% of price
3. **Crypto volatility:** `python indicator/atr.py BTC/USD` → Compare ATR for crypto vs stock

---

### `backtest.py`
**What it does:** Backtests trading strategies (previous-close or fixed-level FOMO) over historical daily or minute bars. Outputs daily results showing entry, stop, targets, P/L, and hit rate.

**Syntax:**
```bash
python indicator/backtest.py <DAYS_BACK> <PROGRAM_FILE.py> <PROGRAM_ARGS...>
```

**Examples:**
```bash
python indicator/backtest.py 100 strategies/previous_close.py AAPL 2
python indicator/backtest.py 50 strategies/long_trend.py MU 10 25000
```

**Test Scenarios:**
1. **Short backtest:** `python indicator/backtest.py 10 strategies/previous_close.py SPY 1` → Quick validation
2. **Longer backtest:** `python indicator/backtest.py 100 strategies/previous_close.py AAPL 2` → Assess strategy consistency
3. **Compare strategies:** Run same 50-day backtest with different position sizes to validate risk scaling

---

### `crypto_price.py`
**What it does:** Fetches and displays the latest trade price for a cryptocurrency pair from Alpaca. Defaults to BTC/USD if no symbol specified.

**Syntax:**
```bash
python indicator/crypto_price.py [SYMBOL]
```

**Examples:**
```bash
python indicator/crypto_price.py                # Default BTC/USD
python indicator/crypto_price.py ETH/USD
python indicator/crypto_price.py DOGE/USD
```

**Test Scenarios:**
1. **Default crypto:** `python indicator/crypto_price.py` → Should return BTC/USD price
2. **Specific altcoin:** `python indicator/crypto_price.py ETH/USD` → Verify correct pricing
3. **Invalid symbol:** `python indicator/crypto_price.py XXX/USD` → Should handle gracefully with error

---

### `daily_pl.py`
**What it does:** Computes daily realized P/L by FIFO-matching all buys and sells from today's filled orders. Prints per-symbol and total daily profit/loss, saves JSON state for tracking.

**Syntax:**
```bash
python indicator/daily_pl.py [OUTPUT_FILE]
```

**Examples:**
```bash
python indicator/daily_pl.py                               # Default output
python indicator/daily_pl.py daily_pl_state.json          # Custom file
```

**Test Scenarios:**
1. **No trades today:** `python indicator/daily_pl.py` → Should show $0.00 P/L
2. **Profitable day:** Execute trades with winners and losers, run command → Verify correct FIFO matching
3. **Existing JSON state:** Delete and recreate daily_pl_state.json, verify rebuild is accurate

---

### `demand.py`
**What it does:** Identifies 2x-range demand patterns in hourly candles, calculates entry/stop/targets, then launches fomo_trade.py with computed levels.

**Syntax:**
```bash
python indicator/demand.py <TICKER>
```

**Examples:**
```bash
python indicator/demand.py MU
python indicator/demand.py AAPL
```

**Test Scenarios:**
1. **Pattern exists:** `python indicator/demand.py MU` → Should find recent pattern and launch fomo_trade.py
2. **No pattern found:** `python indicator/demand.py JNJ` → Should report no qualifying pattern
3. **Check levels:** After running, verify fomo_trade.txt has reasonable stop/target ratio (typically 1:1 to 1:2)

---

### `email_status.py`
**What it does:** Runs in background, polls positions and orders every 60 seconds, detects changes (new orders, filled positions, closures, executions), and emails alerts to configured address.

**Syntax:**
```bash
python indicator/email_status.py
```

**Examples:**
```bash
python indicator/email_status.py        # Run continuously, Ctrl+C to stop
```

**Test Scenarios:**
1. **Fresh position:** Place a new limit buy order, wait 60s → Email should notify order placement
2. **Order fills:** Let order fill (or close it manually), wait 60s → Email should show position filled
3. **Position closes:** Sell position manually, wait 60s → Email should alert closure

---

### `max_loss.py`
**What it does:** Calculates today's maximum realized loss from filled orders using FIFO matching. Prints as `MAX_LOSS_TODAY=<amount>`. Useful for compliance checks with daily loss limits.

**Syntax:**
```bash
python indicator/max_loss.py
```

**Examples:**
```bash
python indicator/max_loss.py            # Output: MAX_LOSS_TODAY=250.50
```

**Test Scenarios:**
1. **No trades:** `python indicator/max_loss.py` → Should output `MAX_LOSS_TODAY=0.00`
2. **Profitable day:** `python indicator/max_loss.py` → Should output `MAX_LOSS_TODAY=0.00` (no loss)
3. **Losing day:** Close positions at a loss, run command → Should show positive loss amount

---

### `plot.py`
**What it does:** Renders stock or crypto daily/volume candle charts using Matplotlib. Supports both daily and fixed-volume candles with customizable lookback period.

**Syntax:**
```bash
python indicator/plot.py [SYMBOL] [daily|vol] [LOOKBACK_CANDLES] [VOLUME_PER_CANDLE]
```

**Examples:**
```bash
python indicator/plot.py AAPL daily 50
python indicator/plot.py MU vol 100 25000
python indicator/plot.py BTC/USD daily 200
```

**Test Scenarios:**
1. **Daily chart:** `python indicator/plot.py SPY daily 100` → Show 100 days of candlesticks
2. **Volume chart:** `python indicator/plot.py MU vol 50 25000` → Show 50-candle volume-based chart
3. **Crypto chart:** `python indicator/plot.py ETH/USD daily 30` → Verify crypto pricing overlay

---

### `plot_graph.py`
**What it does:** Builds fixed-volume candles from minute bars and displays interactive price/volume chart with optional trend line breakout price overlay.

**Syntax:**
```bash
python indicator/plot_graph.py --ticker <TICKER> --volume <VOLUME> [--candles <COUNT>] [--trend_line_break]
```

**Examples:**
```bash
python indicator/plot_graph.py --ticker SPY --volume 25000 --candles 50
python indicator/plot_graph.py --ticker MU --volume 25000 --candles 100 --trend_line_break
python indicator/plot_graph.py --ticker AAPL --daily --candles 200
```

**Test Scenarios:**
1. **Basic volume chart:** `python indicator/plot_graph.py --ticker SPY --volume 25000 --candles 25` → Verify chart renders
2. **With trend line:** `python indicator/plot_graph.py --ticker MU --volume 30000 --candles 50 --trend_line_break` → Show breakout level
3. **Daily fallback:** `python indicator/plot_graph.py --ticker AAPL --daily --candles 60` → Show daily candles if volume too low

---

### `stock_alert.py`
**What it does:** Monitors stock prices against targets from alert_data.txt. Checks every 60 seconds; when price reaches within 0.5% of target, sends email alert. Allows price to leave wider band before re-arming.

**Syntax:**
```bash
python indicator/stock_alert.py
```

**Alert File Format (alert_data.txt):**
```
AAPL 180.00
MU 960.00
SPY 420.00
```

**Test Scenarios:**
1. **New alert:** Add `AAPL 180.00` to alert_data.txt, run script → Wait for price to come within 0.5% of $180 (i.e., $179.10–$180.90)
2. **Email delivery:** Verify email arrives at configured address when price triggers
3. **Re-arm after exit:** Let price move 1% away, then approach again → Verify alert re-fires

---

### `stock_price.py`
**What it does:** Fetches and displays current price, today's high, and today's low for a stock or option. Handles both regular stocks and option symbols.

**Syntax:**
```bash
python indicator/stock_price.py [TICKER]
```

**Examples:**
```bash
python indicator/stock_price.py AAPL
python indicator/stock_price.py MU
python indicator/stock_price.py AAPL260911C00315000    # Option symbol
```

**Test Scenarios:**
1. **Stock price:** `python indicator/stock_price.py SPY` → Compare output with market data
2. **Before market open:** Run before 9:30 AM ET → Should show yesterday's high/low
3. **Option price:** `python indicator/stock_price.py AAPL260911C00315000` → Verify option pricing

---

### `vol_finder.py`
**What it does:** Calculates 14-day average IEX volume and derives the 1% volume-candle size (used for volume-based backtesting and strategy optimization).

**Syntax:**
```bash
python indicator/vol_finder.py <TICKER>
```

**Examples:**
```bash
python indicator/vol_finder.py MU
python indicator/vol_finder.py AAPL
python indicator/vol_finder.py SPY
```

**Test Scenarios:**
1. **High-volume stock:** `python indicator/vol_finder.py SPY` → Should show ~2–4M volume, 1% candle ~25,000–40,000
2. **Lower-volume stock:** `python indicator/vol_finder.py MU` → Compare relative volume to SPY
3. **Use for optimization:** Take 1% candle value and use in `optimize_long_trend.py TICKER`

---

### `zone_finder.py`
**What it does:** Analyzes 500 volume candles, identifies the latest 2x-range displacement pattern, and reports its supply/demand origin zone with high/low levels for entry planning.

**Syntax:**
```bash
python indicator/zone_finder.py <TICKER>
```

**Examples:**
```bash
python indicator/zone_finder.py MU
python indicator/zone_finder.py AAPL
```

**Test Scenarios:**
1. **Recent breakout:** `python indicator/zone_finder.py MU` → Should identify zone where price broke from earlier range
2. **Compare zones:** Run same command repeatedly → Verify zone updates as new patterns form
3. **Set entry levels:** Use reported zone high/low to plan next demand strategy entry

---

### `backtest_previous_close_intc_daily.py`
**What it does:** Runs a hardcoded backtest of the INTC previous-close strategy over 100 daily candles. Demonstrates backtesting mechanics for a single ticker with fixed parameters.

**Syntax:**
```bash
python indicator/backtest_previous_close_intc_daily.py
```

**Test Scenarios:**
1. **Run baseline:** `python indicator/backtest_previous_close_intc_daily.py` → Should complete within seconds
2. **Benchmark:** Note the win rate and average P/L, use as baseline for parameter tuning
3. **Modify and rerun:** Change position size or stop/target % in the script, rerun to see impact

---

## STRATEGY PROGRAMS

### `fomo_trade.py`
**What it does:** Multi-symbol FOMO trading bot. Reads symbols and levels from lists/fomo_trade.txt, places entry limit orders for all symbols simultaneously (SETUP phase), then monitors for fills and places stop/target orders only after positions exist (MONITOR phase). Handles re-entries after stop loss hits. **Automatically detects entry price changes in config file and updates orders.**

**Syntax:**
```bash
python strategies/fomo_trade.py
```

**Configuration File (lists/fomo_trade.txt):**
```
# Format: SYMBOL NUM_STOCKS ENTRY_PRICE STOP_PRICE TARGET1_PRICE TARGET2_PRICE
MU 2 958 948 968 1000
HSY 10 173 172 174 180
AAPL 5 325.40 325.36 326 327
```

**Test Scenarios:**
1. **Multi-symbol entry:** Add 2–3 symbols to fomo_trade.txt, run script → Verify all entry limit orders placed immediately in SETUP phase within first minute
2. **Position fill:** Let at least one order fill → Verify MONITOR phase places stop + 2 targets within 1–2 minutes of fill
3. **Price update:** Edit entry price in fomo_trade.txt while script running (e.g., $958 → $960) → Verify script cancels old order and places new order at updated price on next check

---

### `risk_management.py`
**What it does:** Continuous position monitoring bot. Reads stop/target levels from lists/fomo_trade.txt on every check (dynamic reloading), applies risk management:
- **Stop loss hit:** Exits position and waits for re-entry
- **Target 1 hit:** Sells 1/2 position, moves stop to breakeven
- **Target 2 hit:** Closes remaining position
- **Config changes:** Automatically detects and applies updated stop/target prices from file
- **Entry order placement:** Places initial entry orders for tracked symbols and updates if entry price changes

**Syntax:**
```bash
python strategies/risk_management.py [--ticker SYMBOL] [--stop-loss PCT] [--target1 PCT] [--target2 PCT]
```

**Examples:**
```bash
python strategies/risk_management.py                                           # All positions, default 1% stop
python strategies/risk_management.py --ticker AAPL                             # AAPL only, default stops
python strategies/risk_management.py --stop-loss 0.5 --target1 0.5 --target2 1  # 0.5% stop, 0.5% tgt1, 1% tgt2
```

**Test Scenarios:**
1. **Stop loss execution:** Place position, set fomo_trade.txt stop below current price → Wait for stop to trigger and position to close
2. **Config reload:** Update stop price in fomo_trade.txt while bot running (e.g., 948 → 945) → Verify next check shows new level and applies it
3. **Re-entry after stop:** After position closes, lower entry price to current price in fomo_trade.txt → Verify bot places re-entry order when price recovers to entry level

---

### `tlb_trade.py`
**What it does:** TLB (Today's Leading Breakouts) strategy. Trades all symbols from lists/today-breakout file. Places entry orders for all symbols simultaneously (no waiting), monitors for fills, then places stops/targets only after positions exist. Supports re-entry after stop loss.

**Syntax:**
```bash
python strategies/tlb_trade.py
```

**Configuration File (lists/today-breakout):**
```
AAPL
MU
SPY
```

**Configuration (edit in script):**
- `POSITION_SIZE = 1000.0` → Target dollar per trade
- `STOP_LOSS_PCT = 1.0` → 1% stop
- `TARGET1_PCT = 1.0` → 1% target 1
- `TARGET2_PCT = 2.0` → 2% target 2
- `CHECK_INTERVAL = 5` → Check every 5 seconds

**Test Scenarios:**
1. **Parallel entry orders:** Add 3–5 symbols to today-breakout, run script → Verify all entry orders placed within first 30 seconds
2. **Stop and target placement:** Let one or more orders fill → Verify stop loss and 2 targets placed within 1 minute of fill
3. **Position scaling:** Edit POSITION_SIZE from $1000 to $2000 → Verify position sizes double on next run

---

### `closing_bell.py`
**What it does:** Market-close automation. Closes all open positions by attempting limit sells at the bid/ask midpoint. If order doesn't fill within 60 seconds, cancels and uses market order to ensure closure at end of day.

**Syntax:**
```bash
python strategies/closing_bell.py
```

**Test Scenarios:**
1. **Close single position:** Hold 1 position, run script near market close → Verify position closes
2. **Multiple positions:** Hold 3–5 positions, run script → Verify all close within 2–3 minutes
3. **Order timeout:** Place a position with limit order at extreme price (e.g., $0.01), run closing_bell → Verify order cancels after 60s and closes at market

---

### `spray.py`
**What it does:** Spray Trading Strategy. Sets 4 buy zones around today's open price (+/- 1% and 2%), trades them one at a time. Each zone has 0.25% stop, 0.25% target 1 (half position), 0.75% target 2 (rest). Supports stocks and crypto.

**Syntax:**
```bash
python strategies/spray.py <SYMBOL> <QUANTITY>
```

**Examples:**
```bash
python strategies/spray.py MU 10           # 10 shares of MU
python strategies/spray.py SPY 5
python strategies/spray.py BTC/USD 0.1     # Crypto
```

**Test Scenarios:**
1. **Single zone activation:** `python strategies/spray.py MU 10` → Run during market hours, observe which zone activates based on price approach
2. **Zone progression:** Wait for first zone trade to complete (stop or targets hit) → Observe next zone automatically activates
3. **Crypto spray:** `python strategies/spray.py ETH/USD 1` → Verify all 4 zones function on crypto pair

---

### `status.py`
**What it does:** Real-time account snapshot. Shows today's open orders (including limit price), fills, current positions (qty, entry, market value), account balance, and total P/L. Supports optional symbol filtering and interactive menu.

**Syntax:**
```bash
python strategies/status.py [SYMBOL]
```

**Examples:**
```bash
python strategies/status.py              # All orders and positions
python strategies/status.py MU           # MU only
python strategies/status.py AAPL         # AAPL only
```

**Test Scenarios:**
1. **Full account view:** `python strategies/status.py` → Verify all positions/orders display with correct data
2. **Symbol filter:** `python strategies/status.py SPY` → Verify only SPY orders/positions shown
3. **Open orders:** Place 2–3 limit orders, run status → Verify all orders listed with status, limit prices, and symbol

---

### `find_daily_trend.py`
**What it does:** Scans all symbol lists in lists/ directory for stocks breaking above a descending 15-day high trend line today. Writes top 5 matches to lists/today-breakout for TLB strategy. Optionally shows detailed trend chart for a single ticker.

**Syntax:**
```bash
python strategies/find_daily_trend.py [LIST_FILE ...] [--email] [TICKER]
```

**Examples:**
```bash
python strategies/find_daily_trend.py                    # Scan all lists, write breakouts
python strategies/find_daily_trend.py nasdaq.txt spy.txt # Scan specific lists
python strategies/find_daily_trend.py --email            # Email results
python strategies/find_daily_trend.py INTC               # Detailed analysis + chart for INTC
```

**Test Scenarios:**
1. **Scan and write:** `python strategies/find_daily_trend.py` → Verify lists/today-breakout populated with top 5 breakouts
2. **Email results:** `python strategies/find_daily_trend.py --email` → Check email for breakout report
3. **Detailed view:** `python strategies/find_daily_trend.py AAPL` → See ASCII chart and exact trend break price

---

### `long_trend.py`
**What it does:** Advanced volume-candle breakout strategy. Enters on break above descending resistance, sells 1/2 at 1R target, trails remaining under 10-candle low for continued profit. Requires volume-per-candle input from vol_finder.

**Syntax:**
```bash
python strategies/long_trend.py <SYMBOL> <QUANTITY> <VOLUME_PER_CANDLE>
```

**Examples:**
```bash
python strategies/long_trend.py MU 10 25000       # 10 shares, 25k volume per candle
python strategies/long_trend.py AAPL 5 40000
python strategies/long_trend.py SPY 2 50000
```

**Test Scenarios:**
1. **Get volume:** `python indicator/vol_finder.py MU` → Note 1% candle size (e.g., 28,000)
2. **Run strategy:** `python strategies/long_trend.py MU 10 28000` → Should enter on trend break
3. **Trail trade:** After target 1 hit, observe trailing under 10-candle low until stop or profit target

---

### `previous_close.py`
**What it does:** Derives entry, stop, and target levels from previous close price, then launches a fresh FOMO trade daily (refreshing at 2:00 PM Pacific to plan next day).

**Syntax:**
```bash
python strategies/previous_close.py <TICKER> <NUM_STOCKS>
```

**Examples:**
```bash
python strategies/previous_close.py AAPL 2
python strategies/previous_close.py MU 5
```

**Test Scenarios:**
1. **Intraday trade:** `python strategies/previous_close.py SPY 1` → Should calculate levels from yesterday's close and place entry order
2. **Refresh behavior:** Run script in morning → positions trade. Run again at 2:05 PM → Should refresh levels based on new close price
3. **Multi-day runs:** Run script on 2–3 consecutive days → Verify levels update each time from fresh previous close

---

### `current_week_option.py`
**What it does:** Shows active current-week option contracts (calls and puts) for a ticker or all held stocks. Displays strike, midpoint price, volume, and open interest for market-making visibility.

**Syntax:**
```bash
python strategies/current_week_option.py [TICKER]
```

**Examples:**
```bash
python strategies/current_week_option.py          # All held stock options
python strategies/current_week_option.py AAPL
python strategies/current_week_option.py SPY
```

**Test Scenarios:**
1. **All options:** `python strategies/current_week_option.py` → List all current-week calls/puts for all held stocks
2. **Single ticker:** `python strategies/current_week_option.py AAPL` → Show AAPL weekly calls and puts with prices
3. **Before expiration:** Run mid-week → Verify contracts are current week; run after Friday close → Verify contracts roll to next week

---

### `current_week_option_function_call.py`
**What it does:** Returns the nearest active current-week call symbol for a ticker (useful for scripting). Optionally accepts a reference price for moneyness filtering.

**Syntax:**
```bash
python strategies/current_week_option_function_call.py <TICKER> [REFERENCE_PRICE]
```

**Examples:**
```bash
python strategies/current_week_option_function_call.py AAPL
python strategies/current_week_option_function_call.py AAPL 180.50     # ATM to above
```

**Test Scenarios:**
1. **No price filter:** `python strategies/current_week_option_function_call.py SPY` → Return first available weekly call
2. **With reference:** `python strategies/current_week_option_function_call.py AAPL 180` → Return call closest to or above $180 strike
3. **Pipe to script:** Use output in another script: `` CALL=$(python strategies/current_week_option_function_call.py AAPL) `` → Verify symbol is valid

---

### `current_week_option_function_put.py`
**What it does:** Returns the nearest active current-week put symbol for a ticker. Mirrors call function for put options.

**Syntax:**
```bash
python strategies/current_week_option_function_put.py <TICKER> [REFERENCE_PRICE]
```

**Examples:**
```bash
python strategies/current_week_option_function_put.py AAPL
python strategies/current_week_option_function_put.py SPY 420          # ATM or below
```

**Test Scenarios:**
1. **No price filter:** `python strategies/current_week_option_function_put.py MU` → Return first available weekly put
2. **With reference:** `python strategies/current_week_option_function_put.py MU 950` → Return put at or below $950 strike
3. **Compare calls vs puts:** Run both functions for same ticker → Verify different option types returned

---

### `daily-stat.py`
**What it does:** Records daily account equity in balance.txt without overwriting same-day entries. Reports change from previous record. Useful for daily P/L tracking and account growth metrics.

**Syntax:**
```bash
python strategies/daily-stat.py [--schedule]
```

**Examples:**
```bash
python strategies/daily-stat.py              # Run once, record today's balance
python strategies/daily-stat.py --schedule   # Run via Windows Task Scheduler
```

**Test Scenarios:**
1. **New day:** Run on a fresh day → Records balance, shows 0 change (first record of day)
2. **Same day re-run:** Run again same day → Should skip overwrite, show same balance
3. **Next day:** Run following day → Should record new balance, show day-over-day change

---

### `gocall.py`
**What it does:** Buys weekly call options for every held stock position, halves the call position when underlying reduces by 50 shares, exits when underlying fully closes.

**Syntax:**
```bash
python strategies/gocall.py <SIZE>
```

**Examples:**
```bash
python strategies/gocall.py 2          # 2 call contracts per 100 shares held
python strategies/gocall.py 1
```

**Test Scenarios:**
1. **New position:** Hold 100 shares of AAPL, run script → Should buy 2 call contracts (SIZE=2)
2. **Partial exit:** Sell 50 shares of AAPL → On next run, should sell 1 call (half position)
3. **Full exit:** Close all 100 shares → On next run, should close remaining 1 call contract

---

### `optimize.py`
**What it does:** Grid-search parameter optimization for FOMO strategy. Tests ranges of entry, stop, and target prices, backtests each combination, and reports win rates and average P/L per parameter set.

**Syntax:**
```bash
python strategies/optimize.py <DAYS_BACK> <TICKER> <NUM_STOCKS> <ENTRY_RANGE> <STOP_RANGE> <TARGET1_RANGE> <TARGET2_RANGE>
```

**Examples:**
```bash
python strategies/optimize.py 50 MU 2 "945:960:1" "940:950:1" "960:970:1" "970:985:2"
```

**Range format:** `start:end:step`

**Test Scenarios:**
1. **Quick optimization:** `python strategies/optimize.py 20 AAPL 2 "320:330:2" "310:320:1" "330:340:2" "340:360:5"` → Should complete in 1–2 min
2. **Wide range:** Use 10-point steps → Fewer combinations, faster results
3. **Compare results:** Run same ticker with different DAYS_BACK (20 vs 50 vs 100) → Verify consistency

---

### `optimize_long_trend.py`
**What it does:** Optimization for long_trend.py. Tests performance across 0.1%–1.0% average-volume candle sizes to find optimal volume-candle setting for a ticker.

**Syntax:**
```bash
python strategies/optimize_long_trend.py <TICKER>
```

**Examples:**
```bash
python strategies/optimize_long_trend.py MU
python strategies/optimize_long_trend.py AAPL
```

**Test Scenarios:**
1. **Find optimal volume:** `python strategies/optimize_long_trend.py SPY` → Compare results across 0.1% to 1% increments
2. **Use result:** Take best volume % and use with vol_finder to get exact volume, then run `long_trend.py SPY 10 <VOLUME>`
3. **Ticker-specific tuning:** Run for different tickers (SPY vs MU vs AAPL) → Observe optimal % varies by stock

---

### `orders_menu.py`
**What it does:** Interactive menu for inspecting positions and orders. Lists all open orders with details, allows canceling individual orders or canceling all at once. Useful for manual position management.

**Syntax:**
```bash
python strategies/orders_menu.py
```

**Examples:**
```bash
python strategies/orders_menu.py        # Opens interactive menu
```

**Test Scenarios:**
1. **View orders:** Run with 2–3 open limit orders → Menu shows all with prices, status, qty
2. **Cancel one:** Select option to cancel a specific order → Choose order, confirm cancellation
3. **Cancel all:** Select "Cancel All" → Verify all open orders cancel immediately

---

### `position_options.py`
**What it does:** Runs the weekly-option report for every current stock position, showing call/put availability, midpoints, and volume.

**Syntax:**
```bash
python strategies/position_options.py
```

**Examples:**
```bash
python strategies/position_options.py        # Shows options for all held stocks
```

**Test Scenarios:**
1. **Multiple positions:** Hold 3–5 stock positions, run script → Should list weekly options for each
2. **New position:** Add a position, run script → Should appear in output immediately
3. **Closed position:** Close a position, run script → Should no longer appear

---

### `spy_option.py`
**What it does:** Advanced SPY options strategy. Watches ATR-based price levels, launches fresh weekly call or put trades each time a level triggers (with re-arming after position closure).

**Syntax:**
```bash
python strategies/spy_option.py [ATR_RANGE_MULTIPLIER]
```

**Examples:**
```bash
python strategies/spy_option.py             # Default ATR range
python strategies/spy_option.py 1.5         # 1.5x ATR band
python strategies/spy_option.py 2.0         # 2x ATR band
```

**Test Scenarios:**
1. **Default multiplier:** `python strategies/spy_option.py` → Should watch SPY levels and place calls/puts on breakouts
2. **Tighter band:** `python strategies/spy_option.py 1.0` → More frequent signals
3. **Wider band:** `python strategies/spy_option.py 2.0` → Fewer, potentially higher-conviction trades

---

### `uptrend.py`
**What it does:** Reports daily descending-trend breaks for provided tickers. Shows when break occurred, current status (open trade or exited), and P/L if closed.

**Syntax:**
```bash
python strategies/uptrend.py <TICKER> [TICKER ...]
```

**Examples:**
```bash
python strategies/uptrend.py AAPL
python strategies/uptrend.py AAPL MU SPY
python strategies/uptrend.py INTC HSY
```

**Test Scenarios:**
1. **Single ticker:** `python strategies/uptrend.py AAPL` → Show latest trend break and trade status
2. **Multiple tickers:** `python strategies/uptrend.py SPY QQQ IWM` → Compare trend breaks across indices
3. **Track results:** Run daily for same tickers → Observe trend breaks and trade outcomes over time

---

### `find_vol.py`
**What it does:** Tests volume-candle sizes from 0.1% to 1.0% of average daily volume and recommends which size provides the best breakout signal quality for the long_trend.py strategy.

**Syntax:**
```bash
python strategies/find_vol.py <TICKER>
```

**Examples:**
```bash
python strategies/find_vol.py MU
python strategies/find_vol.py AAPL
```

**Test Scenarios:**
1. **Recommended size:** `python strategies/find_vol.py SPY` → Note recommended candle size (e.g., 0.5% = 35,000 volume)
2. **Use in long_trend:** Take recommended size and run `python strategies/long_trend.py SPY 2 35000`
3. **Compare tickers:** Run on multiple tickers (SPY, IWM, QQQ) → Observe how recommendation varies

---

### `fomo_market.py`
**What it does:** Derives entry, stop, and target levels from current market price, then launches fomo_trade.py. Refreshes levels daily at 2:00 PM Pacific to prepare for next-day trading.

**Syntax:**
```bash
python strategies/fomo_market.py <TICKER> <NUM_STOCKS>
```

**Examples:**
```bash
python strategies/fomo_market.py AAPL 2
python strategies/fomo_market.py MU 5
```

**Test Scenarios:**
1. **Intraday:** `python strategies/fomo_market.py SPY 1` → Should calculate levels from current price and trade immediately
2. **Refresh at 2 PM:** Run in morning, then again at 2:05 PM ET → Verify levels recalculate from current price
3. **Overnight hold:** Run late afternoon → Trade holds levels overnight, next run refreshes

---

### `fomo_trailing_trade.py`
**What it does:** Similar to fomo_trade.py but with trailing stop logic. After target 1 hit, instead of moving stop to breakeven, maintains a trailing stop below recent candle lows for extended profit capture.

**Syntax:**
```bash
python strategies/fomo_trailing_trade.py
```

**Test Scenarios:**
1. **Multi-leg entry:** Add symbols to fomo_trade.txt → Place all entry orders simultaneously
2. **Trailing behavior:** On target 1 hit, observe stop follows below recent lows instead of fixed breakeven
3. **Extended profits:** Compare P/L to standard fomo_trade.py → Trailing should capture more upside on strong trends

---

### `debug_daily_trend.py`
**What it does:** Diagnostic tool for find_daily_trend.py. Shows detailed analysis of how the trend-break algorithm evaluates a specific ticker, useful for tuning detection sensitivity.

**Syntax:**
```bash
python strategies/debug_daily_trend.py <TICKER>
```

**Examples:**
```bash
python strategies/debug_daily_trend.py AAPL
python strategies/debug_daily_trend.py MU
```

**Test Scenarios:**
1. **Analyze breakout:** `python strategies/debug_daily_trend.py MU` → See detailed trend calculations
2. **Verify detection:** Compare debug output to actual breakout on chart
3. **Adjust threshold:** If sensitivity too high/low, use debug output to guide threshold tuning

---

### `draw_chart.py`
**What it does:** Draws price/volume charts for stocks or options with customizable candle timeframe and lookback period.

**Syntax:**
```bash
python strategies/draw_chart.py <TICKER> [TIMEFRAME] [LOOKBACK_DAYS]
```

**Examples:**
```bash
python strategies/draw_chart.py AAPL
python strategies/draw_chart.py MU 15min 10
```

**Test Scenarios:**
1. **Default chart:** `python strategies/draw_chart.py SPY` → Show latest daily candles with volume
2. **Intraday:** `python strategies/draw_chart.py AAPL 5min 5` → Show 5-day of 5-minute candles
3. **Option chart:** `python strategies/draw_chart.py AAPL260911C00315000` → Chart option price action

---

### `mkt-open.py`
**What it does:** Market-open automation. Checks account status at market open, initializes trading for the day, and optionally launches pre-configured strategies.

**Syntax:**
```bash
python strategies/mkt-open.py
```

**Test Scenarios:**
1. **Check account:** Run at 9:31 AM ET → Should verify buying power and margin status
2. **Status report:** See current balance and open positions
3. **Launch strategies:** Configure to auto-launch daily-trend scanning

---

### `mkt-open-trend.py`
**What it does:** Market-open trend scanner. At market open, scans for new breakouts and initiates trades on symbols that break above descending trend.

**Syntax:**
```bash
python strategies/mkt-open-trend.py
```

**Test Scenarios:**
1. **Run at open:** Execute at 9:31 AM ET → Should find and enter breakouts within first 30 minutes
2. **Check results:** After 5 minutes, run status.py to verify positions opened
3. **Compare performance:** Track daily win rate vs non-opening entries

---

### `find_zones.py`
**What it does:** Identifies supply and demand zones using 4 market structure patterns (DBR, RBD, RBR, DBD) on daily charts. Scans historical price action to find consolidation areas where buyers or sellers previously won, creating support/resistance levels.

**Pattern Types:**
- **DBR (Dump Base Rally)** = DEMAND ZONE: Price dumps → consolidates → rallies (buyers won)
- **RBD (Rally Base Dump)** = SUPPLY ZONE: Price rallies → consolidates → dumps (sellers won)
- **RBR (Rally Base Rally)** = SUPPLY ZONE (continuation): Rally → pullback → breaks higher (resistance beaten)
- **DBD (Dump Base Dump)** = DEMAND ZONE (continuation): Dump → bounce → dumps lower (support broken)

**Syntax:**
```bash
python strategies/find_zones.py <TICKER> [--lookback DAYS] [--sensitivity MODE]
```

**Examples:**
```bash
python strategies/find_zones.py AAPL                                    # Default: 500 days, balanced
python strategies/find_zones.py MU --lookback 200                       # Scan 200 days
python strategies/find_zones.py SPY --sensitivity conservative          # Fewer, higher-quality zones
python strategies/find_zones.py INTC --sensitivity aggressive --lookback 100  # More zones, recent
```

**Sensitivity Modes:**
- `conservative`: Stricter criteria, fewer zones, higher quality (min_consol=4, range=0.5%, move=1%)
- `balanced`: Default, good mix (min_consol=3, range=1%, move=0.5%)
- `aggressive`: Looser criteria, more zones, catch all patterns (min_consol=2, range=1.5%, move=0.25%)

**Test Scenarios:**
1. **Find major zones:** `python strategies/find_zones.py AAPL --sensitivity conservative --lookback 500` → Identify primary support/resistance
2. **Recent zones:** `python strategies/find_zones.py MU --lookback 100 --sensitivity aggressive` → All patterns in last 100 days
3. **Use zones for entries:** Identify demand zones for longs, supply zones for shorts on subsequent price approaches

**See Also:** [ZONE_FINDER_GUIDE.md](ZONE_FINDER_GUIDE.md) for comprehensive explanation of pattern calculations and tuning guide.

---

---

## QUICK REFERENCE TABLE

| Category | Program | Core Function | Typical Use |
|----------|---------|---|---|
| **Entry Signals** | find_daily_trend.py | Scan breakouts | Pre-market research |
| | find_zones.py | Market structure zones | Support/resistance identification |
| | demand.py | Find 2x-range patterns | Quick pattern-based entry |
| | zone_finder.py | Supply/demand zones | Reference levels for manual entry |
| | vol_finder.py | Volume-candle sizing | Strategy parameter tuning |
| **Trading** | fomo_trade.py | Multi-symbol FOMO | Primary trading strategy |
| | risk_management.py | Stop/target automation | Position management |
| | tlb_trade.py | Today's breakouts | Morning breakout trading |
| | spray.py | Zone-based entry | Scalping around levels |
| | long_trend.py | Volume-candle trends | Trend-following |
| | closing_bell.py | End-of-day closure | Market close automation |
| **Options** | current_week_option.py | Weekly options list | Options market-making |
| | gocall.py | Call ladder building | Leveraged long positions |
| | spy_option.py | ATR-based options | Options directional trades |
| **Monitoring** | status.py | Position/order view | Real-time account status |
| | email_status.py | Alert on changes | Passive position monitoring |
| | stock_alert.py | Price alerts | Target level notifications |
| **Analysis** | daily_pl.py | Daily P/L summary | End-of-day accounting |
| | max_loss.py | Max daily loss | Compliance/risk check |
| | plot_graph.py | Price/volume charts | Visual analysis |
| | backtest.py | Strategy testing | Historical performance |
| | optimize.py | Parameter grid search | Best parameters |
| **Utility** | daily-stat.py | Equity tracking | Account growth metrics |
| | orders_menu.py | Order management | Manual order control |

---

## NOTES FOR USERS

1. **Configuration Files:**
   - `lists/fomo_trade.txt`: Multi-symbol FOMO trading config (Format: SYMBOL NUM_STOCKS ENTRY STOP TARGET1 TARGET2)
   - `lists/today-breakout`: Auto-generated by find_daily_trend.py, used by tlb_trade.py
   - `lists/*.txt`: Symbol lists for scanning (nasdaq.txt, spy.txt, spray.txt, etc.)
   - `alert_data.txt`: Price alert targets (Format: SYMBOL PRICE)

2. **Continuous Processes:**
   - `email_status.py` and `stock_alert.py` run in infinite loops; press Ctrl+C to stop
   - Logs are written to `*_log.txt` files in strategies/ directory

3. **Alpaca Integration:**
   - Paper trading is configured via env/credentials file
   - All programs authenticate automatically
   - Orders use GTC (Good-Til-Canceled) for limit orders, DAY for most trades

4. **Concurrent Execution:**
   - Most programs can run simultaneously (e.g., fomo_trade.py + risk_management.py)
   - Use different symbols or position sizes to avoid conflicts
   - Check status.py to see active positions

5. **Dynamic Configuration (risk_management.py & fomo_trade.py):**
   - Both bots reload `lists/fomo_trade.txt` on every check/iteration
   - Changes to entry prices are detected and orders automatically updated
   - Changes to stop/target prices are applied without restarting the bot
