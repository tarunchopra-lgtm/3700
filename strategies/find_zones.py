"""
find_zones.py - Supply & Demand Zone Finder

This program identifies 4 types of market structure patterns using daily candles:

PATTERN TYPES:
==============

1. DBR (Drop Base Rally) - DEMAND ZONE
   Pattern Flow: Down move → Consolidation → Up move
   Meaning: Price comes down hard, accumulates at a level (base), then rallies away
   Zone Type: DEMAND (buyers came in at this level)
   Psychology: After the dump, buyers step in and hold price (base), leading to recovery

2. RBR (Rally Base Rally) - DEMAND ZONE (continuation)
   Pattern Flow: Up move → Consolidation → Up move (higher)
   Meaning: Price rallies, pulls back briefly (support forms), then continues rallying
   Zone Type: DEMAND (support level where buyers defended, then broke higher)
   Psychology: First rally up, consolidation zone becomes support, buyers defend and break higher

3. RBD (Rally Base Drop) - SUPPLY ZONE
   Pattern Flow: Up move → Consolidation → Down move
   Meaning: Price rallies to resistance, consolidates, then sells off
   Zone Type: SUPPLY (rejection at this level)
   Psychology: Buyers tried to push higher but failed; sellers took over

4. DBD (Drop Base Drop) - SUPPLY ZONE (continuation)
   Pattern Flow: Down move → Consolidation → Down move (lower)
   Meaning: Price dumps, bounces slightly (resistance fails), then dumps again
   Zone Type: SUPPLY (resistance level where sellers took control, then broke lower)
   Psychology: First dump down, consolidation zone becomes resistance, sellers break through

ZONE CALCULATION:
=================
For each pattern, the ZONE is defined as the consolidation/base area:
- Zone High = Highest point during consolidation
- Zone Low = Lowest point during consolidation
- Zone Size = High - Low (in dollars)
- Zone represents area of price action compression/equilibrium

CONFIGURATION PARAMETERS:
========================
All these can be tuned for different market conditions:

- min_consolidation_candles: Minimum candles to qualify as a base
  * Lower (2-3) = More sensitive, finds every tiny dip
  * Higher (4-5) = More robust, finds only significant bases
  * Trade-off: Sensitivity vs. Noise

- consolidation_range_pct: Max range of base as % of the prior move
  * Lower (0.5) = Tighter consolidation, stronger signal
  * Higher (2.0) = Wider consolidation, more frequent signals
  * Rule: If consolidation range > (prior move * this %), skip

- min_prior_move_pct: Minimum prior move magnitude as % of candle open
  * Higher (1.0%) = Only significant moves qualify
  * Lower (0.25%) = More sensitive to small moves
  * Ensures we're looking at real moves, not noise

- lookback_days: How many daily candles to scan
  * More = Finds older zones (200-500 days back)
  * Less = Recent zones only (50-100 days)
  * Default: 500 to capture significant zones

USAGE:
======
  python strategies/find_zones.py <TICKER> [--lookback <DAYS>] [--sensitivity <MODE>]

EXAMPLES:
=========
  python strategies/find_zones.py AAPL
  python strategies/find_zones.py MU --lookback 200
  python strategies/find_zones.py SPY --sensitivity aggressive
  python strategies/find_zones.py BTC/USD --lookback 100 --sensitivity conservative

SENSITIVITY MODES:
==================
  conservative: min_consol=4, range_pct=0.5, move_pct=1.0 (fewer, higher-quality zones)
  balanced:     min_consol=3, range_pct=1.0, move_pct=0.5 (default)
  aggressive:   min_consol=2, range_pct=1.5, move_pct=0.25 (more signals, more noise)
"""

import sys
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import List, Tuple
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
MANUAL_DEMAND_ZONES_FILE = WORKSPACE_ROOT / "lists" / "manual_demand_zones.txt"
ORIGIN_ZONE_CONFIG_FILE = WORKSPACE_ROOT / "lists" / "zone_origin_config.txt"

try:
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
except ImportError:
    print("Error: Alpaca SDK not installed. Install with: pip install alpacatrade")
    sys.exit(1)

# Load credentials from env/credentials file
try:
    from roles.credentials import CredentialsRole
except ImportError:
    print("Error: Could not import CredentialsRole from roles/credentials.py")
    print("Make sure you're running this from the workspace root or strategies folder.")
    sys.exit(1)

# ============================================================================
# DATA STRUCTURES
# ============================================================================

@dataclass
class Candle:
    """Represents a single daily candle"""
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    
    def range(self) -> float:
        """True range of candle"""
        return self.high - self.low
    
    def body(self) -> float:
        """Body range (open to close)"""
        return abs(self.close - self.open)
    
    def is_up(self) -> bool:
        """Is this an up candle?"""
        return self.close > self.open
    
    def is_down(self) -> bool:
        """Is this a down candle?"""
        return self.close < self.open


@dataclass
class Zone:
    """Represents a demand/supply zone"""
    pattern_type: str          # DBR, RBR, RBD, or DBD
    zone_type: str             # DEMAND or SUPPLY
    high: float                # Zone high price
    low: float                 # Zone low price
    size: float                # Zone size (high - low)
    date_formed: datetime      # When zone was formed (start of consolidation)
    date_confirmed: datetime   # When pattern completed
    consolidation_candles: int # Number of candles in base
    prior_move_size: float     # Size of move before consolidation
    
    def center(self) -> float:
        """Calculate zone center price"""
        return (self.high + self.low) / 2
    
    def __str__(self) -> str:
        """Pretty print zone"""
        return (
            f"[{self.pattern_type}] {self.zone_type:6s} | "
            f"${self.low:.2f} - ${self.high:.2f} | "
            f"Size: ${self.size:.2f} | "
            f"Formed: {self.date_formed.strftime('%Y-%m-%d')} | "
            f"Candles: {self.consolidation_candles}"
        )


def load_manual_demand_zone_dates() -> dict[str, list[str]]:
    """Load exact daily-candle dates to use as manual demand zones."""
    anchors: dict[str, list[str]] = {}
    if not MANUAL_DEMAND_ZONES_FILE.exists():
        return anchors

    with open(MANUAL_DEMAND_ZONES_FILE, encoding="utf-8") as file:
        for line_number, raw_line in enumerate(file, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) != 2:
                print(f"Warning: ignored invalid manual zone line {line_number}: {line}")
                continue
            symbol, date_text = parts
            try:
                datetime.strptime(date_text, "%Y-%m-%d")
            except ValueError:
                print(f"Warning: ignored invalid manual zone date on line {line_number}: {date_text}")
                continue
            anchors.setdefault(symbol.upper(), []).append(date_text)
    return anchors


def get_manual_demand_zones(symbol: str, candles: List[Candle]) -> List[Zone]:
    """Create demand zones from the high/low of configured exact daily candles."""
    dates = load_manual_demand_zone_dates().get(symbol.upper(), [])
    candles_by_date = {candle.timestamp.date().isoformat(): candle for candle in candles}
    zones = []
    for date_text in dates:
        candle = candles_by_date.get(date_text)
        if candle is None:
            print(f"Warning: {symbol} has no daily candle for manual zone date {date_text}")
            continue
        zones.append(
            Zone(
                pattern_type="MANUAL",
                zone_type="DEMAND",
                high=candle.high,
                low=candle.low,
                size=candle.range(),
                date_formed=candle.timestamp,
                date_confirmed=candle.timestamp,
                consolidation_candles=1,
                prior_move_size=candle.body(),
            )
        )
    return zones


def select_manual_demand_zone(zones: List[Zone], current_price: float) -> Zone | None:
    """Choose the closest configured demand zone at or below current price."""
    if not zones:
        return None
    reachable_zones = [zone for zone in zones if zone.high <= current_price]
    if reachable_zones:
        return max(reachable_zones, key=lambda zone: zone.high)
    return min(zones, key=lambda zone: abs(zone.center() - current_price))


def load_origin_zone_config() -> dict[str, float | int]:
    """Load editable defaults for tight, fresh origin zones."""
    config: dict[str, float | int] = {
        "MIN_BASE_CANDLES": 2,
        "MAX_BASE_CANDLES": 5,
        "MAX_DEPARTURE_CANDLES": 5,
        "MIN_DEPARTURE_PCT": 3.0,
        "MIN_DEPARTURE_ATR": 2.0,
        "MAX_ZONE_TO_DEPARTURE_RATIO": 0.35,
    }
    if not ORIGIN_ZONE_CONFIG_FILE.exists():
        return config

    with open(ORIGIN_ZONE_CONFIG_FILE, encoding="utf-8") as file:
        for raw_line in file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = (part.strip() for part in line.split("=", 1))
            if name not in config:
                continue
            try:
                config[name] = int(value) if name in {
                    "MIN_BASE_CANDLES", "MAX_BASE_CANDLES", "MAX_DEPARTURE_CANDLES"
                } else float(value)
            except ValueError:
                print(f"Warning: invalid {name} in {ORIGIN_ZONE_CONFIG_FILE.name}: {value}")
    return config


def find_fresh_origin_zones(candles: List[Candle], atr: float,
                            config: dict[str, float | int]) -> tuple[List[Zone], List[Zone]]:
    """Find tight multi-candle bases followed by sharp, untouched departures."""
    demand_zones = []
    supply_zones = []
    min_base_candles = int(config["MIN_BASE_CANDLES"])
    max_base_candles = int(config["MAX_BASE_CANDLES"])
    max_departure_candles = int(config["MAX_DEPARTURE_CANDLES"])
    min_departure_pct = float(config["MIN_DEPARTURE_PCT"])
    min_departure_atr = float(config["MIN_DEPARTURE_ATR"])
    max_zone_to_departure_ratio = float(config["MAX_ZONE_TO_DEPARTURE_RATIO"])

    for base_start in range(len(candles) - min_base_candles - 1):
        for base_length in range(min_base_candles, max_base_candles + 1):
            base_end = base_start + base_length - 1
            if base_end + 1 >= len(candles):
                break
            base = candles[base_start:base_end + 1]
            zone_high = max(candle.high for candle in base)
            zone_low = min(candle.low for candle in base)
            zone_size = zone_high - zone_low
            if zone_size > atr:
                continue

            departure = candles[base_end + 1:base_end + 1 + max_departure_candles]
            if not departure:
                continue
            highest_departure = max(departure, key=lambda candle: candle.high)
            upward_move = highest_departure.high - zone_high
            upward_pct = (upward_move / zone_high) * 100 if zone_high else 0
            departure_atr = upward_move / atr if atr else 0
            if (upward_move > 0 and upward_pct >= min_departure_pct and
                    departure_atr >= min_departure_atr and
                    zone_size / upward_move <= max_zone_to_departure_ratio):
                departure_end = departure.index(highest_departure) + base_end + 1
                later_candles = candles[departure_end + 1:]
                if any(candle.low <= zone_high for candle in later_candles):
                    continue
                demand_zones.append(
                    Zone(
                        pattern_type="ORIGIN",
                        zone_type="DEMAND",
                        high=zone_high,
                        low=zone_low,
                        size=zone_size,
                        date_formed=base[0].timestamp,
                        date_confirmed=highest_departure.timestamp,
                        consolidation_candles=base_length,
                        prior_move_size=upward_move,
                    )
                )

            lowest_departure = min(departure, key=lambda candle: candle.low)
            downward_move = zone_low - lowest_departure.low
            downward_pct = (downward_move / zone_low) * 100 if zone_low else 0
            departure_atr = downward_move / atr if atr else 0
            if (downward_move > 0 and downward_pct >= min_departure_pct and
                    departure_atr >= min_departure_atr and
                    zone_size / downward_move <= max_zone_to_departure_ratio):
                departure_end = departure.index(lowest_departure) + base_end + 1
                later_candles = candles[departure_end + 1:]
                if any(candle.high >= zone_low for candle in later_candles):
                    continue
                supply_zones.append(
                    Zone(
                        pattern_type="ORIGIN",
                        zone_type="SUPPLY",
                        high=zone_high,
                        low=zone_low,
                        size=zone_size,
                        date_formed=base[0].timestamp,
                        date_confirmed=lowest_departure.timestamp,
                        consolidation_candles=base_length,
                        prior_move_size=downward_move,
                    )
                )
    return demand_zones, supply_zones


def select_bullish_origin_demand_zone(zones: List[Zone], current_price: float) -> Zone | None:
    """Select the most recent distinct move's earliest fresh origin candle."""
    reachable_zones = [zone for zone in zones if zone.high <= current_price]
    if not reachable_zones:
        return None

    ordered_zones = sorted(reachable_zones, key=lambda zone: zone.date_formed)
    move_clusters: List[List[Zone]] = []
    for zone in ordered_zones:
        if not move_clusters:
            move_clusters.append([zone])
            continue
        previous_zone = move_clusters[-1][-1]
        days_apart = (zone.date_formed.date() - previous_zone.date_formed.date()).days
        if days_apart <= 5 and zone.low <= previous_zone.high and zone.high >= previous_zone.low:
            move_clusters[-1].append(zone)
        else:
            move_clusters.append([zone])
    return move_clusters[-1][0]


def select_origin_supply_zone(zones: List[Zone], current_price: float) -> Zone | None:
    """Choose the closest fresh origin supply zone at or above current price."""
    reachable_zones = [zone for zone in zones if zone.low >= current_price]
    if reachable_zones:
        return min(reachable_zones, key=lambda zone: zone.low)
    return min(zones, key=lambda zone: abs(zone.center() - current_price)) if zones else None


# ============================================================================
# CORE PATTERN DETECTION FUNCTIONS
# ============================================================================

def _is_small_candle(candle: Candle, max_range: float) -> bool:
    """Check if a candle's range is small enough to be part of consolidation"""
    return candle.range() <= max_range


def _try_pattern(candles: List[Candle], 
                 start_idx: int,
                 initial_is_up: bool,
                 max_consolidation_range: float,
                 min_prior_move_pct: float) -> tuple[int, float, float, int] | None:
    """
    Try to find a pattern starting at start_idx.
    Returns: (consolidation_end_idx, zone_high, zone_low, num_consolidation_candles) or None
    
    Pattern: Initial move → 1-3 small candles → confirmation move in same direction
    """
    initial_candle = candles[start_idx]
    
    # Verify initial move size
    if initial_is_up:
        move_size = initial_candle.close - initial_candle.open
        if not initial_candle.is_up():
            return None
    else:
        move_size = initial_candle.open - initial_candle.close
        if not initial_candle.is_down():
            return None
    
    move_pct = (move_size / initial_candle.open) * 100
    if move_pct < min_prior_move_pct:
        return None
    
    # Try 1, 2, and 3 consolidation candles
    for num_consol in [1, 2, 3]:
        consol_end = start_idx + num_consol
        
        # Check if we have enough candles left (need consolidation + 1 confirmation candle)
        if consol_end + 1 >= len(candles):
            continue
        
        # Check if all consolidation candles are small enough
        zone_high = initial_candle.low if not initial_is_up else initial_candle.high
        zone_low = initial_candle.low if not initial_is_up else initial_candle.high
        
        all_small = True
        for j in range(start_idx + 1, consol_end + 1):
            if not _is_small_candle(candles[j], max_consolidation_range):
                all_small = False
                break
            zone_high = max(zone_high, candles[j].high)
            zone_low = min(zone_low, candles[j].low)
        
        if not all_small:
            continue
        
        # Check for confirmation candle in the right direction
        confirmation_candle = candles[consol_end + 1]
        if initial_is_up and confirmation_candle.is_up():
            # RBR: Rally Base Rally - confirmation is higher up move
            if confirmation_candle.close > initial_candle.close:
                return (consol_end, zone_high, zone_low, num_consol)
        elif not initial_is_up and confirmation_candle.is_down():
            # DBD: Dump Base Dump - confirmation is lower down move
            if confirmation_candle.close < initial_candle.close:
                return (consol_end, zone_high, zone_low, num_consol)
    
    return None


def dbr(candles: List[Candle], 
        min_consolidation: int = 1,
        consolidation_range_pct: float = 2.0,
        min_prior_move_pct: float = 0.5,
        max_prior_move_pct: float = 100.0) -> List[Zone]:
    """
    DBR (Drop Base Rally) - DEMAND ZONE
    
    Pattern: Sharp down move → 1-3 small candles → sharp up move
    Zone = the consolidation area (support where buyers stepped in)
    
    Filters: Down move must be between min_prior_move_pct and max_prior_move_pct
    """
    zones = []
    
    for i in range(1, len(candles) - 2):
        down_candle = candles[i]
        if not down_candle.is_down():
            continue
        
        down_move_size = down_candle.open - down_candle.close
        down_move_pct = (down_move_size / down_candle.open) * 100
        
        if down_move_pct < min_prior_move_pct or down_move_pct > max_prior_move_pct:
            continue
        
        max_range = down_move_size * consolidation_range_pct
        
        # Try 1-3 consolidation candles
        for num_consol in [1, 2, 3]:
            consol_end = i + num_consol
            
            if consol_end + 1 >= len(candles):
                continue
            
            # Check all consolidation candles are small
            zone_high = down_candle.low
            zone_low = down_candle.low
            
            all_small = True
            for j in range(i + 1, consol_end + 1):
                if candles[j].range() > max_range:
                    all_small = False
                    break
                zone_high = max(zone_high, candles[j].high)
                zone_low = min(zone_low, candles[j].low)
            
            if not all_small:
                break  # Larger candle means no more consolidation possible
            
            # Check for up candle (rally) after consolidation with similar strength
            up_candle = candles[consol_end + 1]
            if not up_candle.is_up():
                continue
            
            # Verify 3rd leg (confirmation) has similar strength as 1st leg
            up_move_size_3rd = up_candle.close - up_candle.open
            up_move_pct_3rd = (up_move_size_3rd / up_candle.open) * 100
            if up_move_pct_3rd < min_prior_move_pct:
                continue  # Confirmation move too weak
            
            # Found pattern!
            zone_size = zone_high - zone_low
            zone = Zone(
                pattern_type="DBR",
                zone_type="DEMAND",
                high=zone_high,
                low=zone_low,
                size=zone_size,
                date_formed=candles[i + 1].timestamp,
                date_confirmed=up_candle.timestamp,
                consolidation_candles=num_consol,
                prior_move_size=down_move_size
            )
            zones.append(zone)
            break  # Found a pattern at this location, move to next
    
    return zones


def rbr(candles: List[Candle],
        min_consolidation: int = 1,
        consolidation_range_pct: float = 2.0,
        min_prior_move_pct: float = 0.5,
        max_prior_move_pct: float = 100.0) -> List[Zone]:
    """
    RBR (Rally Base Rally) - DEMAND ZONE (continuation)
    
    Pattern: Sharp up move → 1-3 small candles → sharp up move (higher)
    Zone = the consolidation area (support where buyers defended and broke higher)
    
    Filters: Up move must be between min_prior_move_pct and max_prior_move_pct
    """
    zones = []
    
    for i in range(1, len(candles) - 2):
        up_candle = candles[i]
        if not up_candle.is_up():
            continue
        
        up_move_size = up_candle.close - up_candle.open
        up_move_pct = (up_move_size / up_candle.open) * 100
        
        if up_move_pct < min_prior_move_pct or up_move_pct > max_prior_move_pct:
            continue
        
        max_range = up_move_size * consolidation_range_pct
        
        # Try 1-3 consolidation candles
        for num_consol in [1, 2, 3]:
            consol_end = i + num_consol
            
            if consol_end + 1 >= len(candles):
                continue
            
            # Check all consolidation candles are small
            zone_high = up_candle.high
            zone_low = up_candle.high
            
            all_small = True
            for j in range(i + 1, consol_end + 1):
                if candles[j].range() > max_range:
                    all_small = False
                    break
                zone_high = max(zone_high, candles[j].high)
                zone_low = min(zone_low, candles[j].low)
            
            if not all_small:
                break  # Larger candle means no more consolidation possible
            
            # Check for up candle (confirmation) after consolidation with similar strength
            up_candle_2 = candles[consol_end + 1]
            if not up_candle_2.is_up():
                continue
            
            # Verify 3rd leg (confirmation) has similar strength as 1st leg
            up_move_size_3rd = up_candle_2.close - up_candle_2.open
            up_move_pct_3rd = (up_move_size_3rd / up_candle_2.open) * 100
            if up_move_pct_3rd < min_prior_move_pct:
                continue  # Confirmation move too weak
            
            # Found pattern!
            zone_size = zone_high - zone_low
            zone = Zone(
                pattern_type="RBR",
                zone_type="DEMAND",
                high=zone_high,
                low=zone_low,
                size=zone_size,
                date_formed=candles[i + 1].timestamp,
                date_confirmed=up_candle_2.timestamp,
                consolidation_candles=num_consol,
                prior_move_size=up_move_size
            )
            zones.append(zone)
            break  # Found a pattern at this location, move to next
    
    return zones


def rbd(candles: List[Candle],
        min_consolidation: int = 1,
        consolidation_range_pct: float = 2.0,
        min_prior_move_pct: float = 0.5,
        max_prior_move_pct: float = 100.0) -> List[Zone]:
    """
    RBD (Rally Base Drop) - SUPPLY ZONE
    
    Pattern: Sharp up move → 1-3 small candles → sharp down move
    Zone = the consolidation area (resistance where sellers rejected)
    
    Filters: Up move must be between min_prior_move_pct and max_prior_move_pct
    """
    zones = []
    
    for i in range(1, len(candles) - 2):
        up_candle = candles[i]
        if not up_candle.is_up():
            continue
        
        up_move_size = up_candle.close - up_candle.open
        up_move_pct = (up_move_size / up_candle.open) * 100
        
        if up_move_pct < min_prior_move_pct or up_move_pct > max_prior_move_pct:
            continue
        
        max_range = up_move_size * consolidation_range_pct
        
        # Try 1-3 consolidation candles
        for num_consol in [1, 2, 3]:
            consol_end = i + num_consol
            
            if consol_end + 1 >= len(candles):
                continue
            
            # Check all consolidation candles are small
            zone_high = up_candle.high
            zone_low = up_candle.high
            
            all_small = True
            for j in range(i + 1, consol_end + 1):
                if candles[j].range() > max_range:
                    all_small = False
                    break
                zone_high = max(zone_high, candles[j].high)
                zone_low = min(zone_low, candles[j].low)
            
            if not all_small:
                break  # Larger candle means no more consolidation possible
            
            # Check for down candle after consolidation with similar strength
            down_candle = candles[consol_end + 1]
            if not down_candle.is_down():
                continue
            
            # Verify 3rd leg (confirmation) has similar strength as 1st leg
            down_move_size_3rd = down_candle.open - down_candle.close
            down_move_pct_3rd = (down_move_size_3rd / down_candle.open) * 100
            if down_move_pct_3rd < min_prior_move_pct:
                continue  # Confirmation move too weak
            
            # Found pattern!
            zone_size = zone_high - zone_low
            zone = Zone(
                pattern_type="RBD",
                zone_type="SUPPLY",
                high=zone_high,
                low=zone_low,
                size=zone_size,
                date_formed=candles[i + 1].timestamp,
                date_confirmed=down_candle.timestamp,
                consolidation_candles=num_consol,
                prior_move_size=up_move_size
            )
            zones.append(zone)
            break  # Found a pattern at this location, move to next
    
    return zones


def dbd(candles: List[Candle],
        min_consolidation: int = 1,
        consolidation_range_pct: float = 2.0,
        min_prior_move_pct: float = 0.5,
        max_prior_move_pct: float = 100.0) -> List[Zone]:
    """
    DBD (Drop Base Drop) - SUPPLY ZONE (continuation)
    
    Pattern: Sharp down move → 1-3 small candles → sharp down move (lower)
    Zone = the consolidation area (resistance where sellers broke through)
    
    Filters: Down move must be between min_prior_move_pct and max_prior_move_pct
    """
    zones = []
    
    for i in range(1, len(candles) - 2):
        down_candle = candles[i]
        if not down_candle.is_down():
            continue
        
        down_move_size = down_candle.open - down_candle.close
        down_move_pct = (down_move_size / down_candle.open) * 100
        
        if down_move_pct < min_prior_move_pct or down_move_pct > max_prior_move_pct:
            continue
        
        max_range = down_move_size * consolidation_range_pct
        
        # Try 1-3 consolidation candles
        for num_consol in [1, 2, 3]:
            consol_end = i + num_consol
            
            if consol_end + 1 >= len(candles):
                continue
            
            # Check all consolidation candles are small
            zone_high = down_candle.high
            zone_low = down_candle.low
            
            all_small = True
            for j in range(i + 1, consol_end + 1):
                if candles[j].range() > max_range:
                    all_small = False
                    break
                zone_high = max(zone_high, candles[j].high)
                zone_low = min(zone_low, candles[j].low)
            
            if not all_small:
                break  # Larger candle means no more consolidation possible
            
            # Check for down candle after consolidation - must be lower with similar strength
            down_candle_2 = candles[consol_end + 1]
            if not down_candle_2.is_down():
                continue
            
            if down_candle_2.close >= down_candle.close:
                continue  # Must be lower (continuation, not bounce)
            
            # Verify 3rd leg (confirmation) has similar strength as 1st leg
            down_move_size_3rd = down_candle_2.open - down_candle_2.close
            down_move_pct_3rd = (down_move_size_3rd / down_candle_2.open) * 100
            if down_move_pct_3rd < min_prior_move_pct:
                continue  # Confirmation move too weak
            
            # Found pattern!
            zone_size = zone_high - zone_low
            zone = Zone(
                pattern_type="DBD",
                zone_type="SUPPLY",
                high=zone_high,
                low=zone_low,
                size=zone_size,
                date_formed=candles[i + 1].timestamp,
                date_confirmed=down_candle_2.timestamp,
                consolidation_candles=num_consol,
                prior_move_size=down_move_size
            )
            zones.append(zone)
            break  # Found a pattern at this location, move to next
    
    return zones


# ============================================================================
# DATA FETCHING
# ============================================================================

def fetch_daily_bars(symbol: str, lookback_days: int = 50) -> List[Candle]:
    """
    Fetch historical daily bars from Alpaca API
    
    Args:
        symbol: Stock or crypto symbol (e.g., "AAPL" or "BTC/USD")
        lookback_days: How many days back to fetch (default 50)
    
    Returns:
        List of Candle objects
    """
    try:
        # Load credentials from env/credentials file
        creds = CredentialsRole()
        
        # Create client with credentials (using positional args)
        client = StockHistoricalDataClient(creds.api_key, creds.secret_key)
        end_date = datetime.now()
        start_date = end_date - timedelta(days=lookback_days)
        
        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            start=start_date,
            end=end_date,
            timeframe=TimeFrame.Day
        )
        
        bars_response = client.get_stock_bars(request)
        
        # Extract bars from response (handles multiple response formats)
        bars_dict = None
        if hasattr(bars_response, 'data'):
            bars_dict = bars_response.data
        elif isinstance(bars_response, dict):
            bars_dict = bars_response.get('data', bars_response)
        else:
            bars_dict = bars_response
        
        if not bars_dict or symbol.upper() not in bars_dict:
            print(f"Error: No data found for {symbol}")
            return []
        
        bars = bars_dict[symbol.upper()]
        
        # Convert to Candle objects and sort by timestamp
        candles = [
            Candle(
                timestamp=bar.timestamp,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume
            )
            for bar in bars
        ]
        
        return sorted(candles, key=lambda x: x.timestamp)
    
    except Exception as e:
        print(f"Error fetching data for {symbol}: {e}")
        return []


# ============================================================================
# MAIN ANALYSIS & REPORTING
# ============================================================================

def get_zone_freshness_info(zone: Zone, candles: List[Candle]) -> dict:
    """
    Analyze zone freshness.
    
    Returns dict with:
    - days_since_price_reached: Days since price last touched zone (None if never reached)
    - was_reached_in_days: Boolean if reached within N days
    - last_touch_date: When price last touched zone
    """
    
    # Find candles after zone formation
    candles_after_formed = [c for c in candles if c.timestamp >= zone.date_formed]
    
    if not candles_after_formed:
        return {
            "days_since_price_reached": None,
            "was_reached": False,
            "last_touch_date": None
        }
    
    last_touch = None
    
    if zone.zone_type == "DEMAND":
        # Demand zone is touched if price went AT or BELOW zone_low (tested support)
        for candle in reversed(candles_after_formed):
            if candle.low <= zone.low:
                last_touch = candle.timestamp
                break
    else:  # SUPPLY zone
        # Supply zone is touched if price went AT or ABOVE zone_high (tested resistance)
        for candle in reversed(candles_after_formed):
            if candle.high >= zone.high:
                last_touch = candle.timestamp
                break
    
    if last_touch:
        days_since = (candles_after_formed[-1].timestamp - last_touch).days
        return {
            "days_since_price_reached": days_since,
            "was_reached": True,
            "last_touch_date": last_touch
        }
    else:
        return {
            "days_since_price_reached": None,
            "was_reached": False,
            "last_touch_date": None
        }


def calculate_atr(candles: List[Candle], period: int = 14) -> float | None:
    """
    Calculate Average True Range (ATR) volatility indicator
    
    Args:
        candles: List of Candle objects
        period: Number of periods for ATR calculation (default 14)
    
    Returns:
        ATR value or None if insufficient data
    """
    if len(candles) < period + 1:
        return None
    
    # Calculate True Range for each candle
    true_ranges = []
    for i in range(1, len(candles)):
        current = candles[i]
        previous = candles[i - 1]
        
        # True Range = max(high - low, abs(high - prev_close), abs(low - prev_close))
        tr = max(
            current.high - current.low,
            abs(current.high - previous.close),
            abs(current.low - previous.close)
        )
        true_ranges.append(tr)
    
    # Calculate ATR as average of most recent TR values
    if len(true_ranges) < period:
        return None
    
    atr = sum(true_ranges[-period:]) / period
    return atr


def is_demand_zone_fresh(zone: Zone, candles: List[Candle], days_threshold: int = 10) -> bool:
    """
    Demand zone is FRESH if:
    - Price has NOT been BELOW zone.low recently (within days_threshold)
    
    Interpretation: Untested support (consolidation intact, not already tested/broken)
    Note: Formation date is NOT a constraint - sharp move + consolidation quality matters
    """
    if zone.zone_type != "DEMAND":
        return False
    
    info = get_zone_freshness_info(zone, candles)
    if not info["was_reached"]:
        return True  # Never reached = definitely fresh
    
    days_since = info["days_since_price_reached"]
    # Fresh if NOT broken in last N days (reached > N days ago or never)
    return days_since is not None and days_since > days_threshold


def is_supply_zone_fresh(zone: Zone, candles: List[Candle], days_threshold: int = 10) -> bool:
    """
    Supply zone is FRESH if:
    - Price has NOT been ABOVE zone.high recently (within days_threshold)
    
    Interpretation: Untested resistance (consolidation intact, not already tested/broken)
    Note: Formation date is NOT a constraint - sharp move + consolidation quality matters
    """
    if zone.zone_type != "SUPPLY":
        return False
    
    info = get_zone_freshness_info(zone, candles)
    if not info["was_reached"]:
        return True  # Never reached = definitely fresh
    
    days_since = info["days_since_price_reached"]
    # Fresh if NOT broken in last N days (reached > N days ago or never)
    return days_since is not None and days_since > days_threshold


def find_best_zones(candles: List[Candle], 
                    config: dict) -> tuple[Zone | None, Zone | None]:
    """
    Find the best demand and supply zones based on freshness criteria.
    
    FRESHNESS DEFINITION (updated - NO formation date constraint):
    - Demand zone: Untested support (not broken below in last 10 days)
    - Supply zone: Untested resistance (not broken above in last 10 days)
    - Sharp move: 1-3 candle consolidation after significant price move
    
    SHARP MOVE METRICS (calibrated to your real zones):
    - Min move: 11% (SHW, MNST)
    - Max move: 52.5% (ORCL)  
    - Average move: 21.8%
    
    ZONE IDENTIFICATION:
    - Demand zone = high and low of consolidation after downmove (DBR pattern)
    - Supply zone = high and low of consolidation after upmove (RBD pattern)
    - Zone width = actual range of consolidation candles
    
    Logic:
    - Demand zone: DBR or RBR with sharp enough down move + tight consolidation
    - Supply zone: RBD or DBD with sharp enough up move + tight consolidation
    """
    
    # Find all zones
    all_zones = []
    
    dbr_zones = dbr(candles, **config)
    all_zones.extend([(z, "DBR") for z in dbr_zones])
    
    rbr_zones = rbr(candles, **config)
    all_zones.extend([(z, "RBR") for z in rbr_zones])
    
    rbd_zones = rbd(candles, **config)
    all_zones.extend([(z, "RBD") for z in rbd_zones])
    
    dbd_zones = dbd(candles, **config)
    all_zones.extend([(z, "DBD") for z in dbd_zones])
    
    # Separate by type
    demand_zones = [z for z, pattern in all_zones if z.zone_type == "DEMAND"]
    supply_zones = [z for z, pattern in all_zones if z.zone_type == "SUPPLY"]
    
    # Sort by oldest first (chronologically ascending) to prefer prior zones over recent ones
    demand_zones.sort(key=lambda x: x.date_confirmed)
    supply_zones.sort(key=lambda x: x.date_confirmed)
    
    # Find freshest demand zone
    best_demand = None
    for zone in demand_zones:
        if is_demand_zone_fresh(zone, candles, days_threshold=15):
            best_demand = zone
            break  # Return the first (oldest) fresh zone
    
    # Find freshest supply zone
    best_supply = None
    for zone in supply_zones:
        if is_supply_zone_fresh(zone, candles, days_threshold=15):
            best_supply = zone
            break
    
    return best_demand, best_supply


def analyze_zones(symbol: str,
                  lookback_days: int = 90,
                  sensitivity: str = "balanced",
                  min_move_pct: float | None = None,
                  max_move_pct: float | None = None,
                  consolidation_range_pct: float | None = None,
                  consolidation_candles: int = 3,
                  debug: bool = False,
                  candles: List[Candle] | None = None) -> tuple[Zone | None, Zone | None, float | None, float | None, list | None]:
    """
    Main analysis function - finds best demand and supply zones
    
    CALIBRATED TO REAL MARKET DATA (9 validated stocks):
    User zones: NVDA 195-230, INTC 90-113, MU 870-975, KHC 22.5-26
                DOW 27.32-32.32, MNST 43-48, ORCL 120-183, TKO 160-212, SHW 310-344
    
    SHARP MOVE METRICS (from user's real zones):
    - Min move: 11.0% (SHW, MNST)
    - Max move: 52.5% (ORCL)
    - Average move: 21.8%
    - Median move: 17.9%
    
    KEY PARAMETERS YOU CAN CUSTOMIZE:
    - min_move_pct: Minimum prior move % (default: 10-15% based on sensitivity)
    - max_move_pct: Maximum prior move % (default: 40-50% based on sensitivity)
    - consolidation_range_pct: Max consolidation width as % of prior move (default: 1.0-2.0%)
    - consolidation_candles: Number of base candles to find (1-3, default: 3)
    
    Args:
        symbol: Stock/crypto symbol to analyze
        lookback_days: How many days to scan (default 50)
        sensitivity: "conservative", "balanced", or "aggressive"
        min_move_pct: Override minimum move % (e.g., 10, 15, 20)
        max_move_pct: Override maximum move % (e.g., 40, 50, 100)
        consolidation_range_pct: Override consolidation max range as % of move
        consolidation_candles: Override number of consolidation candles (1-3)
    
    Returns:
        (demand_zone, supply_zone, current_price, atr) or (None, None, None, None) if not found
    """
    
    # Configure parameters based on sensitivity
    # Based on user's real data: moves range from 11%-53%, avg 22%
    # BUT found zones have smaller consolidation moves (3-10%), so lowering defaults
    presets = {
        "conservative": {
            "min_consolidation": 1,
            "consolidation_range_pct": 0.75,
            "min_prior_move_pct": 8.0,   # Conservative, but not too strict
            "max_prior_move_pct": 45.0
        },
        "balanced": {
            "min_consolidation": 1,
            "consolidation_range_pct": 1.5,
            "min_prior_move_pct": 3.0,   # Tuned down - consolidations are small relative to prior move
            "max_prior_move_pct": 50.0
        },
        "aggressive": {
            "min_consolidation": 1,
            "consolidation_range_pct": 2.5,
            "min_prior_move_pct": 1.0,   # Very sensitive
            "max_prior_move_pct": 100.0
        }
    }
    
    if sensitivity not in presets:
        sensitivity = "balanced"
    
    config = presets[sensitivity].copy()
    
    # Allow custom overrides
    if min_move_pct is not None:
        config["min_prior_move_pct"] = min_move_pct
    if max_move_pct is not None:
        config["max_prior_move_pct"] = max_move_pct
    if consolidation_range_pct is not None:
        config["consolidation_range_pct"] = consolidation_range_pct
    
    # Use supplied cached candles when available; direct CLI calls still fetch.
    if candles is None:
        candles = fetch_daily_bars(symbol, lookback_days)
    else:
        candles = sorted(candles, key=lambda candle: candle.timestamp)[-lookback_days:]
    
    if not candles:
        return None, None, None, None, None
    
    # Get current price (last candle close)
    current_price = candles[-1].close
    
    # Calculate ATR (14-period)
    atr = calculate_atr(candles, period=14)
    
    # Manual demand anchors override automatic detection. Otherwise use tight,
    # untouched origin zones for both demand and supply.
    manual_demand_zones = get_manual_demand_zones(symbol, candles)
    automatic_demand_zone, automatic_supply_zone = find_best_zones(candles, config)
    origin_demand_zones, origin_supply_zones = find_fresh_origin_zones(candles, atr, load_origin_zone_config())
    demand_zone = select_manual_demand_zone(manual_demand_zones, current_price)
    if demand_zone is None:
        demand_zone = select_bullish_origin_demand_zone(origin_demand_zones, current_price)
    if demand_zone is None:
        demand_zone = automatic_demand_zone
    supply_zone = select_origin_supply_zone(origin_supply_zones, current_price)
    if supply_zone is None:
        supply_zone = automatic_supply_zone
    
    # Store config in zones for reporting (hack but useful)
    if demand_zone:
        demand_zone._config = config
    if supply_zone:
        supply_zone._config = config
    
    # Collect all zones for debugging
    all_zones_found = list(manual_demand_zones) + origin_demand_zones + origin_supply_zones
    all_zones_found.extend(dbr(candles, **config))
    all_zones_found.extend(rbr(candles, **config))
    all_zones_found.extend(rbd(candles, **config))
    all_zones_found.extend(dbd(candles, **config))
    
    return demand_zone, supply_zone, current_price, atr, all_zones_found


# ============================================================================
# ENTRY POINT
# ============================================================================

def main():
    """Command-line entry point"""
    # Handle help flag
    if len(sys.argv) < 2 or sys.argv[1] in ["--help", "-h", "help"]:
        print(__doc__)
        print("\nUSAGE:")
        print("  python strategies/find_zones.py <TICKER> [OPTIONS]")
        print("\nREQUIRED:")
        print("  <TICKER>              Stock or crypto symbol (e.g., AAPL, SPY, MU, BTC/USD, ETH/USD)")
        print("\nOPTIONS:")
        print("  --lookback DAYS       Number of days to scan (default: 50)")
        print("  --sensitivity S       Sensitivity mode: conservative, balanced, or aggressive (default: balanced)")
        print("  --min-move PCT        Minimum prior move % to find pattern (e.g., 10, 15, 20)")
        print("  --max-move PCT        Maximum prior move % to find pattern (e.g., 40, 50, 100)")
        print("  --consol-range PCT    Max consolidation width as % of move (e.g., 1.0, 1.5, 2.5)")
        print("  --debug               Show all detected zones (not just fresh ones)")
        print("  --help, -h            Show this help message")
        print("\nMETRICS FROM YOUR REAL ZONES:")
        print("  Prior move range: 11% (SHW/MNST) to 52.5% (ORCL), avg 21.8%")
        print("  Consolidation: 1-3 candles with tight range")
        print("\nEXAMPLES:")
        print("  python strategies/find_zones.py AAPL")
        print("  python strategies/find_zones.py AAPL --lookback 100")
        print("  python strategies/find_zones.py SPY --sensitivity aggressive")
        print("  python strategies/find_zones.py MU --min-move 10 --max-move 30")
        print("  python strategies/find_zones.py NVDA --sensitivity balanced --min-move 15 --max-move 40")
        print("  python strategies/find_zones.py BTC/USD --consol-range 2.0")
        print("\nOUTPUT:")
        print("  Lists all DEMAND and SUPPLY zones found with pattern type (DBR/RBR/RBD/DBD)")
        print("  Shows configuration used (sensitivity, move thresholds, consolidation range)")
        print("  Zones sorted by most recent first")
        print("  Statistics: total count, average size, min/max size, average consolidation length")
        print()
        sys.exit(0 if len(sys.argv) > 1 else 1)
    
    symbol = sys.argv[1].upper()
    lookback_days = 90
    sensitivity = "balanced"
    min_move_pct = None
    max_move_pct = None
    consolidation_range_pct = None
    debug = False
    
    # Parse optional arguments
    i = 2
    while i < len(sys.argv):
        if sys.argv[i] == "--lookback" and i + 1 < len(sys.argv):
            try:
                lookback_days = int(sys.argv[i + 1])
            except ValueError:
                print(f"Error: --lookback value must be an integer, got '{sys.argv[i + 1]}'")
                sys.exit(1)
            i += 2
        elif sys.argv[i] == "--sensitivity" and i + 1 < len(sys.argv):
            sensitivity = sys.argv[i + 1].lower()
            if sensitivity not in ["conservative", "balanced", "aggressive"]:
                print(f"Error: --sensitivity must be conservative, balanced, or aggressive")
                sys.exit(1)
            i += 2
        elif sys.argv[i] == "--min-move" and i + 1 < len(sys.argv):
            try:
                min_move_pct = float(sys.argv[i + 1])
            except ValueError:
                print(f"Error: --min-move value must be a number, got '{sys.argv[i + 1]}'")
                sys.exit(1)
            i += 2
        elif sys.argv[i] == "--max-move" and i + 1 < len(sys.argv):
            try:
                max_move_pct = float(sys.argv[i + 1])
            except ValueError:
                print(f"Error: --max-move value must be a number, got '{sys.argv[i + 1]}'")
                sys.exit(1)
            i += 2
        elif sys.argv[i] == "--consol-range" and i + 1 < len(sys.argv):
            try:
                consolidation_range_pct = float(sys.argv[i + 1])
            except ValueError:
                print(f"Error: --consol-range value must be a number, got '{sys.argv[i + 1]}'")
                sys.exit(1)
            i += 2
        elif sys.argv[i] == "--debug":
            debug = True
            i += 1
        else:
            print(f"Warning: Unknown option '{sys.argv[i]}' (ignored)")
            i += 1
    
    # Run analysis and get zones
    demand_zone, supply_zone, current_price, atr, all_zones = analyze_zones(
        symbol, 
        lookback_days, 
        sensitivity,
        min_move_pct=min_move_pct,
        max_move_pct=max_move_pct,
        consolidation_range_pct=consolidation_range_pct,
        debug=debug
    )
    
    # Print results
    if demand_zone or supply_zone or current_price:
        print(f"\n{'='*80}")
        print(f"ZONE ANALYSIS - {symbol}")
        print(f"{'='*80}")
        print(f"Lookback: {lookback_days} days | Sensitivity: {sensitivity}")
        if min_move_pct is not None or max_move_pct is not None:
            min_disp = f"{min_move_pct:.1f}%" if min_move_pct else "default"
            max_disp = f"{max_move_pct:.1f}%" if max_move_pct else "default"
            print(f"Move Range: {min_disp} to {max_disp}")
        if consolidation_range_pct is not None:
            print(f"Consolidation Range: {consolidation_range_pct:.2f}% of prior move")
        if current_price:
            print(f"Current Price: ${current_price:.2f}")
        if atr:
            print(f"ATR (14):      ${atr:.2f}")
        print()
        
        # Debug output: show all detected zones
        if debug and all_zones:
            print(f"DEBUG: All detected zones ({len(all_zones)} total):")
            for idx, zone in enumerate(all_zones, 1):
                print(f"  {idx}. {zone}")
            print()
        
        if demand_zone:
            if demand_zone.pattern_type == "MANUAL":
                print("[+] DEMAND ZONE (manual anchor: selected daily candle low-to-high range):")
            elif demand_zone.pattern_type == "ORIGIN":
                print("[+] DEMAND ZONE (fresh bullish-move origin: selected candle low-to-high range):")
            else:
                print("[+] DEMAND ZONE (automatic pattern detection):")
            print(f"  {demand_zone}")
            print()
        else:
            print("[-] No fresh DEMAND zone found")
            print()
        
        if supply_zone:
            if supply_zone.pattern_type == "ORIGIN":
                print("[+] SUPPLY ZONE (fresh bearish-move origin: selected candle low-to-high range):")
            else:
                print("[+] SUPPLY ZONE (automatic pattern detection):")
            print(f"  {supply_zone}")
            print()
        else:
            print("[-] No fresh SUPPLY zone found")
            print()
        
        if demand_zone and supply_zone and current_price:
            spread = supply_zone.low - demand_zone.high
            distance_to_demand = current_price - demand_zone.high
            distance_to_supply = supply_zone.low - current_price
            print(f"TRADE SETUP:")
            print(f"  Current Price:  ${current_price:.2f}")
            print(f"  Entry Range:    ${demand_zone.low:.2f} - ${demand_zone.high:.2f}")
            print(f"  Target:         ${supply_zone.low:.2f}")
            print(f"  Risk/Reward:    ${spread:.2f} (spread from demand high to supply low)")
            print(f"  Distance to Entry: ${distance_to_demand:.2f} (current to demand high)")
            print(f"  Distance to Target: ${distance_to_supply:.2f} (current to supply low)")
            if atr:
                print(f"  ATR (14):       ${atr:.2f}")
            print(f"{'='*80}")
        else:
            print("[-] Cannot setup trade: Missing demand zone, supply zone, or current price")
            print(f"{'='*80}")
    else:
        print(f"No zones found for {symbol} in the last {lookback_days} days")
        print("Try increasing --lookback value or using --sensitivity aggressive")
        print("Or adjust --min-move lower to catch smaller swings")


if __name__ == "__main__":
    main()
