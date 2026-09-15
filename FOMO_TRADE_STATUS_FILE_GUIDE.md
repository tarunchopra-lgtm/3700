# FOMO Trade - Position Status File Guide

## Overview
fomo_trade.py now maintains persistent position status files that allow the program to resume from exact state after restart.

---

## Position Status File Location

**Directory:** `positions/`

**Files:** `positions/TICKER.txt` (e.g., `positions/COHR.txt`, `positions/SPY.txt`)

---

## File Format (JSON)

```json
{
  "symbol": "COHR",
  "entry_price": 271.0,
  "original_stop_loss": 270.0,
  "adjusted_stop_loss": 270.5,
  "target1": 272.0,
  "target2": 273.0,
  "first_target_filled": true,
  "qty": 1.0,
  "last_updated": "2026-09-15T05:48:52.389358"
}
```

---

## Field Descriptions

| Field | Description | When Updated |
|-------|-------------|--------------|
| `symbol` | Trading ticker (COHR, SPY, etc.) | Created on entry fill |
| `entry_price` | Buy price when position filled | Created on entry fill |
| `original_stop_loss` | Initial stop loss price from config | Created on entry fill |
| `adjusted_stop_loss` | Moves to 1/2 distance from entry after Target 1 fills | Updated when Target 1 fills |
| `target1` | First target price from config | Created on entry fill |
| `target2` | Second target price from config | Created on entry fill |
| `first_target_filled` | `true` when Target 1 order executes | Updated when Target 1 fills |
| `qty` | Current position quantity (decreases after targets fill) | Updated on any fill |
| `last_updated` | ISO timestamp of last update | Every file update |

---

## Lifecycle Example: COHR Trade

### Step 1: Entry Order Placed
```
fomo_trade.txt: COHR 2 271 270 272 273
Program places: BUY 2 @ $271.00
```
- No status file yet (order pending)

### Step 2: Entry Fills (Position Created)
```
positions/COHR.txt is CREATED with:
{
  "entry_price": 271.0,
  "original_stop_loss": 270.0,
  "adjusted_stop_loss": 270.5,  ← 1/2 distance: (271 - 270) / 2 = 0.5
  "target1": 272.0,
  "target2": 273.0,
  "first_target_filled": false,
  "qty": 2.0
}
```
- Program places Stop Loss @ 270.0 and Target 1 @ 272.0

### Step 3: Target 1 Fills (1 share sold)
```
positions/COHR.txt is UPDATED:
{
  "adjusted_stop_loss": 270.5,  ← Already moved to 1/2 distance
  "first_target_filled": true,  ← Now TRUE
  "qty": 1.0                     ← Reduced from 2 to 1
}
```
- Program moves Stop Loss to Breakeven (270.5)
- Program places Target 2 @ 273.0

### Step 4: Target 2 Fills (last share sold)
```
positions/COHR.txt is DELETED
```
- Position fully closed
- Trade removed from tracking

### Step 5: Re-entry After Stop Loss
```
New positions/COHR.txt created with fresh entry
Cycle repeats...
```

---

## Program Restart Behavior

### On Startup
1. Reads `lists/fomo_trade.txt` for current config
2. Scans `positions/` directory for existing trades
3. For each existing position file:
   - Loads entry price, stops, targets, qty, target fill status
   - Reconciles with live Alpaca account
   - Detects which orders (stops/targets) are still open
   - Resumes from exact state

### Example: Restart During Target 1 Pending
```
Before Control-C:
- Entry filled: 2 @ $271.00
- Stop order open @ 270.00
- Target 1 order open @ 272.00
- Target 2 NOT yet placed (waiting for T1 fill)

positions/COHR.txt contains this state

After restart:
- Program loads positions/COHR.txt
- Detects: entry filled, stop/target1 open, target2 not placed yet
- Resumes monitoring for Target 1 fill
- When T1 fills: moves stop to breakeven, places T2
```

---

## Manual Intervention

### Editing Position Files
You can manually edit `positions/TICKER.txt` JSON to:
- Adjust stop loss price (if needed)
- Mark as `first_target_filled: true` to skip Target 1 logic
- Change `qty` if you modify position in Alpaca

**Example:** Force Target 1 filled status
```json
{
  "entry_price": 271.0,
  "first_target_filled": true,  ← Change to TRUE
  "adjusted_stop_loss": 270.5,
  ...
}
```
Next run will skip Target 1 logic and use adjusted stops/Target 2.

### Deleting Position Files
Remove `positions/TICKER.txt` to forget position. Program will:
- No longer track this position
- Consider it a fresh entry if config still in fomo_trade.txt
- Not resume from previous state

---

## Integration with fomo_trade.txt

The config file (`lists/fomo_trade.txt`) and position status files work together:

| Scenario | Behavior |
|----------|----------|
| Config: COHR 2 271 270 272 273<br>Position file exists | Program resumes from file state, uses config as reference |
| Config: COHR removed<br>Position file exists | Program continues managing existing position until close, then forgets |
| Config: COHR 2 271 270 272 273<br>No position file | Program places new entry order |
| Position file has: qty=0<br>Position still on Alpaca | Program detects mismatch, creates new file |

---

## Console Display (Every 10 Seconds)

```
[14:35:20] 🔍 Market & Config Check (2 symbols)...

  📊 COHR: Qty=1.0 | Current: $271.50 | Fill: $271.00 | Next: Breakeven: $270.50
  📊 SPY: Qty=2.0 | Current: $445.20 | Fill: $444.80 | Next: Target 1: $446.00
```

Shows:
- **Current:** Live price from Alpaca
- **Fill:** Entry price (what you bought at)
- **Next:** Which order is pending (Stop/Breakeven/T1/T2)

---

## Troubleshooting

**Q: Position file shows qty=2 but account shows qty=1?**
- Program detected mismatch on next restart
- Will update file to match Alpaca account state
- Stops/targets will be reconciled

**Q: Position file doesn't exist but position is open in Alpaca?**
- Create file manually in `positions/TICKER.txt` with correct values
- Program will pick it up on next startup
- Or: delete position from Alpaca and let fomo_trade.py place fresh entry

**Q: Want to abandon a position?**
- Delete the position file from `positions/`
- Close position manually in Alpaca
- Program won't track it anymore

**Q: Program restarted mid-trade, now showing duplicate orders?**
- File shows what state should be
- Alpaca may show extra orders
- Program reconciles this on first cycle and cleans up
- Check console output for "Reconciling with existing positions"

---

## File Lifecycle

```
Position Life Cycle:
├─ Entry order placed
│  ├─ (File doesn't exist yet)
│  └─ Order fills
│
├─ Entry filled
│  ├─ positions/TICKER.txt CREATED
│  └─ Stop + Target 1 placed
│
├─ Target 1 fills
│  ├─ positions/TICKER.txt UPDATED
│  │  ├─ first_target_filled = true
│  │  ├─ adjusted_stop_loss (at 1/2 distance)
│  │  └─ qty reduced
│  └─ Stop moved to breakeven, Target 2 placed
│
├─ Target 2 fills (or stop hit)
│  ├─ Position qty → 0
│  ├─ positions/TICKER.txt DELETED
│  └─ All orders closed
│
└─ New entry opportunity
   └─ positions/TICKER.txt CREATED (fresh cycle)
```

---

## Summary

- ✅ Position files automatically maintained in `positions/TICKER.txt`
- ✅ All trade metadata persists across restarts
- ✅ Program resumes from exact state after Control-C or crash
- ✅ Config (fomo_trade.txt) + Status Files (positions/) work together
- ✅ Manual editing possible if needed
- ✅ Visible every 10 seconds in console output
