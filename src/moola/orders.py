"""Turn target weights into orders. PROTECTED: reviews must not edit this file."""
from moola.config import is_crypto
from moola.types import Order, Portfolio


def plan_orders(targets: dict[str, float], portfolio: Portfolio, prices: dict[str, float],
                cfg: dict, stock_session: bool) -> list[Order]:
    r = cfg["risk"]
    orders: list[Order] = []
    for sym in sorted(set(targets) | set(portfolio.positions)):
        if not is_crypto(sym) and not stock_session:
            continue  # stocks only trade after a real session; crypto trades every day
        current = portfolio.positions.get(sym, 0.0)
        target = targets.get(sym, 0.0) * portfolio.equity
        price = prices.get(sym)
        if target == 0 and current > 0:
            orders.append(Order(sym, "sell", current, True, price, "exit: no longer a target"))
            continue
        diff = target - current
        if abs(diff) < r["min_trade_value"]:
            continue
        side = "buy" if diff > 0 else "sell"
        orders.append(Order(sym, side, round(abs(diff), 2), False, price, f"rebalance to {targets.get(sym, 0):.1%}"))

    orders = sorted(orders, key=lambda o: o.notional, reverse=True)[: r["max_orders_per_day"]]

    # Never spend more than cash plus what we're selling: no leverage, even briefly.
    budget = max(0.0, portfolio.cash + sum(o.notional for o in orders if o.side == "sell")) * 0.99
    buys = [o for o in orders if o.side == "buy"]
    wanted = sum(o.notional for o in buys)
    if wanted > budget:
        scale = budget / wanted
        for o in buys:
            o.notional = round(o.notional * scale, 2)
            o.reason += f" (scaled {scale:.0%} to fit cash)"
        orders = [o for o in orders if o.side == "sell" or o.notional >= r["min_trade_value"]]

    return sorted(orders, key=lambda o: o.side != "sell")  # sells first
