#!/usr/bin/env python3
"""
zone_scan.py - Batch Zone Scanning with Individual Ticker Reports

Scans stocks from watchlists in batches of 100 (25 parallel workers),
finds demand/supply zones, and generates individual ticker reports.
Post-processes to create top 10 picks sorted by distance metrics.

Usage:
    python zone_scan.py

Output:
    results/ticker/SYMBOL.output - Individual reports per stock
    results/pick.output - Top 10 picks (within ATR, sorted by distance)
"""

import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

# Add parent directory to path so we can import roles and indicator modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from roles.credentials import CredentialsRole
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.enums import DataFeed
from indicator.atr import _calculate_atr

# Thread-safe locks
output_lock = threading.Lock()
stats_lock = threading.Lock()


@dataclass
class StockSetup:
    """Represents a trading setup for a stock"""
    symbol: str
    current_price: float
    demand_zone_low: float
    demand_zone_high: float
    demand_zone_size: float
    demand_formed: str
    supply_zone_low: float
    supply_zone_high: float
    supply_zone_size: float
    supply_formed: str
    atr: float
    demand_is_fresh: bool = True  # NEW: zone hasn't been recently tested
    supply_is_fresh: bool = True  # NEW: zone hasn't been recently tested
    demand_freshness_notes: str = ""  # NEW: notes on why zone is/isn't fresh
    supply_freshness_notes: str = ""  # NEW: notes on why zone is/isn't fresh
    
    def distance_to_demand(self) -> float:
        """Distance from current price to demand zone"""
        if self.current_price > self.demand_zone_high:
            return self.current_price - self.demand_zone_high
        elif self.current_price < self.demand_zone_low:
            return self.demand_zone_low - self.current_price
        return 0  # Inside zone
    
    def distance_to_supply(self) -> float:
        """Distance from current price to supply zone"""
        if self.current_price < self.supply_zone_low:
            return self.supply_zone_low - self.current_price
        elif self.current_price > self.supply_zone_high:
            return self.current_price - self.supply_zone_high
        return 0  # Inside zone
    
    def is_demand_in_atr_reach(self) -> bool:
        """Check if demand zone is within 1 ATR from current price"""
        return self.distance_to_demand() <= self.atr and self.demand_is_fresh
    
    def stop_loss_to_profit_target_ratio(self) -> float:
        """R:R ratio - profit target / stop loss"""
        # Stop loss = below demand zone
        stop_loss = self.current_price - self.demand_zone_low
        
        # Profit target = at supply zone
        profit_target = self.supply_zone_low - self.current_price
        
        if stop_loss <= 0:
            return 0
        
        return profit_target / stop_loss


def load_watchlist(filename: str) -> list[str]:
    """Load stock symbols from a file"""
    # Look in parent directory's lists folder (since we're in strategies/)
    path = Path(__file__).parent.parent / "lists" / filename
    if not path.exists():
        print(f"Warning: {filename} not found at {path}")
        return []
    
    symbols = []
    with open(path, 'r') as f:
        for line in f:
            symbol = line.strip()
            if symbol:
                symbols.append(symbol)
    
    return symbols


def run_find_zones(symbol: str) -> dict | None:
    """
    Find demand and supply zones using corrected freshness logic.
    
    Demand zone: RBR or DBR that has NOT been touched in last 5 days
    Supply zone: RBD or DBD that has NOT been touched in last 3 days
    Returns spread: distance from demand high to supply low
    """
    try:
        from strategies.find_zones import analyze_zones
        
        demand_zone, supply_zone = analyze_zones(symbol, lookback_days=100, sensitivity='balanced')
        
        if not demand_zone or not supply_zone:
            return None
        
        spread = supply_zone.low - demand_zone.high
        
        return {
            'demand': {
                'low': demand_zone.low,
                'high': demand_zone.high,
                'date': demand_zone.date_formed.strftime('%Y-%m-%d'),
                'size': demand_zone.size,
                'pattern': demand_zone.pattern_type
            },
            'supply': {
                'low': supply_zone.low,
                'high': supply_zone.high,
                'date': supply_zone.date_formed.strftime('%Y-%m-%d'),
                'size': supply_zone.size,
                'pattern': supply_zone.pattern_type
            },
            'spread': spread
        }
    
    except Exception as e:
        return None


def get_current_price(symbol: str, client: StockHistoricalDataClient) -> float | None:
    """Get current price for a stock"""
    try:
        quote = client.get_stock_latest_quote(
            StockLatestQuoteRequest(symbol_or_symbols=symbol.upper())
        )
        if symbol.upper() in quote:
            return quote[symbol.upper()].ask_price
        return None
    except:
        return None


def get_atr(symbol: str, client: StockHistoricalDataClient) -> float | None:
    """Get ATR for a stock"""
    try:
        return _calculate_atr(client, symbol, is_crypto=False)
    except:
        return None


def check_zone_freshness(symbol: str, demand_low: float, demand_high: float, 
                        supply_low: float, supply_high: float,
                        current_price: float, client: StockHistoricalDataClient) -> tuple[bool, str, bool, str]:
    """
    Check if demand and supply zones are FRESH (not already tested by recent price action).
    
    A zone is STALE if:
    - Current price is already inside/below demand zone or inside/above supply zone
    - Recent candles (daily + hourly) have touched/wicked through the zone
    - Price has already broken into the zone
    
    Returns: (demand_is_fresh, demand_notes, supply_is_fresh, supply_notes)
    """
    try:
        # Primary check: Get last 3-5 daily candles (most reliable)
        now = datetime.now(timezone.utc)
        response = client.get_stock_bars(
            StockBarsRequest(
                symbol_or_symbols=[symbol],
                timeframe=TimeFrame.Day,
                start=now - timedelta(days=5),
                end=now,
                feed=DataFeed.IEX,
            )
        )
        
        data = getattr(response, "data", {})
        daily_bars = list(data.get(symbol, [])) if isinstance(data, dict) else []
        
        # Get last 3-6 hourly candles as secondary check
        try:
            response_hourly = client.get_stock_bars(
                StockBarsRequest(
                    symbol_or_symbols=[symbol],
                    timeframe=TimeFrame.Hour,
                    start=now - timedelta(hours=8),
                    end=now,
                    feed=DataFeed.IEX,
                )
            )
            hourly_data = getattr(response_hourly, "data", {})
            hourly_bars = list(hourly_data.get(symbol, [])) if isinstance(hourly_data, dict) else []
        except:
            hourly_bars = []
        
        # Check DEMAND ZONE freshness
        demand_is_fresh = True
        demand_notes = "Fresh - not tested recently"
        
        # Primary: If current price is at/below demand zone high, zone is STALE
        if current_price <= demand_high:
            demand_is_fresh = False
            demand_notes = f"STALE - Price reached zone (${current_price:.2f} <= zone ${demand_high:.2f})"
        else:
            # Secondary: Check if recent daily candles have tested the zone
            if len(daily_bars) >= 2:
                recent_daily = daily_bars[-2:]  # Last 2 daily candles
                for i, bar in enumerate(recent_daily):
                    bar_low = float(getattr(bar, "low", 0))
                    bar_high = float(getattr(bar, "high", 0))
                    # If daily candle low touched/entered demand zone
                    if bar_low <= demand_high and bar_low > demand_low * 0.95:  # within 5% of zone
                        day_offset = "today" if i == 1 else "yesterday"
                        demand_is_fresh = False
                        demand_notes = f"STALE - Daily candle tested zone {day_offset} (low ${bar_low:.2f})"
                        break
            
            # Tertiary: Check hourly candles if available and no daily issue found
            if demand_is_fresh and len(hourly_bars) >= 2:
                recent_hourly = hourly_bars[-3:]  # Last 3 hourly candles
                for i, bar in enumerate(recent_hourly):
                    bar_low = float(getattr(bar, "low", 0))
                    if bar_low <= demand_high and bar_low > demand_low * 0.95:
                        demand_is_fresh = False
                        demand_notes = f"STALE - Hourly candle tested zone ({i} hours ago, low ${bar_low:.2f})"
                        break
        
        # Check SUPPLY ZONE freshness
        supply_is_fresh = True
        supply_notes = "Fresh - not tested recently"
        
        # Primary: If current price is at/above supply zone low, zone is STALE
        if current_price >= supply_low:
            supply_is_fresh = False
            supply_notes = f"STALE - Price reached zone (${current_price:.2f} >= zone ${supply_low:.2f})"
        else:
            # Secondary: Check if recent daily candles have tested the zone
            if len(daily_bars) >= 2:
                recent_daily = daily_bars[-2:]  # Last 2 daily candles
                for i, bar in enumerate(recent_daily):
                    bar_high = float(getattr(bar, "high", 0))
                    # If daily candle high touched/entered supply zone
                    if bar_high >= supply_low and bar_high < supply_high * 1.05:  # within 5% of zone
                        day_offset = "today" if i == 1 else "yesterday"
                        supply_is_fresh = False
                        supply_notes = f"STALE - Daily candle tested zone {day_offset} (high ${bar_high:.2f})"
                        break
            
            # Tertiary: Check hourly candles if available and no daily issue found
            if supply_is_fresh and len(hourly_bars) >= 2:
                recent_hourly = hourly_bars[-3:]  # Last 3 hourly candles
                for i, bar in enumerate(recent_hourly):
                    bar_high = float(getattr(bar, "high", 0))
                    if bar_high >= supply_low and bar_high < supply_high * 1.05:
                        supply_is_fresh = False
                        supply_notes = f"STALE - Hourly candle tested zone ({i} hours ago, high ${bar_high:.2f})"
                        break
        
        return demand_is_fresh, demand_notes, supply_is_fresh, supply_notes
    
    except Exception as e:
        # If we can't check freshness, assume zones are NOT fresh (conservative approach)
        error_msg = str(e)[:40]
        return False, f"Check failed: {error_msg}", False, f"Check failed: {error_msg}"


def scan_stock(symbol: str, client: StockHistoricalDataClient, ticker_dir: Path) -> StockSetup | None:
    """Scan a single stock and write results to individual ticker file"""
    try:
        # Get zones
        zones = run_find_zones(symbol)
        if not zones:
            return None
        
        # Get current price
        price = get_current_price(symbol, client)
        if not price:
            return None
        
        # Get ATR
        atr = get_atr(symbol, client)
        if not atr:
            return None
        
        # Check zone freshness
        demand_fresh, demand_notes, supply_fresh, supply_notes = check_zone_freshness(
            symbol,
            zones['demand']['low'],
            zones['demand']['high'],
            zones['supply']['low'],
            zones['supply']['high'],
            price,
            client
        )
        
        # Create setup
        setup = StockSetup(
            symbol=symbol,
            current_price=price,
            demand_zone_low=zones['demand']['low'],
            demand_zone_high=zones['demand']['high'],
            demand_zone_size=zones['demand']['size'],
            demand_formed=zones['demand']['date'],
            supply_zone_low=zones['supply']['low'],
            supply_zone_high=zones['supply']['high'],
            supply_zone_size=zones['supply']['size'],
            supply_formed=zones['supply']['date'],
            atr=atr,
            demand_is_fresh=demand_fresh,
            supply_is_fresh=supply_fresh,
            demand_freshness_notes=demand_notes,
            supply_freshness_notes=supply_notes
        )
        
        # Write individual ticker file
        ticker_file = ticker_dir / f"{symbol}.output"
        with output_lock:
            with open(ticker_file, 'w', encoding='utf-8') as f:
                rr_ratio = setup.stop_loss_to_profit_target_ratio()
                dist_demand = setup.distance_to_demand()
                dist_supply = setup.distance_to_supply()
                in_atr = "YES" if setup.is_demand_in_atr_reach() else "NO"
                
                f.write("="*100 + "\n")
                f.write(f"TICKER: {setup.symbol}\n")
                f.write("="*100 + "\n\n")
                
                f.write("CURRENT STATUS:\n")
                f.write(f"  Price:        ${setup.current_price:.2f}\n")
                f.write(f"  ATR (14-day): ${setup.atr:.2f}\n")
                f.write(f"  Within ATR:   {in_atr}\n\n")
                
                f.write("DEMAND ZONE (Buy Zone):\n")
                f.write(f"  Range:        ${setup.demand_zone_low:.2f} - ${setup.demand_zone_high:.2f}\n")
                f.write(f"  Size:         ${setup.demand_zone_size:.2f}\n")
                f.write(f"  Distance:     ${dist_demand:.2f} ({(dist_demand/setup.atr)*100:.0f}% of ATR)\n")
                f.write(f"  Formed:       {setup.demand_formed}\n")
                f.write(f"  FRESHNESS:    {'FRESH' if setup.demand_is_fresh else 'STALE'}\n")
                f.write(f"  Notes:        {setup.demand_freshness_notes}\n\n")
                
                f.write("SUPPLY ZONE (Sell Zone):\n")
                f.write(f"  Range:        ${setup.supply_zone_low:.2f} - ${setup.supply_zone_high:.2f}\n")
                f.write(f"  Size:         ${setup.supply_zone_size:.2f}\n")
                f.write(f"  Distance:     ${dist_supply:.2f}\n")
                f.write(f"  Formed:       {setup.supply_formed}\n")
                f.write(f"  FRESHNESS:    {'FRESH' if setup.supply_is_fresh else 'STALE'}\n")
                f.write(f"  Notes:        {setup.supply_freshness_notes}\n\n")
                
                f.write("TRADE METRICS:\n")
                f.write(f"  Stop Loss Distance:  ${setup.current_price - setup.demand_zone_low:.2f}\n")
                f.write(f"  Profit Target (PT):  ${setup.supply_zone_low - setup.current_price:.2f}\n")
                f.write(f"  Risk:Reward Ratio:   1:{rr_ratio:.2f}\n")
        
        return setup
    
    except Exception as e:
        return None


def process_batch(batch_symbols: list, batch_num: int, client: StockHistoricalDataClient, ticker_dir: Path) -> list:
    """Process a batch of stocks in parallel (25 workers)"""
    batch_setups = []
    
    print(f"\n[BATCH {batch_num}] Processing {len(batch_symbols)} stocks...")
    
    with ThreadPoolExecutor(max_workers=25) as executor:
        futures = {
            executor.submit(scan_stock, symbol, client, ticker_dir): symbol 
            for symbol in batch_symbols
        }
        
        for i, future in enumerate(as_completed(futures), 1):
            symbol = futures[future]
            try:
                setup = future.result()
                if setup:
                    batch_setups.append(setup)
                    rr = setup.stop_loss_to_profit_target_ratio()
                    dist = setup.distance_to_demand()
                    status = "IN ATR" if setup.is_demand_in_atr_reach() else "OUT"
                    print(f"  [{i:2d}/{len(batch_symbols)}] {symbol:6s} | ${dist:6.2f} away | RR: 1:{rr:5.2f} | {status}")
                else:
                    print(f"  [{i:2d}/{len(batch_symbols)}] {symbol:6s} - No setup")
            except Exception as e:
                print(f"  [{i:2d}/{len(batch_symbols)}] {symbol:6s} - Error: {str(e)[:30]}")
    
    return batch_setups


def compile_top_picks(all_setups: list, pick_path: Path) -> None:
    """
    Filter and sort setups for top 10 picks:
    1. Filter: must be within ATR reach AND have FRESH zones (not tested recently)
    2. Sort by: distance to demand (ascending = closest first)
    3. Secondary sort: distance to supply (ascending = lowest first)
    """
    # Filter for stocks within ATR reach AND zones are FRESH (not stale)
    actionable = [s for s in all_setups if s.is_demand_in_atr_reach() and s.demand_is_fresh and s.supply_is_fresh]
    
    if not actionable:
        with open(pick_path, 'w', encoding='utf-8') as f:
            f.write("="*120 + "\n")
            f.write("TOP 10 TRADING PICKS - WITHIN ATR (FRESH ZONES ONLY)\n")
            f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("="*120 + "\n\n")
            f.write("No stocks with FRESH zones (within 1 ATR and not recently tested) found.\n")
            f.write("\nNote: Setups are filtered to exclude STALE zones that have been recently tested by price action.\n")
        return
    
    # Sort by distance to demand (closest first), then by distance to supply
    actionable.sort(key=lambda x: (x.distance_to_demand(), x.distance_to_supply()))
    
    # Write top 10 to pick.output
    with open(pick_path, 'w', encoding='utf-8') as f:
        f.write("="*120 + "\n")
        f.write("TOP 10 TRADING PICKS - FRESH ZONES (Within 1 ATR, Not Recently Tested)\n")
        f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("="*120 + "\n\n")
        f.write(f"Total Actionable Stocks (within 1 ATR + FRESH): {len(actionable)}\n")
        f.write(f"Top 10 shown below (sorted by proximity to fresh demand zone)\n\n")
        f.write("FRESHNESS CRITERIA:\n")
        f.write("  - Zone must NOT be already reached by current price\n")
        f.write("  - Zone must NOT be tested by recent candle wicks (last 2 hours)\n")
        f.write("  - Zone must be within 1 ATR distance from current price\n\n")
        
        for rank, setup in enumerate(actionable[:10], 1):
            rr_ratio = setup.stop_loss_to_profit_target_ratio()
            dist_demand = setup.distance_to_demand()
            dist_supply = setup.distance_to_supply()
            pct_atr = (dist_demand / setup.atr) * 100
            
            f.write(f"{rank}. {setup.symbol}\n")
            f.write(f"{'-'*120}\n")
            f.write(f"Current Price:       ${setup.current_price:.2f}\n")
            f.write(f"ATR (14-day):        ${setup.atr:.2f}\n\n")
            
            f.write(f"DEMAND ZONE:         ${setup.demand_zone_low:.2f} - ${setup.demand_zone_high:.2f}\n")
            f.write(f"  Distance:          ${dist_demand:.2f} ({pct_atr:.0f}% of ATR) [PRIMARY SORT]\n")
            f.write(f"  Formed:            {setup.demand_formed}\n")
            f.write(f"  Freshness:         {'✓ FRESH' if setup.demand_is_fresh else '✗ STALE'} - {setup.demand_freshness_notes}\n\n")
            
            f.write(f"SUPPLY ZONE:         ${setup.supply_zone_low:.2f} - ${setup.supply_zone_high:.2f}\n")
            f.write(f"  Distance:          ${dist_supply:.2f} [SECONDARY SORT]\n")
            f.write(f"  Formed:            {setup.supply_formed}\n")
            f.write(f"  Freshness:         {'✓ FRESH' if setup.supply_is_fresh else '✗ STALE'} - {setup.supply_freshness_notes}\n\n")
            
            f.write(f"TRADE SETUP:\n")
            f.write(f"  Entry Range:       ${setup.demand_zone_low:.2f} - ${setup.demand_zone_high:.2f}\n")
            f.write(f"  Stop Loss:         ${setup.demand_zone_low - 0.01:.2f}\n")
            f.write(f"  Profit Target:     ${setup.supply_zone_low:.2f}\n")
            f.write(f"  Risk Amount:       ${setup.current_price - setup.demand_zone_low:.2f}\n")
            f.write(f"  Reward Amount:     ${setup.supply_zone_low - setup.current_price:.2f}\n")
            f.write(f"  RISK:REWARD:       1:{rr_ratio:.2f}\n\n")


def main():
    """Main entry point"""
    print("\n" + "="*100)
    print("ZONE SCANNER - BATCH PROCESSING (100 stocks per batch)")
    print("="*100)
    
    # Load all watchlists
    watchlists = {
        'ETF': load_watchlist('etf.txt'),
        'SPY': load_watchlist('spy.txt'),
        'NASDAQ': load_watchlist('nasdaq.txt')
    }
    
    all_symbols = set()
    for symbols in watchlists.values():
        all_symbols.update(symbols)
    
    all_symbols = sorted(list(all_symbols))
    
    print(f"\nTotal unique stocks to scan: {len(all_symbols)}")
    print(f"Batch size: 100 stocks")
    print(f"Workers per batch: 25 parallel")
    
    # Initialize Alpaca client
    try:
        creds = CredentialsRole()
        client = StockHistoricalDataClient(creds.api_key, creds.secret_key)
    except Exception as e:
        print(f"Error: Failed to initialize Alpaca client: {e}")
        return
    
    # Create ticker directory for individual outputs
    ticker_dir = Path(__file__).parent.parent / "results" / "ticker"
    ticker_dir.mkdir(parents=True, exist_ok=True)
    
    # Process in batches of 100
    all_setups = []
    batch_size = 100
    num_batches = (len(all_symbols) + batch_size - 1) // batch_size
    
    print(f"\n[INFO] Starting scan in {num_batches} batches...")
    print("="*100)
    
    start_time = datetime.now()
    
    for batch_num in range(num_batches):
        batch_start = batch_num * batch_size
        batch_end = min(batch_start + batch_size, len(all_symbols))
        batch_symbols = all_symbols[batch_start:batch_end]
        
        batch_setups = process_batch(batch_symbols, batch_num + 1, client, ticker_dir)
        all_setups.extend(batch_setups)
        
        print(f"[BATCH {batch_num + 1}] Completed: {len(batch_setups)} setups found\n")
    
    elapsed = datetime.now() - start_time
    
    # Compile statistics
    actionable = [s for s in all_setups if s.is_demand_in_atr_reach()]
    fresh_actionable = [s for s in all_setups if s.is_demand_in_atr_reach() and s.demand_is_fresh and s.supply_is_fresh]
    
    print("="*100)
    print("SCAN COMPLETE")
    print("="*100)
    print(f"Total Stocks Scanned:        {len(all_symbols)}")
    print(f"Stocks with Zones:           {len(all_setups)}")
    print(f"Stocks Within 1 ATR:         {len(actionable)}")
    print(f"Stocks with FRESH Zones:     {len(fresh_actionable)}")
    print(f"Time Elapsed:                {elapsed}")
    
    # Create top 10 picks file
    pick_path = Path(__file__).parent.parent / "results" / "pick.output"
    compile_top_picks(all_setups, pick_path)
    
    print(f"\n[OK] Individual ticker files:  results/ticker/*.output ({len(all_setups)} files)")
    print(f"[OK] Top 10 picks file:       results/pick.output")
    
    # Console summary
    if actionable:
        actionable.sort(key=lambda x: (x.distance_to_demand(), x.distance_to_supply()))
        print(f"\n" + "="*100)
        print("QUICK PREVIEW - TOP 5 CLOSEST TO DEMAND")
        print("="*100)
        for rank, setup in enumerate(actionable[:5], 1):
            dist = setup.distance_to_demand()
            pct = (dist / setup.atr) * 100
            print(f"{rank}. {setup.symbol:6s} | ${setup.current_price:8.2f} | ${dist:6.2f} away ({pct:3.0f}% ATR) | Supply: ${setup.distance_to_supply():6.2f}")


if __name__ == "__main__":
    # Handle help flag
    if len(sys.argv) > 1 and sys.argv[1] in ["--help", "-h", "help"]:
        print(__doc__)
        print("SYNTAX:")
        print("  python strategies/zone_scan.py [OPTIONS]")
        print("\nOPTIONS:")
        print("  (No command-line options - uses watchlist files from lists/ directory)")
        print("  --help, -h        Show this help message")
        print("\nDESCRIPTION:")
        print("  Scans all stocks from watchlist files in batches of 100 stocks")
        print("  Uses 25 parallel workers per batch for fast processing")
        print("  Creates individual ticker report files for each stock with zones")
        print("  Generates top 10 picks sorted by proximity metrics")
        print("\nWATCHLIST FILES:")
        print("  lists/etf.txt - Exchange Traded Funds")
        print("  lists/spy.txt - S&P 500 stocks")
        print("  lists/nasdaq.txt - NASDAQ stocks")
        print("\nOUTPUT FILES:")
        print("  results/ticker/*.output - Individual stock reports with zone details")
        print("  results/pick.output - Top 10 actionable setups")
        print("\nEXAMPLE:")
        print("  python strategies/zone_scan.py")
        print("\nEXECUTION TIME:")
        print("  ~30-45 minutes for full scan of 541 stocks (batched, parallel)")
        print()
        sys.exit(0)
    
    main()
