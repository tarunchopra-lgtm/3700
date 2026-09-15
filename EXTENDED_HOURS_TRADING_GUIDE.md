# Extended Hours Trading Guide

## Overview
`fomo_trade.py` now supports **extended hours** and **after-hours trading** for stocks. This allows your trading orders to be placed during pre-market (4:00 AM - 9:30 AM ET) and after-hours (4:00 PM - 8:00 PM ET) sessions.

## Enabling Extended Hours Trading

### Default Behavior
Extended hours trading is **ENABLED by default** in `fomo_trade.py`.

### To Disable Extended Hours
Set the environment variable `FOMO_EXTENDED_HOURS` to `0`, `false`, or `no`:

```powershell
# In PowerShell
$env:FOMO_EXTENDED_HOURS = "0"
python strategies/fomo_trade.py

# Or as a one-liner
$env:FOMO_EXTENDED_HOURS = "0"; python strategies/fomo_trade.py
```

```bash
# In Bash/Unix
export FOMO_EXTENDED_HOURS=0
python strategies/fomo_trade.py
```

## What Changed

### 1. New Configuration Flag
- Added `EXTENDED_HOURS_ENABLED` configuration variable
- Reads from environment variable `FOMO_EXTENDED_HOURS` (default: enabled)
- Only applies to **stocks** (crypto and options do not support extended hours on Alpaca)

### 2. Enhanced Order Placement
- Created `_create_limit_order()` helper function that:
  - Accepts `is_stock` parameter to detect stock orders
  - Automatically adds `extended_hours=True` to stock orders when enabled
  - Keeps the extended hours flag off for crypto and options
  
### 3. Updated All Order Types
All order placements now use the new helper:
- **Entry orders** - BUY orders at configured entry price
- **Stop loss orders** - Sell orders at stop price
- **Target 1 & 2 orders** - Sell orders at target prices  
- **Adjusted stop loss orders** - Sell orders at adjusted (breakeven) price
- **Re-entry orders** - BUY orders after stop trigger

### 4. Improved Startup Message
The startup banner now displays the extended hours status:
```
╔════════════════════════════════════════════════════════╗
║                                                        ║
║   FOMO TRADE - MULTI-SYMBOL SIMULTANEOUS TRADING      ║
║   (Dynamic Config - Hot Reload Enabled)               ║
║   ✓ Extended/After-Hours Trading ENABLED              ║
║                                                        ║
╚════════════════════════════════════════════════════════╝
```

## How Extended Hours Works

### Trading Hours (ET - Eastern Time)
- **Pre-Market:** 4:00 AM - 9:30 AM
- **Regular Market:** 9:30 AM - 4:00 PM
- **After-Hours:** 4:00 PM - 8:00 PM

### Order Behavior
- When extended hours is **enabled**, all stock orders remain in effect across all trading hours
- Orders use `TimeInForce.GTC` (Good-Till-Cancelled) which persists through all sessions
- Orders may fill during any of the trading sessions (pre-market, regular, or after-hours)

### Liquidity Considerations
- **Pre-market and after-hours** typically have lower volume and wider bid-ask spreads
- Entry and target prices may be harder to fill during extended hours
- Consider adjusting limit prices for better fill probabilities outside regular hours
- Stop losses may also trigger during extended hours

## Typical Use Cases

### 1. Place Orders Before Market Opens
```
Entry orders placed in pre-market allow you to:
- Enter positions before the regular market session
- Take advantage of early price moves
- Position for market open gaps
```

### 2. Exit Positions After Regular Hours
```
Target and stop orders placed with GTC will work after market close:
- Close positions after earnings announcements (4 PM ET)
- Capture after-hours price movements
- Exit before next market open
```

### 3. Continuous Trading Day
```
One set of orders can span multiple sessions:
- Entry fills in pre-market
- Target 1 fills during regular hours
- Target 2 fills in after-hours
- All from a single trade configuration
```

## Important Notes

⚠️ **Extended hours have lower liquidity** - Be aware that:
- Bid-ask spreads are typically wider
- Order fills may be slower or partial
- Volume is significantly lower
- Price movements can be more volatile

⚠️ **Alpaca Requirements** - Your Alpaca account must:
- Have extended hours trading enabled in account settings
- Have sufficient buying power for the orders
- Be on a plan that supports extended hours

⚠️ **Stocks Only** - Extended hours trading only works for:
- Regular stock symbols (e.g., `AAPL`, `TSLA`, `INTC`)
- **NOT** for crypto (e.g., `BTC/USD`) 
- **NOT** for options

## Configuration Example

Create entries in `lists/fomo_trade.txt`:
```
# These will all trade 24/5 with extended hours enabled
AAPL 2 150 145 155 160
TSLA 4 250 240 260 280
INTC 2 55 50 60 70
```

With extended hours enabled:
- Entry orders can fill any time (4 AM - 8 PM ET)
- Stop and target orders remain active across all sessions
- You can manage positions around the clock

## Verification

When you start `fomo_trade.py`, check the startup message to confirm extended hours status:

```
╔════════════════════════════════════════════════════════╗
║ FOMO TRADE - MULTI-SYMBOL SIMULTANEOUS TRADING        ║
║ ✓ Extended/After-Hours Trading ENABLED                ║  ← This line confirms it's on
║ (Dynamic Config - Hot Reload Enabled)                 ║
╚════════════════════════════════════════════════════════╝
```

## Troubleshooting

### Orders Not Filling Outside Market Hours
- Check if extended hours is enabled: `$env:FOMO_EXTENDED_HOURS`
- Verify your Alpaca account has extended hours enabled
- Check the order limit prices - they may be too strict for lower-liquidity periods
- Monitor the order status in Alpaca dashboard

### Order Fills Are Delayed
- This is normal outside regular market hours due to lower volume
- Consider placing orders during regular market hours for faster fills
- For after-hours, place orders 15+ minutes before desired fill time

### Account Restrictions
- If you see API errors about extended hours, check Alpaca account settings
- Some account types or API keys may have limited extended hours support
- Contact Alpaca support if needed

## Additional Resources

- **Alpaca API Docs:** https://docs.alpaca.markets/
- **Extended Hours Info:** https://www.alpaca.markets/learn/extended-hours-trading
- **Order Types:** https://docs.alpaca.markets/trading/orders/

