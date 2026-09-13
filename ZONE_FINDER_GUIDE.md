# ZONE FINDER (find_zones.py) - Complete Guide

## Overview

`find_zones.py` identifies **Supply & Demand Zones** in daily price charts using 4 distinct market structure patterns: **DBR, RBR, RBD, and DBD**.

These zones represent areas where buyers and sellers have taken control of price, creating levels with strong technical significance.

---

## THE FOUR PATTERN TYPES

### Pattern 1: DBR (Dump Base Rally) - **DEMAND ZONE** ✓

**Visual Pattern:**
```
    Entry
      ↑
      |
      +--------+   ← Up candle (rally from base)
      |        |
      |        | Base (consolidation)
      |        | Buyers accumulating here
      +--------|   ← Down candle (dump)
      |
   Support
```

**What It Means:**
- Price gets dumped hard (down candle)
- Buyers step in and hold the line (consolidation/base)
- Price recovers higher (up candle)

**Psychology:**
Sellers panicked and pushed price down. But at this lower level, **buyers found it attractive and accumulated**, holding price steady for several candles. When demand proved strong enough, price rallied away.

**Zone Interpretation:**
- The BASE (consolidation area) = **DEMAND ZONE**
- If price returns here later = Strong support where buyers previously won
- Good entry zone for long trades
- Common after panic selling

**Real-World Example:**
AAPL dumps from $180 to $175 (dump candle). Over next 3 days, it consolidates between $175-$176 (buyers buying the dip). Then on day 4, it rallies to $178 (up candle). The $175-$176 zone is the demand zone where buyers accumulated.

---

### Pattern 2: RBD (Rally Base Dump) - **SUPPLY ZONE** ✗

**Visual Pattern:**
```
      ↑
      |
   Resistance
      |
      +--------|   ← Down candle (dump/rejection)
      |        |
      |        | Base (consolidation)
      |        | Sellers taking control
      +--------+   ← Up candle (rally to resistance)
      |
      |
```

**What It Means:**
- Price rallies up hard (up candle)
- Price consolidates at this high level (base)
- Price dumps down (down candle - reversal)

**Psychology:**
Buyers pushed price higher and tested a resistance level. But at this level, **sellers overwhelmed them and took control**. The consolidation zone represents where sellers won the battle.

**Zone Interpretation:**
- The BASE = **SUPPLY ZONE** (where selling pressure emerged)
- If price returns here later = Strong resistance where sellers previously won
- Good short entry zone
- Common after rallies run out of steam

**Real-World Example:**
SPY rallies from $420 to $425 (up candle). Over next 3 days, it consolidates between $424-$425 (sellers rejecting higher prices). Then on day 4, it dumps to $422 (down candle). The $424-$425 zone is the supply zone where sellers overcame buyers.

---

### Pattern 3: RBR (Rally Base Rally) - **SUPPLY ZONE** (Continuation)

**Visual Pattern:**
```
      ↑
      |
   Breakout
      |
      +--------+   ← Up candle #2 (breakout higher)
      |        |
      |        | Base (consolidation)
      |        | Buyers pausing briefly
      +--------|   ← Up candle #1 (initial rally)
      |
```

**What It Means:**
- Price rallies (up candle #1)
- Price consolidates briefly (base/pullback)
- Price breaks higher (up candle #2, above candle #1's close)

**Psychology:**
Initial buyers pushed price up. A brief pullback occurred (could be profit-taking or pullback). But the consolidation zone held support, and **new buying pushed price even higher**, confirming the strength of the uptrend.

**Zone Interpretation:**
- The BASE = **SUPPLY ZONE** (but different from RBD!)
  - This is NOT a reversal point
  - This is a "resistance that was beaten" during an uptrend
  - If price comes back, this is a support level (former resistance becomes new support)
- Used by trend traders as pullback entry levels
- Breakout often leads to further upside

**Why "Supply" not "Demand"?**
- Because it forms at resistance levels during strength
- When price breaks above it, sellers are overwhelmed (lack of supply)
- This is resistance turned support

**Real-World Example:**
MU rallies from $950 to $965 (up candle). Pulls back to consolidate $960-$962 for 3 days (base). Then breaks to $975 (up candle #2). The $960-$962 zone is supply (old resistance), but it acted as support during the consolidation.

---

### Pattern 4: DBD (Dump Base Dump) - **DEMAND ZONE** (Continuation)

**Visual Pattern:**
```
      |
      |
   Support
      |
      +--------|   ← Down candle #2 (break lower)
      |        |
      |        | Base (consolidation)
      |        | Buyers losing control
      +--------+   ← Down candle #1 (initial dump)
      |
     Break
```

**What It Means:**
- Price dumps (down candle #1)
- Price consolidates briefly (base/bounce)
- Price dumps again lower (down candle #2, below candle #1's close)

**Psychology:**
Initial selling dumped price lower. Buyers created a small base, thinking they could hold support. But the selling pressure was too strong - **sellers broke through the support level and dumped even lower**.

**Zone Interpretation:**
- The BASE = **DEMAND ZONE** (but this one FAILED!)
  - Buyers tried to accumulate but failed
  - This is broken support
  - This is a bearish continuation pattern
  - Often marks acceleration of downtrend
- Traders use this as confirmation of bearish momentum
- Area to AVOID going long (failed support)

**Why "Demand"?**
- Because it initially formed as a support/demand level
- But unlike successful DBR, buyers couldn't hold it
- This represents FAILED demand

**Real-World Example:**
INTC dumps from $50 to $48 (down candle). Bounces to consolidate $48-$49 for 2 days (buyers trying to hold). Then dumps to $46 (down candle #2). The $48-$49 zone is failed demand - buyers couldn't hold support.

---

## HOW THE CALCULATIONS WORK

### Core Detection Algorithm

Each pattern follows this logic:

**1. Find Initial Move Candle**
```python
# Look for either UP or DOWN candle depending on pattern
if pattern == DBR:
    need = "down candle"
elif pattern == RBD:
    need = "up candle"
# etc.
```

**2. Calculate Move Size**
```python
move_size = abs(candle.close - candle.open)
move_pct = (move_size / candle.open) * 100

# Filter: Only significant moves matter
if move_pct < min_prior_move_pct:
    skip this candle
```

**3. Scan for Consolidation**
```python
# After the move, scan next 10 candles for small-range candles
max_allowed_range = move_size * (consolidation_range_pct / 100)

for each candle in next 10:
    if candle.range > max_allowed_range:
        break  # Consolidation ended
    
    zone_high = max(zone_high, candle.high)
    zone_low = min(zone_low, candle.low)
```

**4. Verify Consolidation Size**
```python
consolidation_candles = count of small-range candles
if consolidation_candles < min_consolidation_candles:
    skip this pattern  # Base too small
```

**5. Confirm Breakout Direction**
```python
# Check candle after consolidation
if pattern == DBR:
    need next candle to be UP (close > open)
elif pattern == RBD:
    need next candle to be DOWN (close < open)
# etc.
```

**6. Calculate Zone**
```python
zone_high = highest point during consolidation
zone_low = lowest point during consolidation
zone_size = zone_high - zone_low
```

### Zone Size Meaning

The zone size represents the **area of price compression**:

```
BIG ZONE (>$2):
    |
 $10 +---------+
     |         |  Large consolidation = Important level
 $8  |         |  More time spent = More significance
     |         |  More buyers/sellers involved
 $6  +---------+
     |

SMALL ZONE (<$0.50):
    |
 $10 +--+
     |  |  Tight consolidation
 $9.7   |  Quick equilibrium
     +--+
```

**Interpretation:**
- **Larger zones** = More significant (more participants)
- **Tighter zones** = More efficient (quick equilibrium reached)

Both can be valid, depending on your trading style.

---

## CONFIGURABLE PARAMETERS

### 1. `min_consolidation_candles`

**What it controls:** Minimum number of small-range candles needed to qualify as a base

**Values:**
- `2` = Very loose (even quick bases count)
- `3` = Default balanced (middle ground)
- `4` = Strict (only solid bases count)
- `5+` = Very strict (only major bases)

**Trade-off:**
```
Lower → More zones found → More noise, more false signals
Higher → Fewer zones found → Higher quality, potential misses
```

**Tuning Guide:**
- **Aggressive:** Use 2 (catch every consolidation)
- **Balanced:** Use 3 (good mix)
- **Conservative:** Use 4-5 (only established bases)

**Real Example:**
```
Stock dumps, then consolidates for:
- 1 candle: Doesn't qualify as zone (too fast)
- 2 candles: min_consolidation=2 would find it
- 3 candles: min_consolidation=2,3 would find it
- 5 candles: min_consolidation=2,3,4,5 would find it
```

---

### 2. `consolidation_range_pct`

**What it controls:** Maximum candle range during consolidation, as % of the prior move

**Formula:**
```
max_allowed_range = prior_move_size * (consolidation_range_pct / 100)

Example:
- Prior move (down) = $5
- consolidation_range_pct = 1.0 (1%)
- max_allowed_range = $5 * 0.01 = $0.05

So consolidation candles can only have range ≤ $0.05
```

**Values:**
- `0.5` = Very tight (0.5% of move)
- `1.0` = Balanced (default)
- `1.5` = Loose (1.5% of move)
- `2.0` = Very loose (2% of move)

**Trade-off:**
```
Lower → Tighter consolidations → Cleaner zones, fewer found
Higher → Wider consolidations → More zones found, messier
```

**Real Example:**
```
Move down: $10
consolidation_range_pct = 1.0

max_allowed = $10 * 0.01 = $0.10

Next candles:
- Range $0.05: ✓ Qualifies as consolidation
- Range $0.08: ✓ Qualifies as consolidation  
- Range $0.15: ✗ Too wide, consolidation ends

With consolidation_range_pct = 2.0:
- max_allowed = $0.20
- All three would qualify
```

**Tuning Guide:**
- **Conservative:** 0.5% (tight, clean zones)
- **Balanced:** 1.0% (good balance)
- **Aggressive:** 1.5-2.0% (find more zones)

---

### 3. `min_prior_move_pct`

**What it controls:** Minimum size of initial move, as % of candle open price

**Formula:**
```
move_pct = (move_size / candle.open) * 100

Example:
- Down candle: open=$100, close=$99
- Move size = $1
- Move pct = ($1 / $100) * 100 = 1%

With min_prior_move_pct = 0.5:
- This move (1%) qualifies ✓

With min_prior_move_pct = 1.5:
- This move (1%) doesn't qualify ✗
```

**Values:**
- `0.25%` = Catches even small moves (very sensitive)
- `0.5%` = Balanced (default)
- `1.0%` = Only significant moves
- `2.0%` = Only major moves

**Trade-off:**
```
Lower → More sensitivity, catches small moves → More zones/noise
Higher → Only big moves → Higher quality, fewer zones
```

**Real Example:**
```
Three down candles:
1. Open $100, Close $99.50 = 0.5% move
   - min_prior=0.25: ✓ Found
   - min_prior=0.5: ✓ Found
   - min_prior=1.0: ✗ Skipped (too small)

2. Open $100, Close $99 = 1% move
   - min_prior=0.5: ✓ Found
   - min_prior=1.0: ✓ Found

3. Open $100, Close $97 = 3% move
   - All thresholds: ✓ Found
```

**Tuning Guide:**
- **Aggressive:** 0.25% (find everything)
- **Balanced:** 0.5% (default)
- **Conservative:** 1.0% (only significant moves)

---

### 4. `lookback_days`

**What it controls:** How many days of historical data to scan

**Values:**
- `50` = Last ~2 months (recent zones only)
- `100` = Last ~4-5 months (standard)
- `200` = Last ~9-10 months (wider history)
- `500` = Last ~2 years (very comprehensive)

**Trade-off:**
```
Shorter → Recent zones only, misses older structure
Longer → Captures all zones, historical perspective
```

**Note:** More data = Slightly slower (but usually <10s)

**Tuning Guide:**
- For swing trading: 100-200 days
- For day trading: 50-100 days
- For long-term analysis: 500+ days

---

## SENSITIVITY MODES (Pre-configured Sets)

Instead of tuning each parameter, use built-in modes:

### Conservative Mode
```
--sensitivity conservative

Settings:
  min_consolidation=4      (require solid bases)
  consolidation_range_pct=0.5  (very tight consolidation)
  min_prior_move_pct=1.0   (only big moves)

Result: FEWER zones, HIGHER quality
Use for: Finding major structural levels
```

### Balanced Mode (Default)
```
--sensitivity balanced

Settings:
  min_consolidation=3      (good balance)
  consolidation_range_pct=1.0  (normal consolidation)
  min_prior_move_pct=0.5   (good mix)

Result: MODERATE zones, MODERATE quality
Use for: General analysis
```

### Aggressive Mode
```
--sensitivity aggressive

Settings:
  min_consolidation=2      (catch all bases)
  consolidation_range_pct=1.5  (wider consolidations)
  min_prior_move_pct=0.25  (even small moves)

Result: MORE zones, LOWER quality/more noise
Use for: Finding all potential levels
```

---

## USAGE EXAMPLES

### Basic Usage
```bash
python strategies/find_zones.py AAPL
```
Scans AAPL with default settings (balanced, 500 days)

### Different Sensitivities
```bash
# Conservative - fewer, higher-quality zones
python strategies/find_zones.py MU --sensitivity conservative

# Aggressive - more zones, explore all patterns
python strategies/find_zones.py SPY --sensitivity aggressive
```

### Custom Lookback
```bash
# Recent zones only (2 months)
python strategies/find_zones.py NVDA --lookback 50

# Extended history (1 year)
python strategies/find_zones.py INTC --lookback 250

# Full history (2 years)
python strategies/find_zones.py QQQ --lookback 500
```

### Combined Options
```bash
# Find older zones with strict quality
python strategies/find_zones.py AAPL --lookback 250 --sensitivity conservative

# Find all zones in recent period
python strategies/find_zones.py SPY --lookback 50 --sensitivity aggressive
```

---

## INTERPRETING OUTPUT

### Sample Output
```
================================================================================
ZONE FINDER - AAPL
================================================================================
Lookback: 500 days
Sensitivity: balanced
Config: min_consol=3, range_pct=1.0, move_pct=0.5

Fetching daily bars...
Fetched 500 candles from 2023-09-10 to 2025-09-10

Scanning for patterns...
  DBR (Dump Base Rally): Found 8 demand zones
  RBD (Rally Base Dump): Found 6 supply zones
  DBD (Dump Base Dump): Found 2 demand zones (continuation)
  RBR (Rally Base Rally): Found 4 supply zones (continuation)

================================================================================
ZONES FOUND: 20
================================================================================

DEMAND ZONES (Buyer support levels): 10
--------------------------------------------------------------------------------
[DBR] DEMAND | $170.50 - $172.00 | Size: $1.50 | Formed: 2025-08-15 | Candles: 4
[DBR] DEMAND | $168.00 - $169.75 | Size: $1.75 | Formed: 2025-08-01 | Candles: 3
[DBD] DEMAND | $165.00 - $166.25 | Size: $1.25 | Formed: 2025-07-20 | Candles: 2
...

SUPPLY ZONES (Seller resistance levels): 10
--------------------------------------------------------------------------------
[RBD] SUPPLY | $185.50 - $187.00 | Size: $1.50 | Formed: 2025-09-01 | Candles: 3
[RBR] SUPPLY | $183.00 - $184.50 | Size: $1.50 | Formed: 2025-08-28 | Candles: 4
...

================================================================================
ZONE STATISTICS
================================================================================
Total Zones: 20
Average Zone Size: $1.42
Max Zone Size: $3.50
Min Zone Size: $0.25
Average Consolidation Length: 3.2 candles
```

### How to Read It
1. **Zone Type**: DBR/RBD/RBR/DBD = which pattern
2. **Zone Classification**: DEMAND (support) or SUPPLY (resistance)
3. **Price Range**: $X - $Y = high and low of consolidation
4. **Size**: Dollar amount of zone (bigger = more significant)
5. **Formed**: When consolidation started
6. **Candles**: How many candles in consolidation (more = more time spent)

---

## PRACTICAL TRADING APPLICATIONS

### 1. Entry Zone Identification
Use demand zones (DBR, DBD) as buy entry areas:
```
Buy opportunity: Price drops to $170.50-$172.00 demand zone
  → Historically, buyers accumulated here
  → High probability of bounce
```

### 2. Stop Loss Placement
Place stops beyond recent supply or demand zones:
```
Long entry at $180
  - Recent demand zone: $170-$171
  - Stop loss: $169.50 (just below zone)
```

### 3. Trend Confirmation
RBD (Rally Base Dump) often marks end of uptrend:
```
After RBD forms at $185-$187:
  → Supply zone = Buyers exhausted
  → Expect bearish momentum
  → Good short entry area
```

### 4. Support/Resistance Levels
All zones are natural support/resistance:
```
Demand zones = Support levels
  → Price bounces when it hits
Supply zones = Resistance levels
  → Price reverses when it hits
```

### 5. Zone Confluence
Multiple zones at same price = STRONG level:
```
Old DBR zone: $170-$171
New demand forming: $170-$171
  → Same zone repeatedly = Very strong support
```

---

## TROUBLESHOOTING

### "No zones found"
**Solutions:**
1. Try `--sensitivity aggressive` (looser criteria)
2. Increase `--lookback` to 250+ days
3. Check if symbol has sufficient price movement
4. Try stock with more volatility (growth stocks vs. stable stocks)

### "Too many zones (100+)"
**Solutions:**
1. Try `--sensitivity conservative` (stricter criteria)
2. Reduce `--lookback` to 50-100 days
3. Stock might have too much noise/chop

### "Zones seem too small/large"
**Solutions:**
This is normal! Different stocks have different zone sizes:
- Volatile stocks (NVDA, TSLA): Larger zones
- Stable stocks (JNJ, PG): Smaller zones
- Penny stocks: Tiny zones
- Indices (SPY, QQQ): Medium zones

Zones are specific to each stock's price action.

### "Zones don't match my charts"
**Reasons:**
1. You might be looking at different timeframe (this uses daily only)
2. You might be zoomed too tight on chart
3. Look for the consolidation area = the zone location
4. Date on zone = when consolidation started, not ended

---

## ADVANCED: CUSTOM TUNING

### Example 1: Aggressive Trading (Find All Opportunities)
```bash
python strategies/find_zones.py SPY --sensitivity aggressive --lookback 100
```

Config applied:
- min_consolidation = 2 (very tight requirement)
- consolidation_range_pct = 1.5 (wide consolidations OK)
- min_prior_move_pct = 0.25 (small moves qualify)
- lookback = 100 (recent activity)

**Result:** High volume of zones, good for active traders

---

### Example 2: Structural Analysis (Find Major Levels Only)
```bash
python strategies/find_zones.py AAPL --sensitivity conservative --lookback 500
```

Config applied:
- min_consolidation = 4 (need solid bases)
- consolidation_range_pct = 0.5 (tight consolidations)
- min_prior_move_pct = 1.0 (only major moves)
- lookback = 500 (full history)

**Result:** Few zones, but they're major structural levels

---

### Example 3: Swing Trading (Medium-term focus)
```bash
python strategies/find_zones.py MU --sensitivity balanced --lookback 200
```

Config applied:
- min_consolidation = 3 (good balance)
- consolidation_range_pct = 1.0 (normal ranges)
- min_prior_move_pct = 0.5 (good filter)
- lookback = 200 (medium history)

**Result:** Good mix of zones for swing trades (2-4 week holds)

---

## SUMMARY

| Pattern | Move 1 | Base | Move 2 | Type | Meaning |
|---------|--------|------|--------|------|---------|
| DBR | ↓ Down | Hold | ↑ Up | DEMAND | Buyers won, support held |
| RBD | ↑ Up | Hold | ↓ Down | SUPPLY | Sellers won, resistance rejected |
| RBR | ↑ Up | Hold | ↑ Up (↑↑) | SUPPLY | Breakout continuation, resistance beaten |
| DBD | ↓ Down | Hold | ↓ Down (↓↓) | DEMAND | Breakdown continuation, support broken |

All zones can be tuned using sensitivity modes or custom parameters.

