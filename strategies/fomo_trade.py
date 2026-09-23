import os
import re
import sys
import requests
from alpaca.common.exceptions import APIError
from alpaca.trading.requests import GetOrderByIdRequest, GetOrdersRequest, LimitOrderRequest, MarketOrderRequest, ReplaceOrderRequest, StopLossRequest, StopOrderRequest, TakeProfitRequest
from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce, QueryOrderStatus
from alpaca.data.enums import DataFeed, OptionsFeed
from alpaca.data.historical import CryptoHistoricalDataClient, StockHistoricalDataClient, OptionHistoricalDataClient
from alpaca.data.requests import CryptoLatestTradeRequest, StockLatestTradeRequest, OptionLatestTradeRequest, OptionLatestQuoteRequest
import time
from datetime import datetime, time as dt_time
import json

from pathlib import Path
import sys

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from roles.credentials import bootstrap_trading_auth

# Position tracking directory
POSITIONS_DIR = WORKSPACE_ROOT / "positions"
POSITIONS_DIR.mkdir(exist_ok=True)

REQUEST_TIMEOUT_SECONDS = float(os.getenv("ALPACA_HTTP_TIMEOUT", "15"))


# ============================================================================
# BRACKET ORDER ARCHITECTURE (REDESIGNED - Single JSON per Symbol)
# ============================================================================
#
# DUAL-BRACKET ORDER SYSTEM (Per Symbol):
# Each position tracks TWO bracket orders with 50% qty each
#
# FILE: positions/SYMBOL.json (One file per symbol)
# {
#   "symbol": "GOOGL",
#   "qty": 2,                    ← Total position qty (both brackets)
#   "entry_price": 345.0,        ← Entry price (same for both)
#   "entry_time": "2026-09-17T07:30:00",
#   "brackets": {
#     "bracket1": {
#       "entry_order_id": "fb08c7ae-...",
#       "stop_order_id": "5894817b-...",
#       "target_order_id": "ae8a3aa5-...",
#       "qty": 1,                ← 50% of total
#       "target_price": 350.0,
#       "status": "pending"      ← "pending" | "target_filled" | "stopped_out"
#     },
#     "bracket2": {
#       "entry_order_id": "8ba74041-...",
#       "stop_order_id": "c4f2d1a0-...",
#       "target_order_id": "f1e2d3c4-...",
#       "qty": 1,                ← 50% of total
#       "target_price": 355.0,
#       "status": "pending"
#     }
#   },
#   "last_updated": "2026-09-17T07:35:00"
# }
#
# EXECUTION SEQUENCE:
# 1. Place both bracket entry orders simultaneously
# 2. Both entries fill at same price → position qty = 2
# 3. Detect both filled → place both bracket stops/targets
# 4. Monitor for stop/target execution independently
# 5. Track each bracket outcome separately in same JSON file
#
# TIME IN FORCE: All orders use GTC (Good-Till-Cancelled)
#
# ============================================================================
# POSITION TRACKING FUNCTIONS
# ============================================================================

def save_position_tracking(symbol: str, position_data: dict) -> None:
    """
    Save complete position tracking to single JSON file.
    
    Args:
        symbol: Trading symbol
        position_data: Dict with symbol, qty, entry_price, brackets, etc.
    """
    position_file = POSITIONS_DIR / f"{symbol}.json"
    
    position_data["last_updated"] = datetime.now().isoformat()
    
    try:
        with open(position_file, 'w') as f:
            json.dump(position_data, f, indent=2)
    except Exception as e:
        print(f"[ERROR] Could not save position tracking for {symbol}: {e}")


def load_position_tracking(symbol: str) -> dict | None:
    """
    Load complete position tracking from JSON file.
    
    Args:
        symbol: Trading symbol
    
    Returns:
        Position tracking dict or None if file doesn't exist
    """
    position_file = POSITIONS_DIR / f"{symbol}.json"
    
    if not position_file.exists():
        return None
    
    try:
        with open(position_file, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"[ERROR] Could not load position tracking for {symbol}: {e}")
        return None


def remove_position_tracking(symbol: str) -> bool:
    """
    Remove position tracking file.
    
    Args:
        symbol: Trading symbol
    
    Returns:
        True if file was removed
    """
    position_file = POSITIONS_DIR / f"{symbol}.json"
    
    try:
        if position_file.exists():
            position_file.unlink()
            return True
        return False
    except Exception as e:
        print(f"[ERROR] Could not remove position tracking for {symbol}: {e}")
        return False


def get_all_active_position_statuses() -> list:
    """
    Get list of all active positions being tracked.
    
    Returns:
        List of position tracking dicts
    """
    statuses = []
    try:
        for position_file in POSITIONS_DIR.glob("*.json"):
            try:
                with open(position_file, 'r') as f:
                    statuses.append(json.load(f))
            except Exception as e:
                print(f"[ERROR] Could not load {position_file.name}: {e}")
    except Exception as e:
        print(f"[ERROR] Could not scan positions directory: {e}")
    
    return statuses


# ============================================================================
# LEGACY API WRAPPERS (for backward compatibility during transition)
# These map old bracket1/bracket2 file API to new single-JSON format
# ============================================================================

def save_bracket_file(symbol: str, bracket_num: int, entry_order_id: str = None, 
                      stop_order_id: str = None, target_order_id: str = None,
                      entry_price: float = None, stop_price: float = None, 
                      target_price: float = None, qty: float = None,
                      filled: bool = False) -> None:
    """Legacy wrapper: saves to single JSON format"""
    pos_data = load_position_tracking(symbol)
    
    if not pos_data:
        pos_data = {
            "symbol": symbol,
            "qty": qty * 2 if qty else 0,
            "entry_price": entry_price,
            "entry_time": datetime.now().isoformat(),
            "brackets": {
                "bracket1": {"status": "pending"},
                "bracket2": {"status": "pending"}
            }
        }
    
    pos_data["brackets"][f"bracket{bracket_num}"] = {
        "entry_order_id": entry_order_id,
        "stop_order_id": stop_order_id,
        "target_order_id": target_order_id,
        "qty": qty,
        "target_price": target_price,
        "stop_price": stop_price,
        "status": "target_filled" if filled else "pending"
    }
    
    save_position_tracking(symbol, pos_data)


def load_bracket_file(symbol: str, bracket_num: int) -> dict | None:
    """Legacy wrapper: loads from single JSON format"""
    pos_data = load_position_tracking(symbol)
    
    if not pos_data or "brackets" not in pos_data:
        return None
    
    bracket = pos_data["brackets"].get(f"bracket{bracket_num}")
    if not bracket:
        return None
    
    return {
        "symbol": symbol,
        "bracket": bracket_num,
        "entry_order_id": bracket.get("entry_order_id"),
        "stop_order_id": bracket.get("stop_order_id"),
        "target_order_id": bracket.get("target_order_id"),
        "entry_price": pos_data.get("entry_price"),
        "stop_price": bracket.get("stop_price"),
        "target_price": bracket.get("target_price"),
        "qty": bracket.get("qty"),
        "filled": bracket.get("status") in ["target_filled", "stopped_out"],
    }


def remove_bracket_file(symbol: str, bracket_num: int) -> bool:
    """Legacy wrapper retained for compatibility; status updates preserve IDs."""
    return load_position_tracking(symbol) is not None


def remove_all_brackets_for_symbol(symbol: str) -> bool:
    """Legacy wrapper: removes all bracket data for symbol"""
    return remove_position_tracking(symbol)


def save_position_status(symbol: str, qty: float, entry_price: float, 
                        bracket1_status: str = "pending", bracket2_status: str = "pending",
                        current_price: float = None, current_pnl: float = None) -> None:
    """Legacy wrapper: updates status without deleting tracked order IDs."""
    pos_data = load_position_tracking(symbol) or {
        "symbol": symbol,
        "brackets": {"bracket1": {}, "bracket2": {}},
    }
    pos_data["qty"] = qty
    pos_data["entry_price"] = entry_price
    pos_data.setdefault("brackets", {}).setdefault("bracket1", {})["status"] = bracket1_status
    pos_data.setdefault("brackets", {}).setdefault("bracket2", {})["status"] = bracket2_status
    pos_data["current_price"] = current_price
    pos_data["current_pnl"] = current_pnl
    save_position_tracking(symbol, pos_data)


def load_position_status(symbol: str) -> dict | None:
    """Legacy wrapper: loads position status from JSON"""
    pos_data = load_position_tracking(symbol)
    
    if not pos_data:
        return None
    
    bracket1 = pos_data.get("brackets", {}).get("bracket1", {})
    bracket2 = pos_data.get("brackets", {}).get("bracket2", {})
    
    return {
        "symbol": symbol,
        "qty": pos_data.get("qty"),
        "entry_price": pos_data.get("entry_price"),
        "bracket1_status": bracket1.get("status", "pending"),
        "bracket2_status": bracket2.get("status", "pending"),
        "current_price": pos_data.get("current_price"),
        "current_pnl": pos_data.get("current_pnl"),
    }


def remove_position_status(symbol: str) -> bool:
    """Legacy wrapper: removes position tracking file"""
    return remove_position_tracking(symbol)




def calculate_adjusted_stop_loss(entry_price: float, original_stop_loss: float) -> float:
    """
    Calculate adjusted stop loss: 1/2 the distance between entry and original stop loss.
    
    Example: Entry=100, Original Stop=90, Distance=10 → Adjusted Stop=95
    
    Args:
        entry_price: Entry price
        original_stop_loss: Original stop loss price
    
    Returns:
        Adjusted stop loss price
    """
    distance = entry_price - original_stop_loss
    adjusted = entry_price - (distance / 2)
    return adjusted


def check_gap_open(symbol: str, entry_price: float, stop_loss: float, data_client=None) -> str | None:
    """
    Check if market opened with a gap relative to entry/stop.
    
    Returns:
        'gap_down' if opened below stop loss (exit with market order)
        'gap_up_above_entry' if opened above original entry (market order exit)
        None if no gap condition met
    """
    try:
        current_price = _get_current_price(symbol)
        
        # Gap down - opened below stop loss
        if current_price < stop_loss:
            return 'gap_down'
        
        # Gap up above entry
        if current_price > entry_price:
            return 'gap_up_above_entry'
        
        return None
    except Exception as e:
        print(f"[ERROR] Could not check gap open for {symbol}: {e}")
        return None


def is_trading_allowed(symbol: str, cfg: dict) -> bool:
    """
    Check if trading is allowed for a symbol based on time and asset type.
    
    Rules:
    - Stocks: Only trade between 6:30 AM and 12:40 PM ET
    - Crypto: Trade anytime (24/7)
    - Options: Only trade between 6:30 AM and 12:40 PM ET (like stocks)
    
    Returns:
        True if trading is allowed, False otherwise
    """
    current_time = datetime.now().time()
    trading_start = dt_time(6, 30)   # 6:30 AM
    trading_end = dt_time(12, 40)    # 12:40 PM
    
    # Crypto trades anytime
    if cfg.get("is_crypto", False):
        return True
    
    # Stocks and options: only during specified hours
    return trading_start <= current_time <= trading_end



def _install_http_timeout() -> None:
    original_request = requests.sessions.Session.request

    def _request_with_timeout(self, method, url, **kwargs):
        kwargs.setdefault("timeout", REQUEST_TIMEOUT_SECONDS)
        return original_request(self, method, url, **kwargs)

    requests.sessions.Session.request = _request_with_timeout


_install_http_timeout()

DEBUG_TIMING = os.getenv("FOMO_DEBUG_TIMING", "1").strip().lower() not in ("0", "false", "no")

# Extended hours and after-hours trading support
EXTENDED_HOURS_ENABLED = os.getenv("FOMO_EXTENDED_HOURS", "1").strip().lower() not in ("0", "false", "no")

try:
    credentials, trading_client = bootstrap_trading_auth("fomo_trade.py")
except Exception as exc:
    print(f"Authentication failed: {exc}")
    raise SystemExit(1)

PAPER = credentials.paper
OPTION_SYMBOL_PATTERN = re.compile(r"^[A-Z]{1,6}\d{6}[CP]\d{8}$")


def _is_option_symbol(symbol: str) -> bool:
    return bool(OPTION_SYMBOL_PATTERN.match(symbol.upper()))


def _normalize_symbol(symbol: str) -> str:
    return symbol.replace("/", "").replace("-", "").upper()


def _broker_symbol(trade_id_or_symbol: str) -> str:
    """Resolve a local tracking ID such as SPY_2 to its Alpaca ticker SPY."""
    trade = trade_states.get(trade_id_or_symbol)
    return trade["config"]["symbol"] if trade else trade_id_or_symbol


def _timed(label: str, fn, *args, **kwargs):
    started = time.perf_counter()
    result = fn(*args, **kwargs)
    elapsed = time.perf_counter() - started
    if DEBUG_TIMING and elapsed >= 0.5:
        print(f"[TIMING] {label}: {elapsed:.2f}s", flush=True)
    return result


def _create_limit_order(symbol: str, qty: float, side: OrderSide, limit_price: float, 
                       time_in_force: TimeInForce, is_stock: bool = False) -> LimitOrderRequest:
    """
    Create a limit order with extended hours support for stocks.
    
    Args:
        symbol: Trading symbol
        qty: Order quantity
        side: OrderSide.BUY or OrderSide.SELL
        limit_price: Limit price
        time_in_force: TimeInForce enum
        is_stock: Whether this is a stock (for extended hours support)
    
    Returns:
        LimitOrderRequest configured for extended hours if applicable
    """
    order_dict = {
        "symbol": _broker_symbol(symbol),
        "qty": qty,
        "side": side,
        "time_in_force": time_in_force,
        "limit_price": round(limit_price, 2),
    }
    
    # Add extended hours support for stocks
    if is_stock and EXTENDED_HOURS_ENABLED:
        order_dict["extended_hours"] = True
    
    return LimitOrderRequest(**order_dict)


def _create_stop_order(symbol: str, qty: float, side: OrderSide, stop_price: float, 
                       time_in_force: TimeInForce, is_stock: bool = False) -> StopOrderRequest:
    """
    Create a stop-loss order that triggers when price crosses the stop price.
    
    IMPORTANT: Stop orders are NOT eligible for extended hours trading.
    They can only be placed/active during regular market hours (9:30 AM - 4:00 PM ET).
    Entry orders can be pre-market with extended_hours=True, but stops are regular hours only.
    
    Args:
        symbol: Trading symbol
        qty: Order quantity
        side: OrderSide.BUY or OrderSide.SELL
        stop_price: Stop trigger price (order executes as market when price crosses this)
        time_in_force: TimeInForce enum
        is_stock: Whether this is a stock (for reference only - stops are never extended hours)
    
    Returns:
        StopOrderRequest configured for stop-loss execution (regular hours only)
    """
    order_dict = {
        "symbol": _broker_symbol(symbol),
        "qty": qty,
        "side": side,
        "time_in_force": time_in_force,
        "stop_price": round(stop_price, 2),
    }
    
    # NOTE: Stop orders are NEVER eligible for extended hours, even if EXTENDED_HOURS_ENABLED
    # They must only be placed during regular market hours (9:30 AM - 4:00 PM ET)
    # Do NOT set extended_hours for stop orders - Alpaca will reject them
    
    return StopOrderRequest(**order_dict)


def _submit_oco_exit(symbol: str, qty: float, stop_price: float, target_price: float,
                     time_in_force: TimeInForce) -> tuple[str, str]:
    """Submit a linked one-cancels-other sell exit and return target and stop IDs."""
    order = LimitOrderRequest(
        symbol=_broker_symbol(symbol),
        qty=qty,
        side=OrderSide.SELL,
        type="limit",
        time_in_force=time_in_force,
        limit_price=round(target_price, 2),
        order_class=OrderClass.OCO,
        take_profit=TakeProfitRequest(limit_price=round(target_price, 2)),
        stop_loss=StopLossRequest(stop_price=round(stop_price, 2)),
    )
    parent = trading_client.submit_order(order_data=order)
    target_id = str(parent.id)
    stop_id = next(
        (
            str(leg.id)
            for leg in (getattr(parent, "legs", None) or [])
            if getattr(leg, "stop_price", None) is not None
        ),
        None,
    )
    if stop_id is None:
        raise RuntimeError(f"OCO exit {parent.id} was created without a stop-loss leg")
    return target_id, stop_id


def _get_linked_exit_ids(entry_order_id: str) -> tuple[str | None, str | None]:
    """Return the target and stop leg IDs attached to an Alpaca bracket parent order."""
    order = trading_client.get_order_by_id(
        entry_order_id,
        filter=GetOrderByIdRequest(nested=True),
    )
    target_id = None
    stop_id = None
    for leg in getattr(order, "legs", None) or []:
        if getattr(leg, "stop_price", None) is not None:
            stop_id = str(leg.id)
        elif getattr(leg, "limit_price", None) is not None:
            target_id = str(leg.id)
    return target_id, stop_id


def _update_tracked_oco_exits(symbol: str, cfg: dict, state: dict) -> None:
    """Replace open OCO target and stop legs using their persisted Alpaca IDs."""
    for bracket_num in (1, 2):
        bracket = state[f"bracket{bracket_num}"]
        target_price = cfg["target1_price"] if bracket_num == 1 else cfg["target2_price"]
        target_id = bracket["target_order_id"]
        stop_id = bracket["stop_order_id"]
        if not target_id or not stop_id:
            continue
        target_order = trading_client.get_order_by_id(target_id)
        stop_order = trading_client.get_order_by_id(stop_id)
        target_status = str(target_order.status).lower()
        stop_status = str(stop_order.status).lower()
        target_is_live = any(status in target_status for status in ("new", "open", "accepted", "pending"))
        stop_is_live = any(status in stop_status for status in ("new", "open", "accepted", "pending", "held"))

        if not target_is_live and not stop_is_live:
            continue

        current_target = float(target_order.limit_price)
        current_stop = float(stop_order.stop_price)

        if target_is_live and abs(current_target - target_price) >= 0.005:
            target_response = trading_client.replace_order_by_id(
                target_id,
                ReplaceOrderRequest(limit_price=round(target_price, 2)),
            )
            bracket["target_order_id"] = str(target_response.id)
            print(f"[{symbol}] Updated B{bracket_num} OCO target: ${current_target:.2f} -> ${target_price:.2f}")

        if stop_is_live and abs(current_stop - cfg["stop_price"]) >= 0.005:
            stop_response = trading_client.replace_order_by_id(
                stop_id,
                ReplaceOrderRequest(stop_price=round(cfg["stop_price"], 2)),
            )
            bracket["stop_order_id"] = str(stop_response.id)
            print(f"[{symbol}] Updated B{bracket_num} OCO stop: ${current_stop:.2f} -> ${cfg['stop_price']:.2f}")

        save_bracket_file(
            symbol, bracket_num,
            entry_order_id=bracket["entry_order_id"],
            stop_order_id=bracket["stop_order_id"],
            target_order_id=bracket["target_order_id"],
            entry_price=cfg["entry_price"],
            stop_price=cfg["stop_price"],
            target_price=target_price,
            qty=cfg["bracket_qty"],
        )


def _ensure_oco_exits_for_trade(trade_id: str) -> None:
    """Create missing OCO exits for every individually filled tracked entry."""
    cfg = trade_states[trade_id]["config"]
    state = trade_states[trade_id]["state"]
    for bracket_num, bracket, target_price in (
        (1, state["bracket1"], cfg["target1_price"]),
        (2, state["bracket2"], cfg["target2_price"]),
    ):
        if not bracket["entry_order_id"]:
            continue
        entry_status = _check_order_status(bracket["entry_order_id"])
        if entry_status != "filled":
            continue
        if bracket["target_order_id"] and bracket["stop_order_id"]:
            continue

        missing = []
        if not bracket["stop_order_id"]:
            missing.append("stop")
        if not bracket["target_order_id"]:
            missing.append("target")
        print(f"[ALERT] [{trade_id}] B{bracket_num} entry is FILLED but missing {', '.join(missing)} tracking ID(s)")
        try:
            target_id, stop_id = _submit_oco_exit(
                trade_id, float(cfg["bracket_qty"]), cfg["stop_price"],
                target_price, cfg["order_time_in_force"]
            )
            bracket["target_order_id"] = target_id
            bracket["stop_order_id"] = stop_id
            save_bracket_file(
                trade_id, bracket_num,
                entry_order_id=bracket["entry_order_id"],
                stop_order_id=stop_id,
                target_order_id=target_id,
                entry_price=cfg["entry_price"],
                stop_price=cfg["stop_price"],
                target_price=target_price,
                qty=cfg["bracket_qty"],
            )
            print(f"[{trade_id}] ✓ B{bracket_num} OCO protection created: stop ${cfg['stop_price']:.2f} | target ${target_price:.2f}")
        except Exception as error:
            print(f"[{trade_id}] ✗ B{bracket_num} OCO protection error: {error}")


def _recover_missing_entry_ids_for_trade(trade_id: str) -> None:
    """Restore missing filled entry IDs from recent Alpaca buy order history."""
    cfg = trade_states[trade_id]["config"]
    state = trade_states[trade_id]["state"]
    if state["bracket1"].get("entry_order_id") and state["bracket2"].get("entry_order_id"):
        return

    used_entry_ids = {
        bracket.get("entry_order_id")
        for trade in trade_states.values()
        for bracket in trade["state"].values()
        if bracket.get("entry_order_id")
    }

    try:
        orders = trading_client.get_orders(
            filter=GetOrdersRequest(status=QueryOrderStatus.CLOSED, limit=200, symbols=[cfg["symbol"]])
        )
    except Exception as error:
        print(f"[{trade_id}] Could not recover missing entry IDs: {error}")
        return

    candidates = []
    for order in orders:
        order_id = str(getattr(order, "id", ""))
        side = getattr(order, "side", "")
        side_text = side.value if hasattr(side, "value") else str(side)
        status = str(getattr(order, "status", "")).lower()
        limit_price = getattr(order, "limit_price", None)
        qty = _as_float(getattr(order, "qty", None)) if "_as_float" in globals() else float(getattr(order, "qty", 0) or 0)
        if (not order_id or order_id in used_entry_ids or side_text.lower() != "buy" or
                "filled" not in status or limit_price in (None, "")):
            continue
        if abs(float(limit_price) - cfg["entry_price"]) < 0.005 and abs(qty - float(cfg["bracket_qty"])) < 1e-9:
            submitted_at = getattr(order, "submitted_at", None)
            candidates.append((submitted_at, order_id))

    candidates.sort(key=lambda item: item[0] or datetime.min)
    for bracket_num in (1, 2):
        bracket = state[f"bracket{bracket_num}"]
        if bracket.get("entry_order_id"):
            continue
        if not candidates:
            print(f"[ALERT] [{trade_id}] B{bracket_num} missing entry ID; no matching filled buy found")
            return
        _, recovered_id = candidates.pop(0)
        bracket["entry_order_id"] = recovered_id
        target_price = cfg["target1_price"] if bracket_num == 1 else cfg["target2_price"]
        save_bracket_file(
            trade_id, bracket_num,
            entry_order_id=recovered_id,
            stop_order_id=bracket.get("stop_order_id"),
            target_order_id=bracket.get("target_order_id"),
            entry_price=cfg["entry_price"],
            stop_price=cfg["stop_price"],
            target_price=target_price,
            qty=cfg["bracket_qty"],
        )
        print(f"[{trade_id}] Recovered B{bracket_num} entry ID: {recovered_id}")


def _find_position_for_symbol(symbol: str, retries: int = 3, delay_seconds: float = 1.0):
    broker_symbol = _broker_symbol(symbol)
    target = _normalize_symbol(broker_symbol)
    for attempt in range(1, retries + 1):
        try:
            # Fast path: ask Alpaca directly for this symbol instead of scanning all positions.
            pos = _timed("get_open_position", trading_client.get_open_position, broker_symbol)
            if _normalize_symbol(getattr(pos, "symbol", "")) == target:
                return pos
        except Exception:
            # Fallback: broader scan only when direct lookup fails.
            try:
                positions = _timed("get_all_positions", trading_client.get_all_positions)
            except Exception:
                positions = []
            for pos in positions:
                if _normalize_symbol(getattr(pos, "symbol", "")) == target:
                    return pos
        if attempt < retries:
            time.sleep(delay_seconds)
    return None


def _get_open_orders_for_symbol(symbol: str):
    target = _normalize_symbol(_broker_symbol(symbol))
    orders = list(
        _timed(
            "get_orders(open)",
            trading_client.get_orders,
            filter=GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=100)
        )
    )
    return [o for o in orders if _normalize_symbol(getattr(o, "symbol", "")) == target]


def _reset_trade_order_tracking(trade_id: str) -> None:
    """Clear in-memory order IDs after a trade has fully exited."""
    state = trade_states[trade_id]["state"]
    for bracket_num in (1, 2):
        state[f"bracket{bracket_num}"] = {
            "entry_order_id": None,
            "stop_order_id": None,
            "target_order_id": None,
            "filled": False,
        }


def _reconcile_trade_tracker(trade_id: str) -> bool:
    """Remove a tracker only after no position or live buy entry remains."""
    cfg = trade_states[trade_id]["config"]
    state = trade_states[trade_id]["state"]

    if _find_position_for_symbol(trade_id, retries=1, delay_seconds=0.0):
        return False

    try:
        open_orders = _get_open_orders_for_symbol(trade_id)
    except Exception as error:
        print(f"[RECONCILE] [{trade_id}] Could not inspect open orders: {error}")
        return False

    # A live buy may still be waiting to fill. Keep its tracker so the next
    # cycle does not submit a duplicate entry.
    live_buy_ids = {
        str(getattr(order, "id", ""))
        for order in open_orders
        if str(getattr(order, "side", "")).lower().endswith("buy")
    }
    tracked_entry_ids = {
        state[f"bracket{bracket_num}"].get("entry_order_id")
        for bracket_num in (1, 2)
    }
    if live_buy_ids & tracked_entry_ids or live_buy_ids:
        return False

    # No position and no live buy means any remaining tracked exits are stale.
    # Cancel them before deleting the file so an old OCO cannot sell a future
    # position opened by the same configured trade.
    open_order_ids = {str(getattr(order, "id", "")) for order in open_orders}
    for bracket_num in (1, 2):
        bracket = state[f"bracket{bracket_num}"]
        for order_id in (bracket.get("stop_order_id"), bracket.get("target_order_id")):
            if order_id and order_id in open_order_ids:
                try:
                    trading_client.cancel_order_by_id(order_id)
                    print(f"[RECONCILE] [{trade_id}] Cancelled stale exit {order_id}")
                except Exception as error:
                    print(f"[RECONCILE] [{trade_id}] Could not cancel stale exit {order_id}: {error}")

    if remove_position_tracking(trade_id):
        _reset_trade_order_tracking(trade_id)
        print(f"[RECONCILE] [{trade_id}] No broker position remains; removed completed tracker")
        return True
    return False


def _check_order_status(order_id: str) -> str | None:
    """
    Check if a specific order was filled, cancelled, or is still open.
    
    Args:
        order_id: The Alpaca order ID to check
    
    Returns:
        "filled" if order was filled
        "cancelled" if order was cancelled
        "open" if order is still open
        None if order not found or error
    """
    try:
        order = trading_client.get_order_by_id(order_id)
        if hasattr(order, 'status'):
            status = str(order.status).lower()
            if 'filled' in status:
                return 'filled'
            elif 'cancelled' in status or 'cancel' in status:
                return 'cancelled'
            elif 'open' in status or 'pending' in status:
                return 'open'
        return None
    except Exception:
        return None


def _get_bracket_outcome(symbol: str, bracket_num: int, cfg: dict, state: dict) -> dict | None:
    """
    Determine what happened to a bracket: target filled, stopped out, or still pending.
    Returns dict with bracket outcome details.
    
    Args:
        symbol: Trading symbol
        bracket_num: 1 or 2
        cfg: Config dict
        state: State dict
    
    Returns:
        {
            "status": "target_filled" | "stopped_out" | "pending" | "unknown",
            "target_price": float (if target_filled),
            "profit_loss": "profit" | "loss",
            "exit_price": float (approx),
        }
        or None if cannot determine
    """
    bracket = state[f"bracket{bracket_num}"]
    target_order_id = bracket["target_order_id"]
    stop_order_id = bracket["stop_order_id"]
    
    if not target_order_id or not stop_order_id:
        return None
    
    # Check target order status
    target_status = _check_order_status(target_order_id)
    stop_status = _check_order_status(stop_order_id)
    
    target_price = cfg["target1_price"] if bracket_num == 1 else cfg["target2_price"]
    entry_price = cfg["entry_price"]
    stop_price = cfg["stop_price"]
    
    # Determine outcome
    if target_status == "filled":
        return {
            "status": "target_filled",
            "bracket": bracket_num,
            "target_price": target_price,
            "profit_loss": "profit",
            "exit_price": target_price,
        }
    elif stop_status == "filled":
        return {
            "status": "stopped_out",
            "bracket": bracket_num,
            "stop_price": stop_price,
            "profit_loss": "loss",
            "exit_price": stop_price,
        }
    elif target_status == "open" or stop_status == "open":
        return {
            "status": "pending",
            "bracket": bracket_num,
            "target_price": target_price,
            "stop_price": stop_price,
        }
    else:
        return {
            "status": "unknown",
            "bracket": bracket_num,
        }


def _get_current_price(symbol: str) -> float:
    symbol = _broker_symbol(symbol)
    is_crypto = "/" in symbol
    is_option = _is_option_symbol(symbol)
    
    if is_crypto:
        price_request = CryptoLatestTradeRequest(symbol_or_symbols=symbol)
        latest_trade = _timed("get_crypto_latest_trade", data_client.get_crypto_latest_trade, price_request)
    elif is_option:
        price_request = OptionLatestTradeRequest(symbol_or_symbols=symbol, feed=OptionsFeed.INDICATIVE)
        latest_trade = _timed("get_option_latest_trade", data_client.get_option_latest_trade, price_request)
    else:
        price_request = StockLatestTradeRequest(symbol_or_symbols=symbol, feed=DataFeed.IEX)
        latest_trade = _timed("get_stock_latest_trade", data_client.get_stock_latest_trade, price_request)
    return float(latest_trade[symbol].price)


def _normalize_position_qty(raw_qty: float) -> float | int:
    """Normalize position quantity to absolute value, keeping decimals for crypto"""
    # Note: Cannot determine if crypto from quantity alone - that info is in the symbol
    # Crypto positions can have decimals, stocks are integers
    # Since we get here after fetching position, just return the absolute value as-is
    qty_value = abs(float(raw_qty))
    
    # Return as float to preserve decimals for crypto positions
    return qty_value


def _read_config_from_file() -> list[tuple]:
    """Read ALL active (non-comment) lines from lists/fomo_trade.txt"""
    config_path = WORKSPACE_ROOT / "lists" / "fomo_trade.txt"
    
    if not config_path.exists():
        print(f"Error: Configuration file not found at {config_path}")
        print("Please create lists/fomo_trade.txt with content like:")
        print("MU 2 780 770 800 900")
        print("INTC 2 55 50 60 70")
        sys.exit(1)
    
    configs = []
    try:
        with open(config_path, 'r') as f:
            for line in f:
                line = line.strip()
                # Skip empty lines and comments
                if not line or line.startswith('#'):
                    continue
                
                parts = line.split()
                if len(parts) < 6:
                    print(f"Warning: Skipping invalid configuration line: {line}")
                    print("Expected format: SYMBOL NUM_STOCKS ENTRY_PRICE STOP_PRICE TARGET1_PRICE TARGET2_PRICE")
                    continue
                
                symbol = parts[0].upper()
                try:
                    num_stocks = int(parts[1])
                    entry_price = float(parts[2])
                    stop_price = float(parts[3])
                    target1_price = float(parts[4])
                    target2_price = float(parts[5])
                except ValueError as e:
                    print(f"Warning: Skipping invalid value in configuration line: {line}")
                    print(f"Details: {e}")
                    continue
                
                configs.append((symbol, num_stocks, entry_price, stop_price, target1_price, target2_price))
        
        if not configs:
            print("Error: No valid configuration lines found in lists/fomo_trade.txt")
            sys.exit(1)

        symbol_counts = {}
        for symbol, *_ in configs:
            symbol_counts[symbol] = symbol_counts.get(symbol, 0) + 1

        symbol_indexes = {}
        identified_configs = []
        for symbol, num_stocks, entry_price, stop_price, target1_price, target2_price in configs:
            symbol_indexes[symbol] = symbol_indexes.get(symbol, 0) + 1
            trade_id = symbol if symbol_counts[symbol] == 1 else f"{symbol}_{symbol_indexes[symbol]}"
            identified_configs.append((trade_id, symbol, num_stocks, entry_price, stop_price, target1_price, target2_price))

        return identified_configs
    except Exception as e:
        print(f"Error reading configuration file: {e}")
        sys.exit(1)


# Fixed flags
REFRESH_OPTION_MIDPOINT = False
SINGLE_ENTRY_MODE = False
CHECK_INTERVAL = 10  # Check market AND config every 10 seconds (unified)

# Dynamic trade state management
trade_states = {}  # {symbol: {config, state_data}}
previous_configs = {}  # Track previous config to detect changes


def _validate_and_add_trade(trade_id: str, symbol: str, num_stocks: int, entry_price: float, stop_price: float,
                            target1_price: float, target2_price: float, data_client=None) -> bool:
    """Validate trade config and add to trade_states. Returns True if added successfully."""
    if trade_id in trade_states:
        return False  # Already exists
    
    if num_stocks <= 0:
        print(f"[CONFIG] ✗ {symbol}: NUM_STOCKS must be positive")
        return False
    
    if num_stocks % 2 != 0:
        print(f"[CONFIG] ✗ {symbol}: NUM_STOCKS must be even (got {num_stocks})")
        return False
    
    if stop_price >= entry_price:
        print(f"[CONFIG] ✗ {symbol}: STOP must be < ENTRY")
        return False
    
    if target1_price <= entry_price or target2_price <= target1_price:
        print(f"[CONFIG] ✗ {symbol}: Must have ENTRY < TARGET1 < TARGET2")
        return False
    
    is_crypto = "/" in symbol
    is_option = _is_option_symbol(symbol)
    
    # Always use GTC (Good-Till-Cancelled) for all orders
    order_time_in_force = TimeInForce.GTC
    
    bracket_qty = num_stocks // 2
    
    trade_states[trade_id] = {
        "config": {
            "symbol": symbol,
            "trade_id": trade_id,
            "is_duplicate_ticker": trade_id != symbol,
            "num_stocks": num_stocks,
            "entry_price": entry_price,
            "stop_price": stop_price,
            "target1_price": target1_price,
            "target2_price": target2_price,
            "is_crypto": is_crypto,
            "is_option": is_option,
            "order_time_in_force": order_time_in_force,
            "bracket_qty": bracket_qty,
        },
        "state": {
            # Bracket 1 (50% qty with Target 1)
            "bracket1": {
                "entry_order_id": None,
                "stop_order_id": None,
                "target_order_id": None,
                "filled": False,
            },
            # Bracket 2 (50% qty with Target 2)
            "bracket2": {
                "entry_order_id": None,
                "stop_order_id": None,
                "target_order_id": None,
                "filled": False,
            },
        }
    }
    return True


def _restore_trade_tracking(trade_id: str) -> None:
    """Load persisted entry and OCO IDs into one trade's in-memory state."""
    state = trade_states[trade_id]["state"]
    for bracket_num in (1, 2):
        bracket_data = load_bracket_file(trade_id, bracket_num)
        if not bracket_data:
            continue
        bracket = state[f"bracket{bracket_num}"]
        bracket["entry_order_id"] = bracket_data.get("entry_order_id")
        bracket["stop_order_id"] = bracket_data.get("stop_order_id")
        bracket["target_order_id"] = bracket_data.get("target_order_id")
        bracket["filled"] = bracket_data.get("filled", False)
        print(
            f"[STARTUP] [{trade_id}] Restored B{bracket_num}: "
            f"entry={bracket['entry_order_id']}, stop={bracket['stop_order_id']}, "
            f"target={bracket['target_order_id']}"
        )


def _migrate_single_tracker_for_duplicates(configs: list[tuple]) -> None:
    """Rename SYMBOL.json to SYMBOL_1.json when that ticker gains a second config line."""
    for trade_id, symbol, *_ in configs:
        if trade_id != f"{symbol}_1":
            continue
        legacy_file = POSITIONS_DIR / f"{symbol}.json"
        duplicate_file = POSITIONS_DIR / f"{trade_id}.json"
        if legacy_file.exists() and not duplicate_file.exists():
            legacy_file.rename(duplicate_file)
            print(f"[CONFIG] Migrated {legacy_file.name} to {duplicate_file.name}")


def _remove_trade(symbol: str):
    """Remove a trade from active tracking (it will finish its current orders)."""
    if symbol in trade_states:
        del trade_states[symbol]
        print(f"[CONFIG] ⚠ Removed {symbol} from tracking")
        return True
    return False


def _place_entry_orders_for_symbols(symbols_to_place):
    """
    Place two half-size limit entry orders at the configured entry price.

    Their OCO exit pairs are submitted after both entries fill.
    """
    for symbol in symbols_to_place:
        if symbol not in trade_states:
            continue
        
        cfg = trade_states[symbol]["config"]
        state = trade_states[symbol]["state"]
        
        try:
            current_price = _get_current_price(symbol)
            print(f"[{symbol}] Current: ${current_price:.2f} | Entry: ${cfg['entry_price']:.2f}")
            
            # Check for existing position
            existing_pos = _find_position_for_symbol(symbol, retries=1, delay_seconds=0.0)
            if existing_pos and not cfg["is_duplicate_ticker"]:
                print(f"[{symbol}] ✓ Existing position: {existing_pos.qty} shares\n")
                continue
            if existing_pos:
                print(f"[{symbol}] Existing {cfg['symbol']} position belongs to another tracked trade; placing separate entries")
            
            # ====== ENTRY 1 ======
            bracket1_placed = False
            b1_target_id = state["bracket1"].get("target_order_id")
            if b1_target_id and _check_order_status(b1_target_id) == "filled":
                print(f"[{symbol}] ⊘ Bracket 1 target already filled; skipping entry")
            else:
                try:
                    entry1_order = _create_limit_order(
                        symbol, float(cfg["bracket_qty"]), OrderSide.BUY, cfg["entry_price"],
                        cfg["order_time_in_force"], not cfg["is_crypto"] and not cfg["is_option"]
                    )
                    entry1_id = str(trading_client.submit_order(order_data=entry1_order).id)
                    if entry1_id:
                        state["bracket1"]["entry_order_id"] = entry1_id
                        bracket1_placed = True
                        print(f"[{symbol}] ✓ Bracket 1: BUY {cfg['bracket_qty']} @ ${cfg['entry_price']:.2f} (ID: {entry1_id})")
                    else:
                        print(f"[{symbol}] ✗ Bracket 1: No response ID")
                except Exception as e:
                    print(f"[{symbol}] ✗ Bracket 1 ERROR: {e}")

            # ====== ENTRY 2 ======
            bracket2_placed = False
            b2_target_id = state["bracket2"].get("target_order_id")
            if b2_target_id and _check_order_status(b2_target_id) == "filled":
                print(f"[{symbol}] ⊘ Bracket 2 target already filled; skipping entry")
            else:
                try:
                    entry2_order = _create_limit_order(
                        symbol, float(cfg["bracket_qty"]), OrderSide.BUY, cfg["entry_price"],
                        cfg["order_time_in_force"], not cfg["is_crypto"] and not cfg["is_option"]
                    )
                    entry2_id = str(trading_client.submit_order(order_data=entry2_order).id)
                    if entry2_id:
                        state["bracket2"]["entry_order_id"] = entry2_id
                        bracket2_placed = True
                        print(f"[{symbol}] ✓ Bracket 2: BUY {cfg['bracket_qty']} @ ${cfg['entry_price']:.2f} (ID: {entry2_id})")
                    else:
                        print(f"[{symbol}] ✗ Bracket 2: No response ID")
                except Exception as e:
                    print(f"[{symbol}] ✗ Bracket 2 ERROR: {e}")
            
            # ====== SAVE TRACKING ======
            if bracket1_placed:
                save_bracket_file(
                    symbol, 1,
                    entry_order_id=state["bracket1"]["entry_order_id"],
                    stop_order_id=None,
                    target_order_id=None,
                    entry_price=cfg["entry_price"],
                    stop_price=cfg["stop_price"],
                    target_price=cfg["target1_price"],
                    qty=cfg["bracket_qty"]
                )
            if bracket2_placed:
                save_bracket_file(
                    symbol, 2,
                    entry_order_id=state["bracket2"]["entry_order_id"],
                    stop_order_id=None,
                    target_order_id=None,
                    entry_price=cfg["entry_price"],
                    stop_price=cfg["stop_price"],
                    target_price=cfg["target2_price"],
                    qty=cfg["bracket_qty"]
                )
            if bracket1_placed and bracket2_placed:
                print(f"[{symbol}] ✓ Both native bracket entries placed.\n")
                
        except Exception as e:
            print(f"[{symbol}] ✗ Critical error in placement: {e}\n")


# ============================================================================
# STARTUP: Scan existing positions and open orders
# Reconcile with fomo_trade.txt and update trade_states
# ============================================================================

print("\n[STARTUP] Reconciling with existing positions and orders...\n")

# Get all open orders
try:
    all_orders = list(trading_client.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=500)))
except Exception as e:
    print(f"[STARTUP] ⚠ Could not fetch open orders: {e}")
    all_orders = []

# Get all positions
try:
    all_positions = _timed("get_all_positions", trading_client.get_all_positions)
except Exception:
    all_positions = []

# Process each trade in trade_states - load existing bracket files if they exist
for symbol in list(trade_states.keys()):
    cfg = trade_states[symbol]["config"]
    state = trade_states[symbol]["state"]
    
    # Try to load existing bracket files
    bracket1_data = load_bracket_file(symbol, 1)
    bracket2_data = load_bracket_file(symbol, 2)
    
    if bracket1_data:
        state["bracket1"]["entry_order_id"] = bracket1_data.get("entry_order_id")
        state["bracket1"]["stop_order_id"] = bracket1_data.get("stop_order_id")
        state["bracket1"]["target_order_id"] = bracket1_data.get("target_order_id")
        state["bracket1"]["filled"] = bracket1_data.get("filled", False)
        print(f"[STARTUP] [{symbol}] Loaded Bracket 1 tracking (Entry ID: {bracket1_data.get('entry_order_id')})")
    
    if bracket2_data:
        state["bracket2"]["entry_order_id"] = bracket2_data.get("entry_order_id")
        state["bracket2"]["stop_order_id"] = bracket2_data.get("stop_order_id")
        state["bracket2"]["target_order_id"] = bracket2_data.get("target_order_id")
        state["bracket2"]["filled"] = bracket2_data.get("filled", False)
        print(f"[STARTUP] [{symbol}] Loaded Bracket 2 tracking (Entry ID: {bracket2_data.get('entry_order_id')})")
    
    # Check for existing position
    existing_pos = _find_position_for_symbol(symbol, retries=1, delay_seconds=0.0)
    if existing_pos:
        pos_qty = _normalize_position_qty(float(existing_pos.qty))
        print(f"[STARTUP] [{symbol}] Found position: {pos_qty} shares @ ${existing_pos.avg_entry_price:.2f}")
    elif not bracket1_data and not bracket2_data:
        print(f"[STARTUP] [{symbol}] No position or tracker - will place entry orders")

print()


# Print header
print(f"\n{'╔' + '═'*60 + '╗'}")
print(f"║ {' '*60} ║")
print(f"║ {'FOMO TRADE - MULTI-SYMBOL SIMULTANEOUS TRADING':<60} ║")
print(f"║ {'(Dynamic Config - Hot Reload Enabled)':<60} ║")
if EXTENDED_HOURS_ENABLED:
    print(f"║ {'✓ Extended/After-Hours Trading ENABLED':<60} ║")
else:
    print(f"║ {'Regular Market Hours Only':<60} ║")
print(f"║ {' '*60} ║")
print(f"║ {'📅 TRADING HOURS:':<60} ║")
print(f"║ {'  • Stocks/Options: 6:30 AM - 12:40 PM ET':<60} ║")
print(f"║ {'  • Crypto: 24/7 (anytime)':<60} ║")
print(f"║ {' '*60} ║")
print(f"╚" + "═"*60 + "╝\n")

# Read initial configuration
initial_configs = _read_config_from_file()
_migrate_single_tracker_for_duplicates(initial_configs)
for trade_id, symbol, num_stocks, entry_price, stop_price, target1_price, target2_price in initial_configs:
    _validate_and_add_trade(trade_id, symbol, num_stocks, entry_price, stop_price, target1_price, target2_price)
    previous_configs[trade_id] = (symbol, num_stocks, entry_price, stop_price, target1_price, target2_price)

if not trade_states:
    print("Error: No valid trades loaded")
    sys.exit(1)

# Restore persistent JSON tracking after the configured symbols exist in memory.
# This must happen before stale-order cleanup so active OCO and entry IDs are preserved.
for trade_id in trade_states:
    _restore_trade_tracking(trade_id)

# Remove completed trackers before collecting IDs to preserve.
for trade_id in list(trade_states):
    _reconcile_trade_tracker(trade_id)

print(f"[INFO] Loaded {len(trade_states)} initial trade configuration(s):\n")
for symbol, trade_data in trade_states.items():
    cfg = trade_data["config"]
    print(f"  {symbol}:")
    print(f"    Entry: ${cfg['entry_price']:.2f} | Shares: {cfg['num_stocks']} | Stop: ${cfg['stop_price']:.2f}")
    print(f"    Target1: ${cfg['target1_price']:.2f} | Target2: ${cfg['target2_price']:.2f}\n")

# ========== CANCEL ONLY DUPLICATE/STALE ORDERS (Preserve tracked entry/OCO orders) ==========
print(f"[INFO] Cleaning up duplicate/stale orders (preserving tracked entry/OCO orders)...\n")

# Collect all order IDs we're actively tracking from bracket files
tracked_order_ids = set()
for symbol in trade_states:
    state = trade_states[symbol]["state"]
    for bracket_num in [1, 2]:
        bracket = state[f"bracket{bracket_num}"]
        if bracket["entry_order_id"]:
            tracked_order_ids.add(bracket["entry_order_id"])
        if bracket["stop_order_id"]:
            tracked_order_ids.add(bracket["stop_order_id"])
        if bracket["target_order_id"]:
            tracked_order_ids.add(bracket["target_order_id"])

try:
    all_orders = list(trading_client.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=500)))
    config_symbols = {trade_data["config"]["symbol"] for trade_data in trade_states.values()}
    
    cancelled_count = 0
    for order in all_orders:
        # Only cancel if: symbol is in config AND order is NOT being tracked
        if order.symbol in config_symbols and str(order.id) not in tracked_order_ids:
            try:
                trading_client.cancel_order_by_id(order.id)
                print(f"  ✓ Cancelled duplicate order {order.id} ({order.symbol} {order.side} {order.qty} @ ${order.limit_price if hasattr(order, 'limit_price') else 'mkt'})")
                cancelled_count += 1
            except Exception as e:
                print(f"  ⚠ Could not cancel {order.id}: {e}")
    
    if tracked_order_ids:
        print(f"[INFO] ✓ Preserved {len(tracked_order_ids)} tracked entry/OCO orders\n")
    
    if cancelled_count > 0:
        print(f"[INFO] Cancelled {cancelled_count} duplicate/stale orders\n")
    else:
        if not tracked_order_ids:
            print(f"[INFO] No existing orders to cancel\n")
        else:
            print(f"[INFO] No duplicate orders to cancel (all are tracked)\n")
except Exception as e:
    print(f"[INFO] ⚠ Could not fetch/cancel orders: {e}\n")

print(f"[INFO] Placing new 50/50 entry orders...\n")

# Create data client
if all(trade_states[s]["config"]["is_crypto"] for s in trade_states):
    data_client = CryptoHistoricalDataClient(credentials.api_key, credentials.secret_key)
elif all(trade_states[s]["config"]["is_option"] for s in trade_states):
    data_client = OptionHistoricalDataClient(credentials.api_key, credentials.secret_key)
else:
    data_client = StockHistoricalDataClient(credentials.api_key, credentials.secret_key)

# Apply the current fomo_trade.txt stop/target prices to restored live OCO legs.
# This covers edits made while the monitor was not running.
for symbol, trade_data in trade_states.items():
    if not _find_position_for_symbol(symbol, retries=1, delay_seconds=0.0):
        continue
    try:
        _update_tracked_oco_exits(symbol, trade_data["config"], trade_data["state"])
    except Exception as e:
        print(f"[STARTUP] [{symbol}] Could not reconcile tracked OCO orders: {e}")

for trade_id in trade_states:
    _recover_missing_entry_ids_for_trade(trade_id)
    _ensure_oco_exits_for_trade(trade_id)

# Determine which symbols need entry orders placed (not already placed or filled)
symbols_needing_entry_orders = []
for symbol in trade_states:
    state = trade_states[symbol]["state"]
    cfg = trade_states[symbol]["config"]
    pos = _find_position_for_symbol(symbol, retries=1, delay_seconds=0.0)
    
    # Check if we already have tracked entry orders
    bracket1_entry = state["bracket1"]["entry_order_id"]
    bracket2_entry = state["bracket2"]["entry_order_id"]
    
    if bracket1_entry or bracket2_entry:
        # Entry orders already tracked from previous run
        print(f"[STARTUP] [{symbol}] Skipping entry (tracked order IDs restored from previous run)\n")
    elif not pos:  # No position - need entry order
        existing_entry_orders = _get_open_orders_for_symbol(symbol)
        existing_entry_orders = [o for o in existing_entry_orders if o.side == OrderSide.BUY]
        
        if not existing_entry_orders:
            # No entry order either - need to place one
            symbols_needing_entry_orders.append(symbol)
        else:
            # Entry order exists, just waiting for fill
            print(f"[STARTUP] [{symbol}] Skipping entry (order already placed, waiting for fill)\n")
    elif cfg["is_duplicate_ticker"]:
        # Alpaca aggregates by ticker. This separately tracked duplicate is an
        # explicit new trade, so it needs its own two entry orders.
        symbols_needing_entry_orders.append(symbol)
        print(f"[STARTUP] [{symbol}] Existing {cfg['symbol']} position belongs to another tracked trade; placing this trade's entries\n")
    else:
        print(f"[STARTUP] [{symbol}] Skipping entry (position already filled)\n")

# SETUP PHASE: Place only MISSING entry orders
if symbols_needing_entry_orders:
    print(f"[INFO] Placing {len(symbols_needing_entry_orders)} missing entry orders...\n")
    _place_entry_orders_for_symbols(symbols_needing_entry_orders)
else:
    print(f"[INFO] All entry orders already in place or filled\n")

print(f"[INFO] Monitoring started (unified 10s check for market & config)...\n")
print(f"[INFO] Position trackers: positions/TICKER.json (entry and OCO IDs)\n")

try:
    # ========== MONITORING LOOP ==========
    while True:
        current_time = datetime.now()
        
        # ========== UNIFIED 10-SECOND CHECK: MARKET + CONFIG ==========
        print(f"\n[{current_time.strftime('%H:%M:%S')}] 🔍 Market & Config Check ({len(trade_states)} symbols)...")
        
        # CONFIG RELOAD CHECK (every 10 seconds)
        try:
            current_configs = _read_config_from_file()
            _migrate_single_tracker_for_duplicates(current_configs)
            current_symbols = {cfg[0] for cfg in current_configs}
            previous_symbols = set(previous_configs.keys())
            
            # Find NEW trades
            new_symbols = current_symbols - previous_symbols
            if new_symbols:
                print(f"\n[CONFIG] 🆕 Found {len(new_symbols)} new trade(s):")
                for trade_id, symbol, num_stocks, entry_price, stop_price, target1_price, target2_price in current_configs:
                    if trade_id in new_symbols:
                        if _validate_and_add_trade(trade_id, symbol, num_stocks, entry_price, stop_price, target1_price, target2_price):
                            _restore_trade_tracking(trade_id)
                            previous_configs[trade_id] = (symbol, num_stocks, entry_price, stop_price, target1_price, target2_price)
                            cfg = trade_states[trade_id]["config"]
                            print(f"  ✓ {trade_id} ({symbol}): Entry ${cfg['entry_price']:.2f} | Stop ${cfg['stop_price']:.2f}")
                
                # Place entries only for new trades that did not restore IDs.
                new_trade_ids_to_place = [
                    trade_id for trade_id in new_symbols
                    if not trade_states[trade_id]["state"]["bracket1"]["entry_order_id"]
                    and not trade_states[trade_id]["state"]["bracket2"]["entry_order_id"]
                ]
                _place_entry_orders_for_symbols(new_trade_ids_to_place)
                print()
            
            # Find REMOVED trades
            removed_symbols = previous_symbols - current_symbols
            if removed_symbols:
                print(f"\n[CONFIG] ❌ Removed {len(removed_symbols)} trade(s):")
                for symbol in removed_symbols:
                    _remove_trade(symbol)
                    del previous_configs[symbol]
                print()
            
            # Find UPDATED trades (same symbol, but config changed)
            common_symbols = current_symbols & previous_symbols
            updated_symbols = []
            
            for trade_id, symbol, num_stocks, entry_price, stop_price, target1_price, target2_price in current_configs:
                if trade_id in common_symbols:
                    prev_config = previous_configs[trade_id]
                    # Compare all parameters
                    if (symbol, num_stocks, entry_price, stop_price, target1_price, target2_price) != prev_config:
                        updated_symbols.append((trade_id, symbol, num_stocks, entry_price, stop_price, target1_price, target2_price))
            
            if updated_symbols:
                print(f"\n[CONFIG] 🔄 Found {len(updated_symbols)} updated trade(s):")
                for trade_id, symbol, num_stocks, entry_price, stop_price, target1_price, target2_price in updated_symbols:
                    cfg = trade_states[trade_id]["config"]
                    state = trade_states[trade_id]["state"]

                    if (num_stocks <= 0 or num_stocks % 2 != 0 or
                            stop_price >= entry_price or
                            target1_price <= entry_price or
                            target2_price <= target1_price):
                        print(
                            f"  ✗ {trade_id}: Invalid update skipped. Require even quantity, "
                            "STOP < ENTRY < TARGET1 < TARGET2."
                        )
                        previous_configs[trade_id] = (
                            symbol, num_stocks, entry_price, stop_price,
                            target1_price, target2_price
                        )
                        continue
                    
                    # Check if position already filled
                    pos = _find_position_for_symbol(trade_id, retries=1, delay_seconds=0.0)
                    
                    if pos:
                        if (num_stocks, entry_price) != prev_config[1:3]:
                            print(f"  ⚠ {trade_id}: Filled position keeps its original quantity and entry price")
                        cfg["stop_price"] = stop_price
                        cfg["target1_price"] = target1_price
                        cfg["target2_price"] = target2_price
                        try:
                            _update_tracked_oco_exits(trade_id, cfg, state)
                            print(f"  ✓ {trade_id}: Updated tracked OCO stops/targets by trading ID")
                        except Exception as e:
                            print(f"  ✗ {trade_id}: Could not update tracked OCO orders: {e}")
                    else:
                        # No position yet - check if bracket entry orders exist and cancel them
                        cancelled_count = 0
                        cancellation_failed = False
                        for bracket_num in [1, 2]:
                            entry_id = state[f"bracket{bracket_num}"]["entry_order_id"]
                            if entry_id:
                                try:
                                    trading_client.cancel_order_by_id(entry_id)
                                    print(f"  ✓ {trade_id}: Cancelled entry {bracket_num} @ ${cfg['entry_price']:.2f}")
                                    cancelled_count += 1
                                except Exception as e:
                                    cancellation_failed = True
                                    print(f"  ✗ {trade_id}: Error cancelling entry {bracket_num}: {e}")

                        if cancellation_failed:
                            print(f"  ⚠ {trade_id}: Keeping existing tracking; no replacement entries placed")
                            previous_configs[trade_id] = (symbol, num_stocks, entry_price, stop_price, target1_price, target2_price)
                            continue
                        
                        # Update config with new values
                        bracket_qty = num_stocks // 2
                        cfg["num_stocks"] = num_stocks
                        cfg["entry_price"] = entry_price
                        cfg["stop_price"] = stop_price
                        cfg["target1_price"] = target1_price
                        cfg["target2_price"] = target2_price
                        cfg["bracket_qty"] = bracket_qty
                        
                        # Reset bracket states for new orders
                        state["bracket1"] = {
                            "entry_order_id": None,
                            "stop_order_id": None,
                            "target_order_id": None,
                            "filled": False,
                        }
                        state["bracket2"] = {
                            "entry_order_id": None,
                            "stop_order_id": None,
                            "target_order_id": None,
                            "filled": False,
                        }
                        
                        # Place new bracket entry orders
                        try:
                            _place_entry_orders_for_symbols([trade_id])
                            print(f"  ✓ {trade_id}: Placed new entry orders @ ${entry_price:.2f} (was ${prev_config[2]:.2f})")
                        except Exception as e:
                            print(f"  ✗ {trade_id}: Error placing new entry orders: {e}")
                    
                    # Update previous config tracking
                    previous_configs[trade_id] = (symbol, num_stocks, entry_price, stop_price, target1_price, target2_price)
                
                print()
        except Exception as e:
            pass  # Ignore config read errors, will retry next cycle
        
        # ========== MONITOR ALL ACTIVE TRADES ==========
        # ========== MARKET MONITORING: TRACK BRACKET ORDERS ==========
        for trade_id in list(trade_states):
            _reconcile_trade_tracker(trade_id)
        
        # STEP 1: Ensure both bracket entries are placed for all symbols
        for symbol in list(trade_states.keys()):
            cfg = trade_states[symbol]["config"]
            state = trade_states[symbol]["state"]
            
            b1_entry_id = state["bracket1"]["entry_order_id"]
            b2_entry_id = state["bracket2"]["entry_order_id"]

            if not b1_entry_id and not b2_entry_id:
                if not _find_position_for_symbol(symbol, retries=1, delay_seconds=0.0):
                    open_buy_orders = [
                        order for order in _get_open_orders_for_symbol(symbol)
                        if str(getattr(order, "side", "")).lower().endswith("buy")
                    ]
                    if not open_buy_orders:
                        _place_entry_orders_for_symbols([symbol])
                continue
            
            # Safety: If bracket 1 placed but bracket 2 missing, place bracket 2 now
            if b1_entry_id and not b2_entry_id:
                try:
                    entry2_order = _create_limit_order(
                        symbol, float(cfg["bracket_qty"]), OrderSide.BUY, cfg["entry_price"],
                        cfg["order_time_in_force"], not cfg["is_crypto"] and not cfg["is_option"]
                    )
                    entry2_id = str(trading_client.submit_order(order_data=entry2_order).id)
                    if entry2_id:
                        state["bracket2"]["entry_order_id"] = entry2_id
                        save_bracket_file(symbol, 2, entry_order_id=entry2_id,
                                        entry_price=cfg["entry_price"], stop_price=cfg["stop_price"],
                                        target_price=cfg["target2_price"], qty=cfg["bracket_qty"])
                        print(f"[{symbol}] ✓ Bracket 2 entry placed: {entry2_id}")
                except Exception as e:
                    print(f"[{symbol}] ✗ Failed to place bracket 2 entry: {e}")
        
        # STEP 2: Submit and track one OCO exit pair for each filled half-position.
        for trade_id in list(trade_states.keys()):
            _recover_missing_entry_ids_for_trade(trade_id)
            _ensure_oco_exits_for_trade(trade_id)

        for symbol in list(trade_states.keys()):
            cfg = trade_states[symbol]["config"]
            state = trade_states[symbol]["state"]
            
            b1 = state["bracket1"]
            b2 = state["bracket2"]
            
            if not b1["entry_order_id"] or not b2["entry_order_id"]:
                continue
            if (_check_order_status(b1["entry_order_id"]) != "filled" or
                    _check_order_status(b2["entry_order_id"]) != "filled"):
                continue

            for bracket_num, bracket, target_price in (
                (1, b1, cfg["target1_price"]),
                (2, b2, cfg["target2_price"]),
            ):
                if not bracket["entry_order_id"] or bracket["target_order_id"] or bracket["stop_order_id"]:
                    continue
                try:
                    target_id, stop_id = _submit_oco_exit(
                        symbol, float(cfg["bracket_qty"]), cfg["stop_price"],
                        target_price, cfg["order_time_in_force"]
                    )
                    bracket["target_order_id"] = target_id
                    bracket["stop_order_id"] = stop_id
                    save_bracket_file(
                        symbol, bracket_num,
                        entry_order_id=bracket["entry_order_id"],
                        stop_order_id=stop_id,
                        target_order_id=target_id,
                        entry_price=cfg["entry_price"],
                        stop_price=cfg["stop_price"],
                        target_price=target_price,
                        qty=cfg["bracket_qty"],
                    )
                    print(f"[{symbol}] ✓ B{bracket_num} OCO: stop ${cfg['stop_price']:.2f} | target ${target_price:.2f}")
                except Exception as e:
                    print(f"[{symbol}] ✗ B{bracket_num} OCO error: {e}")
            
            # BRACKET 1: Check if entry placed but stops/targets not yet
            if False and b1["entry_order_id"] and not b1["stop_order_id"] and not b1["target_order_id"]:
                # Check if B1 entry filled
                pos = _find_position_for_symbol(symbol, retries=1, delay_seconds=0.0)
                if pos:
                    qty = _normalize_position_qty(float(pos.qty))
                    
                    if qty >= cfg["bracket_qty"] * 0.9:  # B1 filled
                        print(f"[{current_time.strftime('%H:%M:%S')}] [{symbol}] ✓ B1 FILLED @ ${float(pos.avg_entry_price):.2f}")
                        print(f"  ⏳ Placing B1 stop + target...")
                        
                        # Place B1 STOP
                        try:
                            stop_b1 = _create_stop_order(symbol, float(cfg["bracket_qty"]), OrderSide.SELL,
                                                        cfg["stop_price"], cfg["order_time_in_force"],
                                                        not cfg["is_crypto"] and not cfg["is_option"])
                            sr_b1 = trading_client.submit_order(order_data=stop_b1)
                            b1["stop_order_id"] = str(sr_b1.id)
                            print(f"    ✓ B1 Stop @ ${cfg['stop_price']:.2f}")
                        except Exception as e:
                            print(f"    ✗ B1 Stop Error: {e}")
                        
                        time.sleep(1)
                        
                        # Place B1 TARGET
                        try:
                            target_b1 = _create_limit_order(symbol, float(cfg["bracket_qty"]), OrderSide.SELL,
                                                           cfg["target1_price"], cfg["order_time_in_force"],
                                                           not cfg["is_crypto"] and not cfg["is_option"])
                            tr_b1 = trading_client.submit_order(order_data=target_b1)
                            b1["target_order_id"] = str(tr_b1.id)
                            print(f"    ✓ B1 Target @ ${cfg['target1_price']:.2f}\n")
                        except Exception as e:
                            print(f"    ✗ B1 Target Error: {e}\n")
                        
                        # Save B1
                        save_bracket_file(symbol, 1, entry_order_id=b1["entry_order_id"],
                                        stop_order_id=b1["stop_order_id"], target_order_id=b1["target_order_id"],
                                        entry_price=cfg["entry_price"], stop_price=cfg["stop_price"],
                                        target_price=cfg["target1_price"], qty=cfg["bracket_qty"])
            
            # BRACKET 2: Check if B1 is complete before placing B2
            # (Only place B2 entry if B1 entry already exists and B2 entry doesn't)
            if False and b1["entry_order_id"] and not b2["entry_order_id"] and b1["stop_order_id"]:
                # B1 is now protected (has stops/targets) → Safe to place B2
                print(f"[{current_time.strftime('%H:%M:%S')}] [{symbol}] ⏳ B1 protected, placing B2 entry...")
                
                try:
                    entry2_order = _create_limit_order(
                        symbol=symbol,
                        qty=float(cfg["bracket_qty"]),
                        side=OrderSide.BUY,
                        limit_price=cfg["entry_price"],
                        time_in_force=cfg["order_time_in_force"],
                        is_stock=not cfg["is_crypto"] and not cfg["is_option"]
                    )
                    entry2_resp = trading_client.submit_order(order_data=entry2_order)
                    b2["entry_order_id"] = str(entry2_resp.id)
                    print(f"  ✓ B2 Entry @ ${cfg['entry_price']:.2f}\n")
                    
                    save_bracket_file(symbol, 2, entry_order_id=b2["entry_order_id"],
                                    stop_order_id=None, target_order_id=None,
                                    entry_price=cfg["entry_price"], stop_price=cfg["stop_price"],
                                    target_price=cfg["target2_price"], qty=cfg["bracket_qty"])
                except Exception as e:
                    print(f"  ✗ B2 Entry Error: {e}\n")
            
            # BRACKET 2: Check if entry placed but stops/targets not yet
            if False and b2["entry_order_id"] and not b2["stop_order_id"] and not b2["target_order_id"]:
                # Check if B2 entry filled
                pos = _find_position_for_symbol(symbol, retries=1, delay_seconds=0.0)
                if pos:
                    qty = _normalize_position_qty(float(pos.qty))
                    
                    # B2 filled when total qty >= 2 (B1 + B2)
                    if qty >= cfg["num_stocks"] * 0.9:
                        print(f"[{current_time.strftime('%H:%M:%S')}] [{symbol}] ✓ B2 FILLED @ ${float(pos.avg_entry_price):.2f}")
                        print(f"  ⏳ Placing B2 stop + target...")
                        
                        # Place B2 STOP
                        try:
                            stop_b2 = _create_stop_order(symbol, float(cfg["bracket_qty"]), OrderSide.SELL,
                                                        cfg["stop_price"], cfg["order_time_in_force"],
                                                        not cfg["is_crypto"] and not cfg["is_option"])
                            sr_b2 = trading_client.submit_order(order_data=stop_b2)
                            b2["stop_order_id"] = str(sr_b2.id)
                            print(f"    ✓ B2 Stop @ ${cfg['stop_price']:.2f}")
                        except Exception as e:
                            print(f"    ✗ B2 Stop Error: {e}")
                        
                        time.sleep(1)
                        
                        # Place B2 TARGET
                        try:
                            target_b2 = _create_limit_order(symbol, float(cfg["bracket_qty"]), OrderSide.SELL,
                                                           cfg["target2_price"], cfg["order_time_in_force"],
                                                           not cfg["is_crypto"] and not cfg["is_option"])
                            tr_b2 = trading_client.submit_order(order_data=target_b2)
                            b2["target_order_id"] = str(tr_b2.id)
                            print(f"    ✓ B2 Target @ ${cfg['target2_price']:.2f}\n")
                        except Exception as e:
                            print(f"    ✗ B2 Target Error: {e}\n")
                        
                        # Save B2
                        save_bracket_file(symbol, 2, entry_order_id=b2["entry_order_id"],
                                        stop_order_id=b2["stop_order_id"], target_order_id=b2["target_order_id"],
                                        entry_price=cfg["entry_price"], stop_price=cfg["stop_price"],
                                        target_price=cfg["target2_price"], qty=cfg["bracket_qty"])
        
        # STEP 3: Monitor each bracket's outcome (existing per-bracket logic)
        for symbol in list(trade_states.keys()):
            cfg = trade_states[symbol]["config"]
            state = trade_states[symbol]["state"]
            
            # Monitor each bracket independently (Bracket 1 and Bracket 2)
            for bracket_num in [1, 2]:
                bracket = state[f"bracket{bracket_num}"]
                entry_id = bracket["entry_order_id"]
                stop_id = bracket["stop_order_id"]
                target_id = bracket["target_order_id"]
                
                if bracket["filled"]:
                    # Bracket already complete, skip
                    continue
                
                # PHASE 0: Safety check - if bracket 2 has no entry order ID, place it now
                if bracket_num == 2 and not entry_id:
                    print(f"[{symbol}] ⚠ Bracket 2 missing entry order! Placing now...")
                    try:
                        entry2_order = _create_limit_order(
                            symbol, float(cfg["bracket_qty"]), OrderSide.BUY, cfg["entry_price"],
                            cfg["order_time_in_force"], not cfg["is_crypto"] and not cfg["is_option"]
                        )
                        entry2_id = str(trading_client.submit_order(order_data=entry2_order).id)
                        if entry2_id:
                            entry_id = entry2_id
                            bracket["entry_order_id"] = entry_id
                            print(f"[{symbol}] ✓ Bracket 2 Entry placed: {entry_id}")
                            
                            # Save to tracking file
                            save_bracket_file(
                                symbol, 2,
                                entry_order_id=entry_id,
                                stop_order_id=None,
                                target_order_id=None,
                                entry_price=cfg["entry_price"],
                                stop_price=cfg["stop_price"],
                                target_price=cfg["target2_price"],
                                qty=cfg["bracket_qty"]
                            )
                    except Exception as e:
                        print(f"[{symbol}] ✗ Error placing Bracket 2 entry in Phase 0: {e}")
                        continue  # Skip to next bracket
                
                # NOTE: Phase 1 (old synchronized placement) has been moved to STEP 2 above
                # STEP 2 now handles ALL stop/target placement with proper sequential delay
                # This prevents duplicate placement and qty conflicts
                
                # Phase 2: Check bracket outcome (target filled vs stopped out vs still pending)
                if stop_id or target_id:
                    # Get detailed status of this bracket's target and stop orders
                    outcome = _get_bracket_outcome(symbol, bracket_num, cfg, state)
                    
                    if outcome:
                        if outcome["status"] == "target_filled":
                            # Bracket hit target! Half the position exits at profit
                            target_price = outcome["target_price"]
                            print(f"[{current_time.strftime('%H:%M:%S')}] [{symbol}] Bracket {bracket_num} TARGET FILLED ✓ @ ${target_price:.2f}")
                            bracket["filled"] = True
                            remove_bracket_file(symbol, bracket_num)
                            
                            # Update position status
                            other_bracket = 2 if bracket_num == 1 else 1
                            other_status = "target_filled" if state[f"bracket{other_bracket}"]["filled"] else "pending"
                            save_position_status(
                                symbol, 
                                qty=cfg["bracket_qty"],  # Remaining qty after this bracket exits
                                entry_price=cfg["entry_price"],
                                bracket1_status="target_filled" if bracket_num == 1 else other_status,
                                bracket2_status=other_status if bracket_num == 1 else "target_filled",
                                current_price=target_price
                            )
                        
                        elif outcome["status"] == "stopped_out":
                            # Bracket hit stop loss! Half the position exits at loss
                            stop_price = outcome["stop_price"]
                            print(f"[{current_time.strftime('%H:%M:%S')}] [{symbol}] Bracket {bracket_num} STOPPED OUT ⚠ @ ${stop_price:.2f}")
                            bracket["filled"] = True
                            remove_bracket_file(symbol, bracket_num)
                            
                            # Update position status
                            other_bracket = 2 if bracket_num == 1 else 1
                            other_status = "stopped_out" if state[f"bracket{other_bracket}"]["filled"] else "pending"
                            save_position_status(
                                symbol, 
                                qty=cfg["bracket_qty"],  # Remaining qty after this bracket exits
                                entry_price=cfg["entry_price"],
                                bracket1_status="stopped_out" if bracket_num == 1 else other_status,
                                bracket2_status=other_status if bracket_num == 1 else "stopped_out",
                                current_price=stop_price
                            )
                        
                        elif outcome["status"] == "pending":
                            # Both orders still open, waiting for either target or stop to hit
                            try:
                                current_price = _get_current_price(symbol)
                            except:
                                current_price = None
                            
                            # Show status occasionally
                            if current_time.second % 30 == 0:
                                target_price = outcome["target_price"]
                                stop_price = outcome["stop_price"]
                                status = f"  📊 {symbol} B{bracket_num}: Price ${current_price:.2f} | "
                                status += f"Target: ${target_price:.2f} | Stop: ${stop_price:.2f}"
                                print(status)
            
            # ===== RE-ENTRY CHECK: Both brackets complete, price reverses to entry =====
            # Check if BOTH brackets are complete (both had their target or stop execute)
            bracket1_complete = state["bracket1"]["filled"]
            bracket2_complete = state["bracket2"]["filled"]
            
            if bracket1_complete and bracket2_complete:
                # Both brackets are done (targets or stops executed for both)
                # Check for re-entry opportunity
                pos = _find_position_for_symbol(symbol, retries=1, delay_seconds=0.0)
                
                if not pos:
                    # No position exists - both brackets have exited
                    # This means: Bracket1 either hit target1 OR stop
                    #            Bracket2 either hit target2 OR stop
                    # Now wait for price to return to entry level to re-enter
                    try:
                        current_price = _get_current_price(symbol)
                        
                        # Check if price has returned to entry level (within 1%)
                        entry_threshold_upper = cfg["entry_price"] * 1.01
                        entry_threshold_lower = cfg["entry_price"] * 0.99
                        
                        if entry_threshold_lower <= current_price <= entry_threshold_upper:
                            # Price is back at entry! Reset and place NEW bracket orders
                            print(f"\n[{current_time.strftime('%H:%M:%S')}] [{symbol}] 🔄 RE-ENTRY OPPORTUNITY!")
                            print(f"  Both brackets exited | Price ${current_price:.2f} @ entry (${cfg['entry_price']:.2f})")
                            print(f"  Placing NEW bracket orders (50% qty each)\n")
                            
                            # Clear position status (position territory cleared)
                            remove_position_status(symbol)
                            
                            # Reset bracket states
                            state["bracket1"] = {
                                "entry_order_id": None,
                                "stop_order_id": None,
                                "target_order_id": None,
                                "filled": False,
                            }
                            state["bracket2"] = {
                                "entry_order_id": None,
                                "stop_order_id": None,
                                "target_order_id": None,
                                "filled": False,
                            }
                            
                            # Place new bracket orders
                            try:
                                _place_entry_orders_for_symbols([symbol])
                            except Exception as e:
                                print(f"  ✗ Error placing new bracket orders: {e}\n")
                    
                    except Exception as e:
                        pass  # Ignore price check errors
        
        time.sleep(CHECK_INTERVAL)

except KeyboardInterrupt:
    print(f"\n\n{'─'*64}")
    print(f"  Monitoring stopped by user")
    print(f"{'─'*64}\n")
    sys.exit(0)


# ============================================================================
# HELPER FUNCTIONS FOR EXTERNAL USE (e.g., risk_management.py)
# ============================================================================

def get_all_active_positions() -> list[dict]:
    """
    Get all active bracket orders from tracking files.
    Aggregates both bracket1 and bracket2 files for each symbol.
    
    Returns:
        List of bracket dicts with status
    """
    brackets = []
    
    for bracket_file in POSITIONS_DIR.glob("*_bracket*.txt"):
        try:
            with open(bracket_file, 'r') as f:
                bracket_data = json.load(f)
                brackets.append(bracket_data)
        except Exception as e:
            print(f"[ERROR] Could not read {bracket_file.name}: {e}")
    
    return brackets


def update_stop_loss_for_symbol(symbol: str, bracket_num: int, new_stop_loss: float) -> bool:
    """
    Update stop loss for a bracket. Called by risk_management.py if needed.
    
    Args:
        symbol: Trading symbol
        bracket_num: 1 or 2 (which bracket)
        new_stop_loss: New stop loss price
    
    Returns:
        True if update successful, False otherwise
    """
    bracket_data = load_bracket_file(symbol, bracket_num)
    
    if not bracket_data:
        print(f"[ERROR] No bracket file found for {symbol}_bracket{bracket_num}")
        return False
    
    bracket_data["stop_price"] = new_stop_loss
    bracket_data["last_updated"] = datetime.now().isoformat()
    
    try:
        save_bracket_file(
            symbol, bracket_num,
            entry_order_id=bracket_data.get("entry_order_id"),
            stop_order_id=bracket_data.get("stop_order_id"),
            target_order_id=bracket_data.get("target_order_id"),
            entry_price=bracket_data.get("entry_price"),
            stop_price=new_stop_loss,
            target_price=bracket_data.get("target_price"),
            qty=bracket_data.get("qty"),
            filled=bracket_data.get("filled", False)
        )
        print(f"[UPDATE] Stop loss for {symbol}_bracket{bracket_num} updated to ${new_stop_loss:.2f}")
        return True
    except Exception as e:
        print(f"[ERROR] Could not update stop loss for {symbol}_bracket{bracket_num}: {e}")
        return False


def emergency_exit_position(symbol: str, trading_client_instance=None) -> bool:
    """
    Force exit a position with market order for both brackets.
    Used by risk_management.py for emergency situations.
    
    Args:
        symbol: Trading symbol
        trading_client_instance: Alpaca trading client (uses global if None)
    
    Returns:
        True if exit successful, False otherwise
    """
    client = trading_client_instance or trading_client
    
    try:
        pos = _find_position_for_symbol(symbol, retries=1, delay_seconds=0.0)
        
        if not pos:
            print(f"[ERROR] No open position for {symbol}")
            return False
        
        current_qty = _normalize_position_qty(float(pos.qty))
        
        market_order = MarketOrderRequest(
            symbol=symbol,
            qty=float(current_qty),
            side=OrderSide.SELL,
            time_in_force=TimeInForce.GTC,
        )
        
        response = client.submit_order(order_data=market_order)
        print(f"[EMERGENCY] Market order submitted for {symbol}: {current_qty} shares (ID: {response.id})")
        remove_all_brackets_for_symbol(symbol)
        remove_position_status(symbol)  # Clean up position tracking
        return True
        
    except Exception as e:
        print(f"[ERROR] Could not exit position for {symbol}: {e}")
        return False


def get_all_active_position_statuses() -> list:
    """
    Get summary of all active positions (position territory tracked).
    Reads all position status files in positions/ directory.
    
    Returns:
        List of dicts with position status info for each active symbol
    """
    active_positions = []
    
    try:
        for position_file in POSITIONS_DIR.glob("*_position.txt"):
            try:
                with open(position_file, 'r') as f:
                    position_data = json.load(f)
                    active_positions.append(position_data)
            except Exception:
                continue
    except Exception:
        pass
    
    return active_positions


def get_position_status(symbol: str) -> dict | None:
    """
    Get combined status of both brackets for a symbol.
    
    Args:
        symbol: Trading symbol
    
    Returns:
        Dict with both bracket statuses, or None if neither bracket file exists
    """
    bracket1 = load_bracket_file(symbol, 1)
    bracket2 = load_bracket_file(symbol, 2)
    
    if not bracket1 and not bracket2:
        return None
    
    return {
        "symbol": symbol,
        "bracket1": bracket1,
        "bracket2": bracket2,
    }

