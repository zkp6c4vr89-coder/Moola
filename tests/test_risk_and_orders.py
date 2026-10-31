from moola.orders import plan_orders
from moola.risk import enforce
from moola.types import Portfolio


def pf(equity=100_000, cash=100_000, positions=None, peak=None):
    return Portfolio(equity, cash, positions or {}, peak or equity)


def test_position_cap_and_no_shorts(cfg):
    out, flags = enforce({"SPY": 0.9, "TLT": -0.2, "GLD": float("nan")}, pf(), cfg)
    assert out == {"SPY": cfg["risk"]["max_position_weight"]}
    assert len(flags) == 3


def test_crypto_cap(cfg):
    out, _ = enforce({"BTC/USD": 0.25, "ETH/USD": 0.25, "SPY": 0.25}, pf(), cfg)
    assert abs(out["BTC/USD"] + out["ETH/USD"] - cfg["risk"]["max_crypto_weight"]) < 1e-9
    assert out["SPY"] == 0.25


def test_no_leverage(cfg):
    out, _ = enforce({s: 0.25 for s in ["SPY", "QQQ", "IWM", "EFA", "GLD"]}, pf(), cfg)
    assert sum(out.values()) <= 1 + 1e-9


def test_kill_switch(cfg):
    out, flags = enforce({"SPY": 0.2}, pf(equity=70_000, peak=100_000), cfg)
    assert out == {} and flags[0].startswith("KILL_SWITCH")


def test_orders_skip_stocks_outside_session(cfg):
    orders = plan_orders({"SPY": 0.2, "BTC/USD": 0.2}, pf(), {}, cfg, stock_session=False)
    assert [o.symbol for o in orders] == ["BTC/USD"]


def test_orders_sells_first_and_exit_closes_all(cfg):
    p = pf(cash=10_000, positions={"TLT": 30_000, "SPY": 60_000})
    orders = plan_orders({"SPY": 0.25, "GLD": 0.25}, p, {}, cfg, stock_session=True)
    sides = [o.side for o in orders]
    assert sides == sorted(sides, key=lambda s: s != "sell")
    tlt = next(o for o in orders if o.symbol == "TLT")
    assert tlt.close_all


def test_orders_never_exceed_cash(cfg):
    orders = plan_orders({"SPY": 0.25, "QQQ": 0.25}, pf(cash=1_000, positions={"GLD": 99_000}), {}, cfg, True)
    buys = sum(o.notional for o in orders if o.side == "buy")
    sells = sum(o.notional for o in orders if o.side == "sell")
    assert buys <= 1_000 + sells


def test_small_rebalances_skipped(cfg):
    orders = plan_orders({"SPY": 0.2}, pf(positions={"SPY": 19_990}, cash=80_010), {}, cfg, True)
    assert orders == []


def test_order_count_capped(cfg):
    targets = {f"S{i}": 0.05 for i in range(20)}
    orders = plan_orders(targets, pf(), {}, cfg, True)
    assert len(orders) <= cfg["risk"]["max_orders_per_day"]
