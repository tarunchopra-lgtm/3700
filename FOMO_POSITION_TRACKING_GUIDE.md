# FOMO Trade - Updated Position Management System

## Overview

The updated `fomo_trade.py` now includes comprehensive position tracking, automated stop loss adjustment, and market order execution for gap scenarios. All position data persists in tracking files so trades can survive program restarts or crashes.

## New Features

### 1. Position Tracking Files

Each active position creates a status file in the `positions/` directory with the format `{SYMBOL}.txt`.

**File Contents (JSON format):**
```json
{
  "symbol": "MU",
  "entry_price": 100.00,
  "original_stop_loss": 95.00,
  "adjusted_stop_loss": 97.50,
  "target1": 105.00,
  "target2": 110.00,
  "first_target_filled": false,
  "qty": 2,
  "last_updated": "2026-09-14T14:30:00.123456"
}
```

**When file is created:** Immediately when position is filled
**When file is updated:** When Target 1 is hit (stop loss adjusted)
**When file is removed:** When position is fully closed (all targets hit or stopped out)

### 2. Adjusted Stop Loss (1/2 Distance)

When **Target 1 is achieved**, the stop loss automatically moves to **50% of the original distance**.

**Example:**
- Entry Price: $100.00
- Original Stop Loss: $95.00 (Distance: $5.00)
- **Adjusted Stop Loss: $97.50** (Entry - $2.50)

**Formula:**
```
Adjusted Stop Loss = Entry Price - ((Entry Price - Original Stop Loss) / 2)
```

This locks in half your potential loss and improves risk management.

### 3. Gap Detection & Market Orders

The system checks for overnight gaps and executes market orders in two scenarios:

**Scenario A: Gap Down (Opened Below Stop Loss)**
- Automatically exits with a market sell order
- Prevents unexpected losses from gap downs
- Position file is cleaned up immediately

**Scenario B: Gap Up Above Entry**
- Automatically exits with a market sell order  
- Prevents fomo-driven trades from becoming unprofitable re-entries
- Ensures strict risk management

### 4. Trading Phases (Updated)

The monitoring loop now runs through 8 phases every 5 seconds:

1. **PHASE 1:** Place stops/targets when entry fills
2. **PHASE 2:** Move stop to adjusted level when Target 1 hits
3. **PHASE 3:** Detect position fully closed (no more contracts)
4. **PHASE 4:** (Unused - for future expansion)
5. **PHASE 5:** Check for position closure (cleanup)
6. **PHASE 6:** Check for gap opens and exit with market orders
7. **PHASE 7:** Detect stop loss trigger
8. **PHASE 8:** Check for re-entry opportunity

## File Structure

```
workspace/
├── positions/                          # NEW: Position tracking directory
│   ├── MU.txt                         # MU position status
│   ├── INTC.txt                       # INTC position status
│   └── SPY.txt                        # SPY position status
├── strategies/
│   ├── fomo_trade.py                  # Updated main trading script
│   ├── fomo_position_manager.py       # NEW: Helper module for risk_management.py
│   └── risk_management.py             # Can now use position manager functions
└── lists/
    └── fomo_trade.txt                 # Your trade configuration
```

## Using Position Tracking in risk_management.py

The new `fomo_position_manager.py` provides utilities for risk management:

### Quick Example

```python
from strategies.fomo_position_manager import (
    get_all_positions,
    get_position,
    update_position,
    close_position,
    print_position_summary
)

# View all active positions
positions = get_all_positions()

# Get specific position
mu_pos = get_position("MU")
print(f"MU Entry: ${mu_pos['entry_price']:.2f}")
print(f"MU Current Stop: ${mu_pos['adjusted_stop_loss']:.2f}")

# Manual stop loss adjustment (if needed)
update_position("MU", adjusted_stop_loss=98.00)

# Close position manually
close_position("MU")

# Print summary
print_position_summary()
```

### Available Functions in fomo_position_manager.py

#### `get_all_positions() -> list[dict]`
Returns all active positions from tracking files.

#### `get_position(symbol: str) -> dict | None`
Get a specific position by symbol.

#### `update_position(symbol: str, **kwargs) -> bool`
Update position fields. Example:
```python
update_position("MU", adjusted_stop_loss=97.50, qty=1)
```

#### `close_position(symbol: str) -> bool`
Close/remove a position file.

#### `get_positions_by_status(first_target_filled: bool = None) -> list[dict]`
Filter positions by whether first target was hit.

#### `print_position_summary()`
Print a formatted summary of all positions.

## Built-in Functions in fomo_trade.py

These can also be imported directly:

```python
from strategies.fomo_trade import (
    save_position_file,
    load_position_file,
    remove_position_file,
    calculate_adjusted_stop_loss,
    check_gap_open,
    get_all_active_positions,
    update_stop_loss_for_symbol,
    emergency_exit_position,
    get_position_status
)
```

### Key Functions

#### `calculate_adjusted_stop_loss(entry: float, original_stop: float) -> float`
Calculate what the adjusted stop loss should be.

```python
adjusted = calculate_adjusted_stop_loss(100.00, 95.00)
# Returns: 97.50
```

#### `check_gap_open(symbol: str, entry_price: float, stop_loss: float) -> str | None`
Check if market opened with a gap.

Returns:
- `'gap_down'` if opened below stop loss
- `'gap_up_above_entry'` if opened above entry
- `None` if no gap condition

#### `emergency_exit_position(symbol: str, trading_client_instance=None) -> bool`
Force exit a position with market order (for emergency situations).

```python
from strategies.fomo_trade import emergency_exit_position

# Emergency exit MU
success = emergency_exit_position("MU")
```

## Configuration (lists/fomo_trade.txt)

Format remains the same:
```
SYMBOL NUM_STOCKS ENTRY_PRICE STOP_PRICE TARGET1_PRICE TARGET2_PRICE
MU 2 100 95 105 110
INTC 2 55 50 60 70
SPY 2 762 761 763 764
```

## Persistence & Recovery

**If fomo_trade.py crashes or is restarted:**

1. Position files are checked at startup
2. Any existing positions are detected
3. Stops/targets are verified or re-placed
4. Trading continues from the last known state

This ensures you never lose track of open positions.

## Risk Management Features

### Automatic Gap Exits
Prevents holding through unfavorable overnight moves.

### Adjusted Stop Loss
- Original stop: Full risk if stopped out
- Adjusted stop: Half the risk after Target 1 filled
- Limits downside while capturing upside

### Position Persistence
- Files survive program crashes
- Can check position status anytime
- Risk management scripts can read/update positions

## Example: risk_management.py Integration

```python
"""
Example of how risk_management.py can monitor fomo_trade positions
"""

from strategies.fomo_position_manager import get_all_positions, print_position_summary
from datetime import datetime

def check_position_health():
    """Check all positions and alert if any issues"""
    positions = get_all_positions()
    
    for pos in positions:
        # Alert if position has been open 24+ hours
        last_updated = datetime.fromisoformat(pos['last_updated'])
        hours_open = (datetime.now() - last_updated).total_seconds() / 3600
        
        if hours_open > 24:
            print(f"⚠️ {pos['symbol']} open for {hours_open:.1f} hours")
        
        # Check if we're approaching target 2
        if not pos['first_target_filled']:
            print(f"📍 {pos['symbol']}: Entry {pos['entry_price']} → Target1 {pos['target1']}")
        else:
            print(f"✓ {pos['symbol']}: Adjusted Stop {pos['adjusted_stop_loss']} → Target2 {pos['target2']}")

# Run this periodically
check_position_health()
print_position_summary()
```

## Logging

Position tracking logs to console in real-time:

```
[HH:MM:SS] [SYMBOL] Position filled! X shares @ $XXX.XX
  ✓ Stop loss placed at $XXX.XX
  ✓ Target 1 placed at $XXX.XX
  ✓ Target 2 placed at $XXX.XX

[HH:MM:SS] [SYMBOL] ✓ Target 1 FILLED! Position reduced to X shares
  → Moving stop loss to $XXX.XX (1/2 distance from entry)
  ✓ Old stop loss ($XXX.XX) cancelled
  ✓ Stop loss moved to adjusted level at $XXX.XX

[HH:MM:SS] [SYMBOL] ✓ Position fully CLOSED (all contracts filled or stopped)

[HH:MM:SS] [SYMBOL] ⚠ GAP DOWN detected below stop loss!
  → Exiting with MARKET ORDER
  ✓ Market order submitted (ID: xxxxx-xxxx-xxxx-xxxxx)

[CLEANUP] Removed position file for SYMBOL
```

## Troubleshooting

### Position file not found
- Check `positions/` directory exists
- Verify position was actually filled
- Check file permissions

### Stop loss not updating
- Verify position file exists in `positions/`
- Check Target 1 order was actually filled
- Review console logs for error messages

### Gap detection not working
- Ensure market is open
- Verify symbol data is being fetched correctly
- Check logs for price fetch errors

---

**Updated:** September 14, 2026
**Version:** 2.0 (Position Tracking System)
