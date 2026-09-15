"""
Position Manager for FOMO Trade

Provides utilities for risk_management.py and other scripts to interact with
the position tracking system used by fomo_trade.py.

Usage:
    from fomo_position_manager import get_all_positions, close_position_emergency, etc.
"""

import sys
import json
from pathlib import Path
from datetime import datetime

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
POSITIONS_DIR = WORKSPACE_ROOT / "positions"


def get_all_positions() -> list[dict]:
    """
    Get all active positions from tracking files.
    
    Returns:
        List of position dicts with: symbol, entry_price, original_stop_loss, 
        adjusted_stop_loss, target1, target2, first_target_filled, qty, last_updated
    """
    positions = []
    
    if not POSITIONS_DIR.exists():
        return positions
    
    for position_file in POSITIONS_DIR.glob("*.txt"):
        try:
            with open(position_file, 'r') as f:
                position_data = json.load(f)
                positions.append(position_data)
        except Exception as e:
            print(f"[ERROR] Could not read {position_file.name}: {e}")
    
    return positions


def get_position(symbol: str) -> dict | None:
    """
    Get a specific position by symbol.
    
    Args:
        symbol: Trading symbol
    
    Returns:
        Position dict or None if not found
    """
    position_file = POSITIONS_DIR / f"{symbol}.txt"
    
    if not position_file.exists():
        return None
    
    try:
        with open(position_file, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"[ERROR] Could not load position for {symbol}: {e}")
        return None


def update_position(symbol: str, **kwargs) -> bool:
    """
    Update position tracking data.
    
    Args:
        symbol: Trading symbol
        **kwargs: Fields to update (e.g., adjusted_stop_loss=99.50)
    
    Returns:
        True if update successful, False otherwise
    """
    position_data = get_position(symbol)
    
    if not position_data:
        print(f"[ERROR] No position file found for {symbol}")
        return False
    
    # Update provided fields
    for key, value in kwargs.items():
        if key in position_data:
            position_data[key] = value
    
    position_data["last_updated"] = datetime.now().isoformat()
    
    try:
        position_file = POSITIONS_DIR / f"{symbol}.txt"
        with open(position_file, 'w') as f:
            json.dump(position_data, f, indent=2)
        return True
    except Exception as e:
        print(f"[ERROR] Could not update position for {symbol}: {e}")
        return False


def close_position(symbol: str) -> bool:
    """
    Remove/close a position file (when trade is fully cleared).
    
    Args:
        symbol: Trading symbol
    
    Returns:
        True if file was removed, False otherwise
    """
    position_file = POSITIONS_DIR / f"{symbol}.txt"
    
    try:
        if position_file.exists():
            position_file.unlink()
            print(f"[INFO] Position file closed for {symbol}")
            return True
        return False
    except Exception as e:
        print(f"[ERROR] Could not close position file for {symbol}: {e}")
        return False


def get_positions_by_status(first_target_filled: bool = None) -> list[dict]:
    """
    Get positions filtered by status.
    
    Args:
        first_target_filled: True to get positions where target 1 was hit, 
                            False for entry positions, None for all
    
    Returns:
        List of matching positions
    """
    all_positions = get_all_positions()
    
    if first_target_filled is None:
        return all_positions
    
    return [p for p in all_positions if p.get("first_target_filled") == first_target_filled]


def print_position_summary():
    """Print a summary of all active positions."""
    positions = get_all_positions()
    
    if not positions:
        print("No active positions")
        return
    
    print(f"\n{'─'*80}")
    print(f"ACTIVE POSITIONS SUMMARY")
    print(f"{'─'*80}")
    
    entry_positions = [p for p in positions if not p.get("first_target_filled")]
    profit_positions = [p for p in positions if p.get("first_target_filled")]
    
    if entry_positions:
        print(f"\n📍 ENTRY POSITIONS ({len(entry_positions)}):")
        for pos in entry_positions:
            print(f"  {pos['symbol']}: Entry ${pos['entry_price']:.2f} | "
                  f"Stop ${pos['original_stop_loss']:.2f} | Target1 ${pos['target1']:.2f}")
    
    if profit_positions:
        print(f"\n✓ FIRST TARGET HIT ({len(profit_positions)}):")
        for pos in profit_positions:
            print(f"  {pos['symbol']}: Adjusted Stop ${pos['adjusted_stop_loss']:.2f} | "
                  f"Target2 ${pos['target2']:.2f} | Qty {pos.get('qty', 'N/A')}")
    
    print(f"{'─'*80}\n")


# Example usage
if __name__ == "__main__":
    print_position_summary()
    
    # Example: Get all positions
    all_pos = get_all_positions()
    print(f"Total positions: {len(all_pos)}")
    
    # Example: Get specific position
    if all_pos:
        symbol = all_pos[0]["symbol"]
        pos = get_position(symbol)
        print(f"\nPosition details for {symbol}:")
        print(json.dumps(pos, indent=2, default=str))
