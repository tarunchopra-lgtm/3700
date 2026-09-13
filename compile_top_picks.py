#!/usr/bin/env python3
"""
Compile top 10 picks from existing ticker files without re-running zone scan.
Parses all results/ticker/*.output files and creates top 10 sorted picks.
"""

import re
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass

@dataclass
class Trade:
    symbol: str
    current_price: float
    atr: float
    demand_zone_low: float
    demand_zone_high: float
    demand_zone_size: float
    demand_distance: float
    demand_formed: str
    demand_is_fresh: bool
    demand_notes: str
    supply_zone_low: float
    supply_zone_high: float
    supply_zone_size: float
    supply_distance: float
    supply_formed: str
    supply_is_fresh: bool
    supply_notes: str
    stop_loss_distance: float
    profit_target: float
    rr_ratio: float
    
    def is_actionable(self) -> bool:
        """Check if trade is within ATR and both zones are fresh"""
        return self.demand_distance <= self.atr and self.demand_is_fresh and self.supply_is_fresh
    
    def sort_key(self) -> tuple:
        """Sort by biggest distance from demand to supply (R:R ratio) in descending order"""
        # Spread = supply_low - demand_high (negative value = good R:R)
        # Negate to sort descending (biggest spread first)
        spread = self.supply_zone_low - self.demand_zone_high
        return (-spread, self.demand_distance)  # Biggest spread first, then closer demand zones

def parse_ticker_file(filepath: Path) -> Trade | None:
    """Parse a single ticker output file"""
    try:
        content = filepath.read_text(encoding='utf-8')
        symbol = filepath.stem  # filename without extension
        
        # Extract price
        price_match = re.search(r'Price:\s*\$([0-9.]+)', content)
        if not price_match:
            return None
        price = float(price_match.group(1))
        
        # Extract ATR
        atr_match = re.search(r'ATR \(14-day\):\s*\$([0-9.]+)', content)
        if not atr_match:
            return None
        atr = float(atr_match.group(1))
        
        # Extract Demand Zone
        demand_range = re.search(r'DEMAND ZONE.*?Range:\s*\$([0-9.]+)\s*-\s*\$([0-9.]+)', content, re.DOTALL)
        demand_size = re.search(r'DEMAND ZONE.*?Size:\s*\$([0-9.]+)', content, re.DOTALL)
        demand_dist = re.search(r'DEMAND ZONE.*?Distance:\s*\$([0-9.]+)', content, re.DOTALL)
        demand_formed = re.search(r'DEMAND ZONE.*?Formed:\s*([0-9-]+)', content, re.DOTALL)
        demand_fresh = re.search(r'DEMAND ZONE.*?FRESHNESS:\s*(FRESH|STALE)', content, re.DOTALL)
        demand_notes = re.search(r'DEMAND ZONE.*?Notes:\s*(.+?)(?:\n|$)', content, re.DOTALL)
        
        if not all([demand_range, demand_dist, demand_fresh]):
            return None
        
        demand_low = float(demand_range.group(1))
        demand_high = float(demand_range.group(2))
        demand_sz = float(demand_size.group(1)) if demand_size else 0
        demand_d = float(demand_dist.group(1))
        demand_frm = demand_formed.group(1) if demand_formed else "N/A"
        demand_f = demand_fresh.group(1) == "FRESH"
        demand_n = demand_notes.group(1).strip() if demand_notes else ""
        
        # Extract Supply Zone
        supply_range = re.search(r'SUPPLY ZONE.*?Range:\s*\$([0-9.]+)\s*-\s*\$([0-9.]+)', content, re.DOTALL)
        supply_size = re.search(r'SUPPLY ZONE.*?Size:\s*\$([0-9.]+)', content, re.DOTALL)
        supply_dist = re.search(r'SUPPLY ZONE.*?Distance:\s*\$([0-9.]+)', content, re.DOTALL)
        supply_formed = re.search(r'SUPPLY ZONE.*?Formed:\s*([0-9-]+)', content, re.DOTALL)
        supply_fresh = re.search(r'SUPPLY ZONE.*?FRESHNESS:\s*(FRESH|STALE)', content, re.DOTALL)
        supply_notes = re.search(r'SUPPLY ZONE.*?Notes:\s*(.+?)(?:\n|$)', content, re.DOTALL)
        
        if not all([supply_range, supply_dist, supply_fresh]):
            return None
        
        supply_low = float(supply_range.group(1))
        supply_high = float(supply_range.group(2))
        supply_sz = float(supply_size.group(1)) if supply_size else 0
        supply_d = float(supply_dist.group(1))
        supply_frm = supply_formed.group(1) if supply_formed else "N/A"
        supply_f = supply_fresh.group(1) == "FRESH"
        supply_n = supply_notes.group(1).strip() if supply_notes else ""
        
        # Extract Trade Metrics
        sl_dist = re.search(r'Stop Loss Distance:\s*\$([0-9.]+)', content)
        pt = re.search(r'Profit Target \(PT\):\s*\$(-?[0-9.]+)', content)
        rr = re.search(r'Risk:Reward Ratio:\s*1:(-?[0-9.]+)', content)
        
        stop_loss = float(sl_dist.group(1)) if sl_dist else 0
        profit_target = float(pt.group(1)) if pt else 0
        rr_ratio = float(rr.group(1)) if rr else 0
        
        return Trade(
            symbol=symbol,
            current_price=price,
            atr=atr,
            demand_zone_low=demand_low,
            demand_zone_high=demand_high,
            demand_zone_size=demand_sz,
            demand_distance=demand_d,
            demand_formed=demand_frm,
            demand_is_fresh=demand_f,
            demand_notes=demand_n,
            supply_zone_low=supply_low,
            supply_zone_high=supply_high,
            supply_zone_size=supply_sz,
            supply_distance=supply_d,
            supply_formed=supply_frm,
            supply_is_fresh=supply_f,
            supply_notes=supply_n,
            stop_loss_distance=stop_loss,
            profit_target=profit_target,
            rr_ratio=rr_ratio
        )
    except Exception as e:
        return None

def compile_picks(ticker_dir: Path, output_path: Path) -> None:
    """Compile top 10 picks from all ticker files"""
    
    # Parse all ticker files
    trades = []
    ticker_files = sorted(ticker_dir.glob("*.output"))
    print(f"Parsing {len(ticker_files)} ticker files...")
    
    for ticker_file in ticker_files:
        trade = parse_ticker_file(ticker_file)
        if trade:
            trades.append(trade)
    
    print(f"Parsed {len(trades)} trades successfully")
    
    # Filter for actionable trades (within ATR and fresh)
    actionable = [t for t in trades if t.is_actionable()]
    print(f"Actionable trades (within 1 ATR + FRESH): {len(actionable)}")
    
    # Sort by demand distance, then supply distance
    actionable.sort(key=lambda t: t.sort_key())
    
    # Write top 10 to file
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write("="*120 + "\n")
        f.write("TOP 10 TRADING PICKS - FRESH ZONES (Biggest R:R Ratio, Within 1 ATR)\n")
        f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("="*120 + "\n\n")
        f.write(f"Total Actionable Stocks (within 1 ATR + FRESH): {len(actionable)}\n")
        f.write(f"Top 10 shown below (sorted by BIGGEST distance from demand to supply zone)\n\n")
        f.write("FRESHNESS CRITERIA:\n")
        f.write("  - Demand zone: NOT reached in last 5 days (untested support)\n")
        f.write("  - Supply zone: NOT reached in last 3 days (untested resistance)\n")
        f.write("  - Demand zone must be within 1 ATR distance from current price\n")
        f.write("  - Sorted by biggest distance from demand high to supply low (best R:R)\n\n")
        
        # Write top 10
        for rank, trade in enumerate(actionable[:10], 1):
            pct_atr = (trade.demand_distance / trade.atr) * 100 if trade.atr > 0 else 0
            spread = trade.supply_zone_low - trade.demand_zone_high
            
            f.write(f"{rank}. {trade.symbol}\n")
            f.write(f"{'-'*120}\n")
            f.write(f"Current Price:       ${trade.current_price:.2f}\n")
            f.write(f"ATR (14-day):        ${trade.atr:.2f}\n\n")
            
            f.write(f"DEMAND ZONE:         ${trade.demand_zone_low:.2f} - ${trade.demand_zone_high:.2f}\n")
            f.write(f"  Distance:          ${trade.demand_distance:.2f} ({pct_atr:.0f}% of ATR)\n")
            f.write(f"  Formed:            {trade.demand_formed}\n")
            f.write(f"  Freshness:         {'✓ FRESH' if trade.demand_is_fresh else '✗ STALE'} - {trade.demand_notes}\n\n")
            
            f.write(f"SUPPLY ZONE:         ${trade.supply_zone_low:.2f} - ${trade.supply_zone_high:.2f}\n")
            f.write(f"  Distance:          ${trade.supply_distance:.2f}\n")
            f.write(f"  Formed:            {trade.supply_formed}\n")
            f.write(f"  Freshness:         {'✓ FRESH' if trade.supply_is_fresh else '✗ STALE'} - {trade.supply_notes}\n\n")
            
            f.write(f"TRADE SETUP:\n")
            f.write(f"  Entry Range:       ${trade.demand_zone_low:.2f} - ${trade.demand_zone_high:.2f}\n")
            f.write(f"  Stop Loss:         ${trade.demand_zone_low - 0.01:.2f}\n")
            f.write(f"  Profit Target:     ${trade.supply_zone_low:.2f}\n")
            f.write(f"  Risk Amount:       ${trade.stop_loss_distance:.2f}\n")
            f.write(f"  Reward Amount:     ${trade.profit_target:.2f}\n")
            f.write(f"  Demand→Supply:     ${spread:.2f} [SORT METRIC]\n")
            f.write(f"  RISK:REWARD:       1:{trade.rr_ratio:.2f}\n\n")
    
    print(f"[OK] Output written to: {output_path}")
    print(f"[OK] Top {min(10, len(actionable))} picks compiled successfully")

if __name__ == "__main__":
    base_dir = Path(__file__).parent
    ticker_dir = base_dir / "results" / "ticker"
    output_path = base_dir / "results" / "pick.output"
    compile_picks(ticker_dir, output_path)
