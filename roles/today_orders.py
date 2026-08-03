from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Any

import requests
from alpaca.common.exceptions import APIError
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import QueryOrderStatus
from alpaca.trading.requests import GetOrdersRequest


class TodayOrdersRole:
    """List orders placed this week with status and key details."""

    def __init__(self, trading_client: TradingClient) -> None:
        self.trading_client = trading_client

    def list_today_orders(self) -> list[dict[str, Any]]:
        try:
            now = datetime.now(timezone.utc)
            week_start = datetime.combine(
                (now.date() - timedelta(days=now.weekday())),
                time.min,
                tzinfo=timezone.utc,
            )

            request = GetOrdersRequest(
                status=QueryOrderStatus.ALL,
                after=week_start,
                until=now,
                limit=500,
            )
            orders = self.trading_client.get_orders(filter=request)
        except requests.exceptions.ProxyError as exc:
            raise RuntimeError(f"Network proxy issue: {exc}") from exc
        except requests.exceptions.ConnectionError as exc:
            raise RuntimeError(f"Network connection issue: {exc}") from exc
        except APIError as exc:
            raise RuntimeError(f"Alpaca API error: {exc}") from exc

        return [
            {
                "id": order.id,
                "symbol": order.symbol,
                "side": order.side,
                "qty": order.qty,
                "order_type": order.order_type,
                "status": order.status,
                "filled_qty": getattr(order, "filled_qty", None),
                "filled_avg_price": getattr(order, "filled_avg_price", None),
                "limit_price": getattr(order, "limit_price", None),
                "stop_price": getattr(order, "stop_price", None),
                "submitted_at": getattr(order, "submitted_at", None),
            }
            for order in orders
        ]

    def print_today_orders(self) -> None:
        orders = self.list_today_orders()
        if not orders:
            print("No orders placed this week.")
            return

        print("Orders placed this week:")
        for order in orders:
            print(
                f"- {order['symbol']} | side={order['side']} | qty={order['qty']} | "
                f"status={order['status']} | filled_qty={order['filled_qty']} | "
                f"filled_avg_price={order['filled_avg_price']} | "
                f"limit_price={order['limit_price']} | stop_price={order['stop_price']} | "
                f"type={order['order_type']} | submitted_at={order['submitted_at']}"
            )
