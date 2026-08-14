#!/usr/bin/env python3
"""Return today's realized max loss as a single value line.

Usage:
    python max_loss.py

Output:
    MAX_LOSS_TODAY=<amount>

Notes:
- Uses the same realized P/L computation pipeline as daily_pl.py.
- If today's realized P/L is positive, max loss is reported as 0.00.
"""

from __future__ import annotations

from pathlib import Path
import sys

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from roles.credentials import bootstrap_trading_auth

import daily_pl


def get_todays_max_loss() -> float:
    """Return today's realized max loss as a positive dollar amount."""
    _, trading_client = bootstrap_trading_auth("max_loss.py")
    fills = daily_pl._fetch_fills(trading_client)
    current_position_map = daily_pl._build_current_position_map(trading_client)
    summary = daily_pl._compute_daily_summary(fills, current_position_map)
    realized_pnl = float(summary.get("daily_realized_pnl", 0.0))
    return max(0.0, -realized_pnl)


def main() -> int:
    try:
        max_loss = get_todays_max_loss()
    except Exception as exc:
        print(f"Error computing today's max loss: {exc}")
        return 1

    print(f"MAX_LOSS_TODAY={max_loss:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
