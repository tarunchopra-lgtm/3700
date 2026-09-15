# FOMO Trade - Agent/Prompt Reference

**Use this file to prompt Copilot about fomo_trade.py changes**

---

## Key Principles (Memorized)

**NEVER FORGET THESE WHEN ASKING FOR CHANGES:**

1. **fomo_trade.txt IS THE SOURCE OF TRUTH**
   - Every order, entry, stop, target comes FROM this file
   - Config changes should immediately update live orders
   - Always check: "Is this change reflected in fomo_trade.txt handling?"

2. **CONFIG UPDATES MUST CANCEL & REPLACE**
   - If entry order pending and config changes: cancel old, place new
   - If position filled: ignore config changes (locked in)
   - If removed from config: stop monitoring

3. **TWO-PHASE TARGET EXECUTION**
   - Entry fill: Place Stop + Target 1 ONLY
   - Target 1 fills: Move stop to breakeven, THEN place Target 2
   - Never place all three simultaneously (qty conflict)

4. **STATE PERSISTENCE**
   - Position files in `positions/TICKER.txt` track everything
   - On restart: load files, reconcile with Alpaca, resume exact state
   - If no file: fresh entry

5. **10-SECOND UNIFIED CHECK**
   - Everything runs every 10 seconds: market check + config check
   - Single Alpaca API call per cycle
   - Status display: Qty | Current Price | Fill Price | Next Order

6. **EXTENDED HOURS BY DEFAULT**
   - Stocks: added_hours=True (4-9:30 AM, 4-8 PM ET)
   - Crypto: 24/7 (flag ignored)
   - Options: DAY orders only

---

## Quick Prompt Templates

### Template 1: Add New Feature
```
For fomo_trade.py:

Goal: [What you want]

Requirements:
- Must read from fomo_trade.txt config
- Must update on every 10-second check
- Must log all actions with [SYMBOL] prefix
- Must update position files if position affected
- Extended hours support (if applicable)
- Restart-safe (state in positions/TICKER.txt)

Implementation should:
1. [First step]
2. [Second step]
3. [Third step]

Current behavior: [What it does now]
Desired behavior: [What it should do]
```

### Template 2: Fix Config Handling Bug
```
Bug: fomo_trade.txt config not being updated live

Current: [Describe what happens]
Expected: [What should happen per heartline config principle]

The fix must:
- Detect config change every 10s
- For PENDING orders (no position): cancel & replace
- For FILLED positions: ignore (locked in)
- Update trade_states immediately
- Log what happened

File to check: strategies/fomo_trade.py config reload section (lines ~640-700)
```

### Template 3: Order Placement Issue
```
Problem with order placement for [SYMBOL]:

Current flow:
Entry → [what happens]

Correct flow per design:
Entry → Stop + Target 1 → [wait T1 fill] → Move Stop + Place T2 → [wait exits]

Issue: [What's broken]
Solution: [What needs to happen]

Must update:
- trade_states[symbol]["state"] flags
- position files after fills
- Alpaca order placement
```

### Template 4: Position File / Restart Issue
```
Problem with persistence across restart:

Current: [What happens on restart]
Expected: [What should happen per design]

Files involved:
- positions/COHR.txt (position tracking)
- lists/fomo_trade.txt (live config)
- trade_states dict (in-memory state)

The fix needs to:
1. [Handle on startup]
2. [Reconcile with Alpaca]
3. [Resume from saved state]
```

---

## What To Say When Asking

### Good Approach:
✅ "Update fomo_trade.py to detect config changes and cancel/replace pending entry orders"
✅ "When fomo_trade.txt entry price changes, cancel old order and place new one"
✅ "Config file is heartline - changes should immediately affect live orders"

### What NOT to Say:
❌ "Make changes to fomo_trade" (too vague)
❌ "Add feature X" (without linking to config)
❌ "Fix order placement" (without explaining which phase)

### Always Include:
- What change triggered it (config update, position fill, etc.)
- What file to read (fomo_trade.txt)
- What should happen (specific order of actions)
- Which phase of state machine
- File updates needed (position files, state tracking)

---

## Files & Locations (Quick Reference)

```
Main Script:     strategies/fomo_trade.py
Config File:     lists/fomo_trade.txt
Position Files:  positions/TICKER.txt (e.g., positions/COHR.txt)
Email Utility:   roles/email_notify.py
Credentials:     roles/credentials.py

Key Functions:
- _read_config_from_file() - Reads fomo_trade.txt
- _validate_and_add_trade() - Validates config line
- _create_limit_order() - Creates order (with extended_hours support)
- save_position_file() - Saves state to positions/TICKER.txt
- load_position_file() - Loads state on restart

Key Variables:
- trade_states[symbol] - Active trades in memory
- previous_configs[symbol] - Last known config (detects changes)
- stops_placed - Set of symbols with positions filled
```

---

## Checklist Before Asking for Changes

**Before you ask for a fomo_trade.py change, verify:**

- [ ] Change affects which file? (fomo_trade.txt / fomo_trade.py / positions/)
- [ ] Is this a CONFIG CHANGE or STATE CHANGE?
- [ ] Which PHASE does this affect? (0=config, 1=entry, 2=T1, 3=T2, 4=reentry)
- [ ] Does it need to update position files?
- [ ] Does it need to cancel/replace orders?
- [ ] Should it work on restart?
- [ ] Should extended hours be supported?
- [ ] Need to log output? (use [SYMBOL] prefix + emoji)

---

## Common Patterns (Use These)

### Pattern 1: Config Affects Pending Order
```python
# Pseudo-code structure:
if symbol in trade_states:
    state = trade_states[symbol]["state"]
    if state["entry_order_id"] and not position_filled:
        # Cancel old order
        cancel_order(state["entry_order_id"])
        # Place new order
        place_new_order()
        # Update state
        state["entry_order_id"] = new_order_id
```

### Pattern 2: Detect Config Change
```python
# Compare current vs previous
current_config = (num_stocks, entry, stop, t1, t2)
previous_config = previous_configs[symbol]
if current_config != previous_config:
    # Config changed!
    # Handle it...
    previous_configs[symbol] = current_config
```

### Pattern 3: Update Position File
```python
save_position_file(
    symbol,
    entry_price=cfg['entry_price'],
    stop_loss=cfg['stop_price'],
    target1=cfg['target1_price'],
    target2=cfg['target2_price'],
    adjusted_stop_loss=state['adjusted_stop_loss'],
    first_target_filled=state['first_contract_sold'],
    qty=position_qty
)
```

### Pattern 4: Status Display
```python
# Show: Qty | Current Price | Fill Price | Next Order
print(f"  📊 {symbol}: Qty={qty:.2f} | Current: ${current_price:.2f} | Fill: ${fill_price:.2f} | Next: {next_order}")
```

---

## Red Flags (Stop and Ask First)

🚩 **ASK BEFORE IMPLEMENTING IF:**
- Change adds NEW orders beyond (Entry + Stop + T1 + T2)
- Change bypasses position file persistence
- Change removes 10-second config check
- Change ignores extended_hours flag
- Change places orders without checking position state
- Change modifies fomo_trade.txt programmatically (config should be user-edited only)

---

## Session Context (Save This)

**When starting a new session, say:**

"I'm working with fomo_trade.py - the main trading engine for FOMO trades. 
Configuration file is lists/fomo_trade.txt (the heartline).
Check /memories/fomo_trade_architecture.md for full design details.

Current request: [your request]"

**Then ask:** "Based on the architecture, should I [your ask]?"

---

## Design Documents to Reference

When you want help with fomo_trade, these docs exist in memory:

1. **fomo_trade_architecture.md** - Full design, state machine, config philosophy
2. **fomo_trade_workflows.md** - Common workflows, troubleshooting, examples
3. **This file (fomo_trade_reference.md)** - Quick prompting guide

**Memory Location:** `/memories/fomo_trade_reference.md`
**Local Copy:** `FOMO_TRADE_REFERENCE.md` (this file, in root)

---

## Success Criteria

**Your fomo_trade.py change is GOOD if:**
- ✅ Reads from fomo_trade.txt correctly
- ✅ Updates immediately on config change (≤10s)
- ✅ Cancels/replaces orders for pending entries
- ✅ Saves position state to positions/TICKER.txt
- ✅ Restarts cleanly (loads from position files)
- ✅ Handles extended hours (stocks only)
- ✅ Logs all actions with [SYMBOL] and emoji
- ✅ No syntax errors
- ✅ Status displays every 10s: Qty | Current $ | Fill $ | Next Order
