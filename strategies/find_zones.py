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
        min_prior_move_pct: float = 0.5) -> List[Zone]:
    """
    DBR (Drop Base Rally) - DEMAND ZONE
    
    Pattern: Sharp down move → 1-3 small candles → sharp up move
    Zone = the consolidation area (support where buyers stepped in)
    """
    zones = []
    
    for i in range(1, len(candles) - 2):
        down_candle = candles[i]
        if not down_candle.is_down():
            continue
        
        down_move_size = down_candle.open - down_candle.close
        down_move_pct = (down_move_size / down_candle.open) * 100
        
        if down_move_pct < min_prior_move_pct:
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
            
            # Check for up candle (rally) after consolidation
            up_candle = candles[consol_end + 1]
            if not up_candle.is_up():
                continue
            
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
        min_prior_move_pct: float = 0.5) -> List[Zone]:
    """
    RBR (Rally Base Rally) - DEMAND ZONE (continuation)
    
    Pattern: Sharp up move → 1-3 small candles → sharp up move (higher)
    Zone = the consolidation area (support where buyers defended and broke higher)
    """
    zones = []
    
    for i in range(1, len(candles) - 2):
        up_candle = candles[i]
        if not up_candle.is_up():
            continue
        
        up_move_size = up_candle.close - up_candle.open
        up_move_pct = (up_move_size / up_candle.open) * 100
        
        if up_move_pct < min_prior_move_pct:
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
            
            # Check for up candle (confirmation) after consolidation
            up_candle_2 = candles[consol_end + 1]
            if not up_candle_2.is_up():
                continue
            
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
        min_prior_move_pct: float = 0.5) -> List[Zone]:
    """
    RBD (Rally Base Drop) - SUPPLY ZONE
    
    Pattern: Sharp up move → 1-3 small candles → sharp down move
    Zone = the consolidation area (resistance where sellers rejected)
    """
    zones = []
    
    for i in range(1, len(candles) - 2):
        up_candle = candles[i]
        if not up_candle.is_up():
            continue
        
        up_move_size = up_candle.close - up_candle.open
        up_move_pct = (up_move_size / up_candle.open) * 100
        
        if up_move_pct < min_prior_move_pct:
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
            
            # Check for down candle after consolidation
            down_candle = candles[consol_end + 1]
            if not down_candle.is_down():
                continue
            
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
        min_prior_move_pct: float = 0.5) -> List[Zone]:
    """
    DBD (Drop Base Drop) - SUPPLY ZONE (continuation)
    
    Pattern: Sharp down move → 1-3 small candles → sharp down move (lower)
    Zone = the consolidation area (resistance where sellers broke through)
    """
    zones = []
    
    for i in range(1, len(candles) - 2):
        down_candle = candles[i]
        if not down_candle.is_down():
            continue
        
        down_move_size = down_candle.open - down_candle.close
        down_move_pct = (down_move_size / down_candle.open) * 100
        
        if down_move_pct < min_prior_move_pct:
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
            
            # Check for down candle after consolidation - must be lower
            down_candle_2 = candles[consol_end + 1]
            if not down_candle_2.is_down():
                continue
            
            if down_candle_2.close >= down_candle.close:
                continue  # Must be lower (continuation, not bounce)
            
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


def is_demand_zone_fresh(zone: Zone, candles: List[Candle], days_threshold: int = 5) -> bool:
    """
    Demand zone is FRESH if price has NOT reached it within last N days.
    This means it's an untested support level (fresh opportunity).
    """
    if zone.zone_type != "DEMAND":
        return False
    
    info = get_zone_freshness_info(zone, candles)
    if not info["was_reached"]:
        return True  # Never reached = fresh
    
    days_since = info["days_since_price_reached"]
    # Fresh if NOT reached in last N days (reached > N days ago or never)
    return days_since is not None and days_since > days_threshold


def is_supply_zone_fresh(zone: Zone, candles: List[Candle], days_threshold: int = 3) -> bool:
    """
    Supply zone is FRESH if price has NOT reached it within last N days.
    This means it's an untested resistance level.
    """
    if zone.zone_type != "SUPPLY":
        return False
    
    info = get_zone_freshness_info(zone, candles)
    if info["was_reached"]:
        days_since = info["days_since_price_reached"]
        if days_since is not None and days_since <= days_threshold:
            return False  # Reached recently = not fresh
    
    return True  # Either never reached or reached > N days ago = fresh


def find_best_zones(candles: List[Candle], 
                    config: dict) -> tuple[Zone | None, Zone | None]:
    """
    Find the best demand and supply zones based on freshness criteria.
    
    Logic:
    - Demand zone: RBR or DBR that has NOT been touched in last 5 days
    - Supply zone: RBD or DBD that has NOT been touched in last 3 days
    - If most recent was touched, use prior untouched zone
    
    Returns:
        (demand_zone, supply_zone) tuple
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
    # When adjacent zones exist and both are untested, this returns the prior zone
    demand_zones.sort(key=lambda x: x.date_confirmed)
    supply_zones.sort(key=lambda x: x.date_confirmed)
    
    # Find freshest demand zone (not touched in last 5 days)
    # Return the OLDEST untested zone (so if DBR and RBR both untested, return DBR)
    best_demand = None
    for zone in demand_zones:
        if is_demand_zone_fresh(zone, candles, days_threshold=5):
            best_demand = zone
            break  # Return the first (oldest) fresh zone
    
    # Find freshest supply zone (not touched in last 3 days)
    # Return the OLDEST untested zone (so if RBD and DBD both untested, return RBD)
    best_supply = None
    for zone in supply_zones:
        if is_supply_zone_fresh(zone, candles, days_threshold=3):
            best_supply = zone
            break
    
    return best_demand, best_supply


def analyze_zones(symbol: str, 
                  lookback_days: int = 50,
                  sensitivity: str = "balanced") -> tuple[Zone | None, Zone | None]:
    """
    Main analysis function - finds best demand and supply zones
    
    Args:
        symbol: Stock/crypto symbol to analyze
        lookback_days: How many days back to scan (default 50)
        sensitivity: "conservative", "balanced", or "aggressive"
    
    Returns:
        (demand_zone, supply_zone) or (None, None) if not found
    """
    
    # Configure parameters based on sensitivity
    params = {
        "conservative": {
            "min_consolidation": 1,
            "consolidation_range_pct": 1.0,
            "min_prior_move_pct": 0.5
        },
        "balanced": {
            "min_consolidation": 1,
            "consolidation_range_pct": 2.0,
            "min_prior_move_pct": 0.25
        },
        "aggressive": {
            "min_consolidation": 1,
            "consolidation_range_pct": 3.5,
            "min_prior_move_pct": 0.15
        }
    }
    
    if sensitivity not in params:
        sensitivity = "balanced"
    
    config = params[sensitivity]
    
    # Fetch data
    candles = fetch_daily_bars(symbol, lookback_days)
    
    if not candles:
        return None, None
    
    # Find best zones
    demand_zone, supply_zone = find_best_zones(candles, config)
    
    return demand_zone, supply_zone


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
        print("  <TICKER>          Stock or crypto symbol (e.g., AAPL, SPY, MU, BTC/USD, ETH/USD)")
        print("\nOPTIONS:")
        print("  --lookback DAYS   Number of days to scan (default: 50)")
        print("  --sensitivity S   Sensitivity mode: conservative, balanced, or aggressive (default: balanced)")
        print("  --help, -h        Show this help message")
        print("\nEXAMPLES:")
        print("  python strategies/find_zones.py AAPL")
        print("  python strategies/find_zones.py AAPL --lookback 100")
        print("  python strategies/find_zones.py SPY --sensitivity aggressive")
        print("  python strategies/find_zones.py MU --lookback 200 --sensitivity conservative")
        print("  python strategies/find_zones.py BTC/USD --lookback 50")
        print("\nOUTPUT:")
        print("  Lists all DEMAND and SUPPLY zones found with pattern type (DBR/RBR/RBD/DBD)")
        print("  Zones sorted by most recent first")
        print("  Statistics: total count, average size, min/max size, average consolidation length")
        print()
        sys.exit(0 if len(sys.argv) > 1 else 1)
    
    symbol = sys.argv[1].upper()
    lookback_days = 50
    sensitivity = "balanced"
    
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
        else:
            print(f"Warning: Unknown option '{sys.argv[i]}' (ignored)")
            i += 1
    
    # Run analysis and get zones
    demand_zone, supply_zone = analyze_zones(symbol, lookback_days, sensitivity)
    
    # Print results
    if demand_zone or supply_zone:
        print(f"\n{'='*80}")
        print(f"ZONE ANALYSIS - {symbol}")
        print(f"{'='*80}")
        print(f"Lookback: {lookback_days} days | Sensitivity: {sensitivity}")
        print()
        
        if demand_zone:
            print(f"✓ DEMAND ZONE (RBR/DBR - untested support, not reached in last 5 days):")
            print(f"  {demand_zone}")
            print()
        else:
            print("✗ No fresh DEMAND zone found (all recent demand zones were tested)")
            print()
        
        if supply_zone:
            print(f"✓ SUPPLY ZONE (RBD/DBD - untested resistance, not reached in last 3 days):")
            print(f"  {supply_zone}")
            print()
        else:
            print("✗ No fresh SUPPLY zone found (all recent supply zones were tested)")
            print()
        
        if demand_zone and supply_zone:
            spread = supply_zone.low - demand_zone.high
            print(f"TRADE SETUP:")
            print(f"  Entry Range:    ${demand_zone.low:.2f} - ${demand_zone.high:.2f}")
            print(f"  Target:         ${supply_zone.low:.2f}")
            print(f"  Risk/Reward:    ${spread:.2f} (spread from demand high to supply low)")
            print(f"{'='*80}")
        else:
            print("❌ Cannot setup trade: Missing demand or supply zone")
            print(f"{'='*80}")
    else:
        print(f"No zones found for {symbol} in the last {lookback_days} days")
        print("Try increasing --lookback value or using --sensitivity aggressive")


if __name__ == "__main__":
    main()
