# Position Territory & Status Tracking Guide

## Overview
The system now maintains a **Position Territory** for each active stock—a master record that tracks the overall status of all brackets for that position.

---

## File Structure

```
positions/
├── INTC_bracket1.txt      ← Detailed order tracking (entry ID, stop ID, target ID)
├── INTC_bracket2.txt      ← Detailed order tracking (entry ID, stop ID, target ID)
└── INTC_position.txt      ← Master position summary (qty, status, prices)
```

### Three-File System Per Stock

| File | Purpose | Content |
|------|---------|---------|
| `_bracket1.txt` | Track Bracket 1 orders | entry_order_id, stop_order_id, target1_order_id |
| `_bracket2.txt` | Track Bracket 2 orders | entry_order_id, stop_order_id, target2_order_id |
| `_position.txt` | Master status summary | Total qty, both bracket statuses, P&L |

---

## Position Status File Example

```json
{
  "symbol": "INTC",
  "qty": 200,
  "entry_price": 55.0,
  "bracket1_status": "pending",
  "bracket2_status": "pending",
  "current_price": 55.0,
  "current_pnl": 0.0,
  "last_updated": "2026-09-16T15:30:45.123456"
}
```

### Status Values
- **pending** — Waiting for target or stop to execute
- **target_filled** — Target order filled (profit)
- **stopped_out** — Stop loss hit (loss)
- **cancelled** — Order was cancelled manually

---

## Territory Lifecycle

### Phase 1: Entry (Position Opens)

```
Action: Two bracket orders placed simultaneously
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Position File Created:
├─ qty: 200 (total, both brackets)
├─ entry_price: 55.0
├─ bracket1_status: "pending"
├─ bracket2_status: "pending"
└─ current_price: 55.0
```

**Bracket Details:**
- **Bracket 1**: 100 shares entered → Stop at 50.0, Target at 60.0
- **Bracket 2**: 100 shares entered → Stop at 50.0, Target at 120.0

---

### Phase 2a: One Bracket Targets (Partial Profit)

```
Action: Bracket 1 target hit @ 60.0 (50% of position exits with profit)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Position File Updated:
├─ qty: 100 (remaining from Bracket 2)
├─ entry_price: 55.0
├─ bracket1_status: "target_filled" ✓ (COMPLETED)
├─ bracket2_status: "pending"        ← Still waiting
└─ current_price: 60.0
```

**Key Point:** Bracket 2 is **BLOCKED** from re-entry because it's still pending.
- Cannot close position and re-enter yet
- Must wait for Bracket 2 to complete

---

### Phase 2b: Second Bracket Also Targets (Full Exit)

```
Action: Bracket 2 target hit @ 120.0 (remaining 50% exits with profit)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Position File Status:
├─ qty: 0 (both brackets exited)
├─ bracket1_status: "target_filled" ✓ (COMPLETED)
├─ bracket2_status: "target_filled" ✓ (COMPLETED)
└─ Position File DELETED ← Territory Cleared
```

**Re-Entry Eligibility Activated:**
- Both brackets complete ✓
- No position exists ✓
- Waiting for price reversal to entry level

---

### Phase 3: Re-Entry Trigger

```
Action: Price drops back to entry level (55.0 ± 1%) after both brackets exited
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

System Action:
1. ✓ Detects both brackets complete
2. ✓ Confirms no position exists
3. ✓ Checks price = 54.7 (within 54.45 - 55.55 range)
4. ✓ NEW Position File Created (resets territory)
5. ✓ TWO NEW bracket orders placed (as if from start)

New Position File:
├─ qty: 200
├─ entry_price: 55.0
├─ bracket1_status: "pending"  ← Fresh start
├─ bracket2_status: "pending"  ← Fresh start
└─ current_price: 54.7
```

---

## Monitoring & Safety

### Prevents Double Entry

**Scenario:** Bracket 1 targets at 60, but Bracket 2 still pending at 58

```
❌ BLOCKED: Cannot re-enter even though price at 60
   Reason: bracket2_status = "pending" (must wait)

After Bracket 2 stops at 50:
✓ bracket1_status = "target_filled"
✓ bracket2_status = "stopped_out"  
✓ Now eligible for re-entry when price = 55
```

### Position Territory Queries

Get all active positions:
```python
active_positions = get_all_active_position_statuses()

# Returns list like:
# [
#   {"symbol": "INTC", "qty": 100, "bracket1_status": "pending", ...},
#   {"symbol": "SPY", "qty": 200, "bracket1_status": "target_filled", ...}
# ]
```

Get single symbol status:
```python
status = load_position_status("INTC")
print(f"INTC Bracket 1: {status['bracket1_status']}")
print(f"INTC Bracket 2: {status['bracket2_status']}")
```

---

## Key Differences from Old System

| Aspect | Old System | New System |
|--------|-----------|-----------|
| Position Tracking | Single position file | 3 files (2 bracket + 1 position) |
| Status Visibility | Need to query Alpaca | Read from `_position.txt` |
| Territory Clarity | Complex multi-phase states | Simple "pending" → "filled" |
| Re-Entry Logic | Query multiple order IDs | Check one `_position.txt` file |
| P&L Tracking | Not explicit | Stored in position file |
| Portfolio Overview | Manual loop through orders | `get_all_active_position_statuses()` |

---

## Best Practices

1. **Always check position file** before manual re-entry
2. **Monitor territory status** for each stock individually
3. **Use portfolio summary** for daily reporting
4. **Clean logs** when position files are deleted (territory clear)
5. **Emergency exit** cleans up all 3 files (territory reset)

---

## Example: Complete Trade Cycle

```
TIME    ACTION                          FILES EXIST
────────────────────────────────────────────────────
09:30   Entry orders placed             _bracket1.txt ✓
                                        _bracket2.txt ✓
                                        _position.txt ✓

09:45   Bracket 1 enters @ 55.0         (files updated)

10:15   Bracket 1 targets @ 60.0        _bracket1.txt DELETED
                                        _bracket2.txt ✓
                                        _position.txt ✓ (qty=100 now)

10:45   Bracket 2 stops @ 50.0          _bracket2.txt DELETED
                                        _position.txt DELETED ✓

11:00   Price reverses to 54.9          _position.txt CREATED ✓ (re-entry)
        New brackets placed             _bracket1.txt ✓
                                        _bracket2.txt ✓
```

---

## Integration with Other Systems

### With `risk_management.py`:
- Can query position status before emergency exit
- Emergency exit cleans up all territory files

### With Daily Reports:
- Aggregate all `*_position.txt` files for portfolio P&L
- Track active positions by date

### With Monitoring Dashboard:
- Display bracket1_status + bracket2_status per symbol
- Show total qty still at risk
- Alert if re-entry conditions met

---

Generated: 2026-09-16
System: Dual-Bracket Order Architecture with Position Territory Tracking
