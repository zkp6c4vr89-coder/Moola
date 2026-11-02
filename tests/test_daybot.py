from datetime import date, datetime, time, timedelta
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from daybot import backtest, config, risk, strategy
from daybot.main import round_trips

ET = config.ET
OPEN = datetime(2025, 3, 4, 9, 30, tzinfo=ET)
CLOSE = datetime(2025, 3, 4, 16, 0, tzinfo=ET)


def session(day: date, close_hour=16):
    return {"date": day, "open": datetime.combine(day, time(9, 30), ET),
            "close": datetime.combine(day, time(close_hour, 0), ET)}


def minute_bars(days: list[date], path_fn) -> pd.DataFrame:
    rows = []
    for d in days:
        for i in range(390):
            ts = pd.Timestamp(datetime.combine(d, time(9, 30), ET)) + pd.Timedelta(minutes=i)
            px = path_fn(d, i)
            rows.append((ts, px, px * 1.0005, px * 0.9995, px, 1000.0))
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"]).set_index("ts")
    return df


def dip_and_recover(d, i):
    # flat at 100, sell-off to 98 around 11:00, then recovery to 100 by 13:00
    if i < 60:
        return 100.0
    if i < 90:
        return 100 - 2 * (i - 60) / 30
    if i < 210:
        return 98 + 2 * (i - 90) / 120
    return 100.0


# --- risk ---------------------------------------------------------------------------
def test_sizing_and_daily_limits():
    s = risk.new_state()
    assert risk.base_unit(s) == 10 and risk.position_notional(s) == 2.50
    assert risk.day_stop_reason(0.20, 10).startswith("profit ceiling")
    assert risk.day_stop_reason(-0.30, 10).startswith("daily loss")
    assert risk.day_stop_reason(0.19, 10) is None


def test_entry_window_and_trade_cap():
    ok = lambda now, n=0, pnl=0.0, stopped=False: risk.can_enter(now, OPEN, CLOSE, n, pnl, 10, stopped)[0]
    assert not ok(OPEN + timedelta(minutes=10))           # first 15 minutes
    assert ok(OPEN + timedelta(minutes=16))
    assert not ok(OPEN + timedelta(minutes=16), n=3)      # max 3 trades
    assert not ok(OPEN + timedelta(minutes=16), pnl=0.2)  # ceiling hit
    assert not ok(OPEN + timedelta(minutes=16), stopped=True)
    assert not ok(datetime(2025, 3, 4, 15, 31, tzinfo=ET))


def test_scale_up_after_20_positive_days_and_no_trade_days_are_neutral():
    s = risk.new_state()
    for _ in range(19):
        risk.end_of_day(s, 0.05, traded=True)
    risk.end_of_day(s, 0.0, traded=False)                 # quiet day: streak kept
    assert s["streak"] == 19 and s["level"] == 0
    events = risk.end_of_day(s, 0.05, traded=True)
    assert s["level"] == 1 and risk.base_unit(s) == 25 and s["streak"] == 0 and events


def test_losing_day_resets_streak_and_drawdown_steps_down():
    s = risk.new_state()
    s.update(level=2, streak=7)
    risk.end_of_day(s, -0.01, traded=True)
    assert s["streak"] == 0
    risk.end_of_day(s, -0.25 * 50, traded=True)
    assert s["level"] == 1 and s["level_pnl"] == 0


def test_kill_switch_on_account_or_bot_drawdown():
    s = risk.new_state()
    assert risk.kill_check(s, 100_000, 0) is None
    assert risk.kill_check(s, 69_000, 0).startswith("account")
    s2 = risk.new_state()
    risk.kill_check(s2, 100, 0)
    assert risk.kill_check(s2, 100, -3.5).startswith("bot balance")


# --- strategy ---------------------------------------------------------------------------
def test_signal_on_stretched_dip_with_bracket_levels():
    df = minute_bars([date(2025, 3, 3), date(2025, 3, 4)], dip_and_recover)
    ind = strategy.indicators(df)
    params = dict(strategy.PARAMS, require_green_bar=False, rsi_entry=40)
    hits = ind[strategy.entry_mask(ind, params)]
    assert not hits.empty
    sig, _ = strategy.make_signal("SPY", hits.iloc[0], params)
    assert sig and sig.stop < sig.price < sig.take_profit


def test_no_signal_in_flat_market():
    df = minute_bars([date(2025, 3, 3), date(2025, 3, 4)], lambda d, i: 100.0)
    assert not strategy.entry_mask(strategy.indicators(df)).any()


def test_indicators_have_no_lookahead():
    df = minute_bars([date(2025, 3, 3), date(2025, 3, 4)], dip_and_recover)
    full = strategy.indicators(df)
    cut = df[df.index < pd.Timestamp(datetime(2025, 3, 4, 11, 0, tzinfo=ET))]
    part = strategy.indicators(cut)
    common = part.index[:-1]
    pd.testing.assert_frame_equal(full.loc[common, ["vwap", "rsi", "atr"]], part.loc[common, ["vwap", "rsi", "atr"]])


# --- backtest ------------------------------------------------------------------------------
@pytest.fixture
def market():
    days = [date(2025, 3, 3), date(2025, 3, 4), date(2025, 3, 5)]
    bars = {"SPY": minute_bars(days, dip_and_recover)}
    return backtest.Market(bars, [session(d) for d in days] + [session(date(2025, 3, 6), close_hour=13)])


def test_backtest_rules_and_costs(market):
    params = dict(strategy.PARAMS, require_green_bar=False, rsi_entry=40, min_reward_risk=0.1)
    daily, trades = backtest.simulate(market, params)
    assert len(market.sessions) == 3                      # half-day dropped
    assert not trades.empty
    assert (daily["trades"] <= config.MAX_TRADES_PER_DAY).all()
    for _, t in trades.iterrows():
        assert t["entry_time"].time() >= time(9, 45)
        gross = (t["exit_price"] - t["entry_price"]) * t["qty"]
        assert t["pnl"] < gross                            # fees always cost something
    assert (trades["qty"] * trades["entry_price"]).round(6).le(2.5).all()


def test_fee_rounding():
    assert backtest.sell_fees(2.5, 0.004) == pytest.approx(0.02)   # SEC + TAF each round up to 1 cent


def test_walk_forward_folds_do_not_overlap():
    fs = backtest.folds(date(2024, 1, 1), date(2025, 1, 31), 6, 2)
    assert fs
    for tr_a, tr_b, te_a, te_b in fs:
        assert tr_a < tr_b < te_a <= te_b and te_a == tr_b + timedelta(days=1)


# --- round trips ----------------------------------------------------------------------------
def _order(cid, side, qty, px, minute):
    return SimpleNamespace(client_order_id=cid, side=SimpleNamespace(value=side), symbol="SPY", filled_qty=str(qty),
                           filled_avg_price=str(px), filled_at=datetime(2025, 3, 4, 10, minute, tzinfo=ET))


def test_round_trips_pair_and_label_exits():
    orders = [_order("db-entry-SPY-1", "buy", 0.004, 600, 0), _order("db-tp-SPY-2", "sell", 0.004, 603, 5),
              _order("manual", "buy", 1, 600, 6)]
    t = round_trips(orders, date(2025, 3, 4))
    assert len(t) == 1 and t[0]["exit_reason"] == "tp" and t[0]["pnl"] == pytest.approx(0.012)


def test_report_headlines_fee_adjusted_pnl(tmp_path, monkeypatch):
    from daybot import journal, report
    monkeypatch.setattr(config, "LOG_DIR", tmp_path)
    monkeypatch.setattr(config, "STATE_FILE", tmp_path / "state.json")
    journal.append(journal.DAILY, [{"date": "2026-11-02", "base_unit": 10, "day_pnl": 0.03, "est_fees": 0.04,
                                    "day_pnl_after_fees": -0.01, "pct_of_base": -0.001, "trades": 2, "wins": 0,
                                    "stop_reason": "", "level_after": 0, "base_after": 10, "streak": 0,
                                    "level_pnl": -0.01, "bot_balance": 9.99, "account_equity": 100000, "events": ""}])
    journal.append(journal.TRADES, [{"date": "2026-11-02", "symbol": "SPY", "entry_price": 600, "exit_price": 603,
                                     "qty": 0.005, "pnl": 0.015, "est_fees": 0.02, "pnl_after_fees": -0.005,
                                     "exit_reason": "tp"}] * 2)
    report.write()
    text = (tmp_path / "summary.md").read_text()
    assert "Total P&L:** $-0.01" in text and "Raw paper P&L (no fees):** $0.03" in text
    assert "win rate 0%" in text
