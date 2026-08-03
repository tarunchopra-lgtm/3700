from roles.credentials import bootstrap_trading_auth
from roles.position import PositionCheckRole
from roles.today_orders import TodayOrdersRole


def main() -> None:
    try:
        _, trading_client = bootstrap_trading_auth("main.py")

        print("Open positions:")
        position_checker = PositionCheckRole(trading_client)
        position_checker.print_positions()

        print("\nToday orders:")
        today_orders = TodayOrdersRole(trading_client)
        today_orders.print_today_orders()
    except Exception as exc:
        print(f"Error: {exc}")


if __name__ == "__main__":
    main()
