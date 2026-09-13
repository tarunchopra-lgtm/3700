# find_zones.py - QUICK REFERENCE

## 📍 What It Does
Identifies supply and demand zones on daily charts using 4 market structure patterns.

## 🎯 Quick Start

```bash
# Basic scan (AAPL, 500 days, balanced settings)
python strategies/find_zones.py AAPL

# Conservative (fewer, higher-quality zones)
python strategies/find_zones.py MU --sensitivity conservative

# Aggressive (more zones to explore)
python strategies/find_zones.py SPY --sensitivity aggressive

# Recent zones only (50 days)
python strategies/find_zones.py INTC --lookback 50

# Full analysis
python strategies/find_zones.py AAPL --lookback 500 --sensitivity balanced
```

## 📊 The 4 Patterns

### 1️⃣ DBR (Dump Base Rally) → **DEMAND ZONE** ↑
```
Down candle → Consolidation (buyers hold) → Up candle
Zone = WHERE BUYERS ACCUMULATED (support level)
```

### 2️⃣ RBD (Rally Base Dump) → **SUPPLY ZONE** ↓
```
Up candle → Consolidation (sellers take over) → Down candle
Zone = WHERE SELLERS REJECTED (resistance level)
```

### 3️⃣ RBR (Rally Base Rally) → **SUPPLY ZONE** (Continuation) ↑↑
```
Up candle → Consolidation (brief pause) → Higher up candle
Zone = RESISTANCE THAT WAS BEATEN (old resistance becomes support)
```

### 4️⃣ DBD (Dump Base Dump) → **DEMAND ZONE** (Continuation) ↓↓
```
Down candle → Consolidation (brief bounce) → Lower down candle
Zone = SUPPORT THAT WAS BROKEN (failed support)
```

## 🎛️ Three Sensitivity Modes

| Mode | Quality | Quantity | Use Case |
|------|---------|----------|----------|
| **conservative** | ⭐⭐⭐ Highest | Few | Major structural levels |
| **balanced** | ⭐⭐ Medium | Moderate | General trading |
| **aggressive** | ⭐ Low | Many | Explore all options |

## 📈 Key Parameters Explained

### `min_consolidation_candles`
- **What**: How many small-range candles make a valid base
- **Conservative**: 4-5 (need solid bases)
- **Balanced**: 3 (good middle ground)
- **Aggressive**: 2 (catch even quick bases)

### `consolidation_range_pct`
- **What**: Max width of consolidation as % of prior move
- **Conservative**: 0.5% (very tight)
- **Balanced**: 1.0% (normal)
- **Aggressive**: 1.5-2.0% (loose, catch more)

### `min_prior_move_pct`
- **What**: Minimum initial move size (% of open price)
- **Conservative**: 1.0% (only big moves)
- **Balanced**: 0.5% (medium moves)
- **Aggressive**: 0.25% (even small moves)

## 💡 Reading the Output

```
[DBR] DEMAND | $170.50 - $172.00 | Size: $1.50 | Formed: 2025-08-15 | Candles: 4
└─┬─┘  └────┬──┘   └───────┬──────┘   └─┬─┘   └─────────┬─────────┘   └─┬──┘
  │      Type        Price Range      Zone Size     When Formed      Base Length
Pattern
```

## 🎯 Trading Applications

### Buy Setup
```
Look for DBR demand zones:
1. Price approaches zone from above
2. Bounces in zone = Buy signal
3. Stop below zone high
4. Target = Last significant high
```

### Sell Setup
```
Look for RBD supply zones:
1. Price approaches zone from below
2. Rejects at zone = Short signal
3. Stop above zone low
4. Target = Last significant low
```

### Trend Confirmation
```
After RBD forms = Trend may be changing
After RBR forms = Trend likely continuing
```

## ⚙️ Tuning Examples

### "Too few zones"
```bash
python strategies/find_zones.py SPY --sensitivity aggressive --lookback 200
```

### "Too many zones (noise)"
```bash
python strategies/find_zones.py SPY --sensitivity conservative --lookback 100
```

### "Want only recent zones"
```bash
python strategies/find_zones.py AAPL --lookback 50
```

### "Want all historical structure"
```bash
python strategies/find_zones.py AAPL --lookback 500
```

## 📚 Full Guide
See `ZONE_FINDER_GUIDE.md` for:
- Detailed pattern explanations
- Complete calculation breakdown
- Advanced tuning strategies
- Troubleshooting
- Real-world examples

## 🔧 Customization

Each parameter can be adjusted independently in the code:

```python
zones = dbr(
    candles,
    min_consolidation=3,              # Change this
    consolidation_range_pct=1.0,      # Or this
    min_prior_move_pct=0.5            # Or this
)
```

Rerun after tuning to see different results!

## 💾 Output Sections

1. **ZONES FOUND (Total)**
2. **DEMAND ZONES** - List of all support levels
3. **SUPPLY ZONES** - List of all resistance levels
4. **ZONE STATISTICS** - Average size, count, etc.

## ⏱️ Typical Runtime
- 50 days: <2 seconds
- 100 days: <3 seconds
- 500 days: <8 seconds

Depends on network/API latency.

---

**🚀 Next Steps:**
1. Run on your favorite tickers
2. Compare zones to your charts
3. Try different sensitivities
4. Use zones as entry/exit points
5. Track if zones hold as support/resistance
