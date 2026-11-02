"""Backtest on IEX minute bars with spreads, slippage and fees, plus walk-forward parameter selection.

Uses the same strategy.py and risk.py as the live bot. Fill model:
  - a 5-min setup completing at T enters at the open of the 1-min bar starting at T;
  - buys pay half the spread + slippage; sells receive half the spread + slippage less, minus SEC/TAF fees
    (rounded up to the cent, as Alpaca bills them);
  - stops fill at min(bar open, stop); take-profits at max(bar open, target); if one bar touches both, the stop wins;
  - flat 10 minutes before the close; first 15 minutes, half-days and holidays skipped; max 3 trades a day;
    the day stops at the profit ceiling or loss limit.

    python -m daybot.backtest                          # walk-forward over the default range
    python -m daybot.backtest --start 2024-01-01 --end 2025-12-31 --train-months 6 --test-months 2
    python -m daybot.backtest --fixed                  # current PARAMS only, with the scaling ladder on
"""
import argparse
import itertools
import math
from datetime import date, timedelta

import pandas as pd

from daybot import config, data, risk, strategy
from daybot.report import format_stats, stats

GRID = {"rsi_entry": [25.0, 30.0, 35.0], "min_vwap_dev": [0.002, 0.004, 0.006], "stop_atr": [1.0, 1.5, 2.0]}
MIN_TRAIN_TRADES = 10


sell_fees = risk.sell_fees


def buy_price(symbol: str, px: float) -> float:
    return px * (1 + (config.spread_bps(symbol) / 2 + config.SLIPPAGE_BPS) / 1e4)


def sell_price(symbol: str, px: float) -> float:
    return px * (1 - (config.spread_bps(symbol) / 2 + config.SLIPPAGE_BPS) / 1e4)


class Market:
    """Pre-indexed minute bars and indicators, shared by every simulation run."""

    def __init__(self, bars: dict[str, pd.DataFrame], sessions: list[dict]):
        self.sessions = [s for s in sessions if (s["close"].hour, s["close"].minute) == (16, 0)]
        self.ind = {sym: strategy.indicators(df) for sym, df in bars.items()}
        self.minutes: dict[date, dict[str, dict]] = {}
        for sym, df in bars.items():
            for day, g in df.groupby(df.index.date):
                self.minutes.setdefault(day, {})[sym] = {
                    ts: (o, h, l, c) for ts, o, h, l, c in zip(g.index, g["open"], g["high"], g["low"], g["close"])}


def simulate(m: Market, params: dict, scaling: bool = False, start: date | None = None,
             end: date | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    state = risk.new_state()
    candidates: dict[pd.Timestamp, list] = {}
    for sym, ind in m.ind.items():
        hits = ind[strategy.entry_mask(ind, params)]
        for _, row in hits.iterrows():
            candidates.setdefault(row["bar_end"], []).append((sym, row))

    daily, trades = [], []
    for sess in m.sessions:
        day = sess["date"]
        if (start and day < start) or (end and day > end):
            continue
        base = risk.base_unit(state) if scaling else float(config.LADDER[0])
        notional = config.MAX_POSITION_FRACTION * base
        bars = m.minutes.get(day, {})
        flatten_at = sess["close"] - timedelta(minutes=config.FLATTEN_BEFORE_CLOSE_MINUTES)
        pos: dict[str, dict] = {}
        realized, n_trades, stop_reason = 0.0, 0, ""
        last_px: dict[str, float] = {}

        def close(sym: str, px_raw: float, t, reason: str):
            nonlocal realized
            p = pos.pop(sym)
            px = sell_price(sym, px_raw)
            pnl = px * p["qty"] - sell_fees(px * p["qty"], p["qty"]) - p["cost"]
            realized += pnl
            trades.append({"date": day, "symbol": sym, "entry_time": p["time"], "exit_time": t,
                           "entry_price": p["entry"], "exit_price": px, "qty": p["qty"], "pnl": pnl, "exit_reason": reason})

        for t in sorted(set().union(*(b.keys() for b in bars.values())) if bars else []):
            if t >= flatten_at:
                for sym in list(pos):
                    bar = bars[sym].get(t)
                    close(sym, bar[0] if bar else last_px[sym], t, "eodflat")
                break
            # entries: a setup that completed at t fills at this bar's open
            if not stop_reason and t in candidates:
                unreal = sum(p["qty"] * sell_price(s, last_px.get(s, p["entry"])) - p["cost"] for s, p in pos.items())
                ok, _ = risk.can_enter(t.to_pydatetime(), sess["open"], sess["close"], n_trades, realized + unreal, base, False)
                if ok:
                    sigs = [strategy.make_signal(sym, row, params)[0] for sym, row in candidates[t] if sym not in pos]
                    sigs = [s for s in sigs if s and t in bars.get(s.symbol, {})]
                    if sigs:
                        sig = max(sigs, key=lambda s: s.score)
                        entry = buy_price(sig.symbol, bars[sig.symbol][t][0])
                        pos[sig.symbol] = {"entry": entry, "qty": notional / entry, "cost": notional, "time": t,
                                           "stop": sig.stop, "tp": sig.take_profit}
                        n_trades += 1
            # exits on this bar
            for sym in list(pos):
                bar = bars[sym].get(t)
                if not bar:
                    continue
                o, h, l, c = bar
                p = pos[sym]
                if l <= p["stop"]:
                    close(sym, min(o, p["stop"]), t, "stop")
                elif h >= p["tp"]:
                    close(sym, max(o, p["tp"]), t, "tp")
            for sym, b in bars.items():
                if t in b:
                    last_px[sym] = b[t][3]
            unreal = sum(p["qty"] * sell_price(s, last_px.get(s, p["entry"])) - p["cost"] for s, p in pos.items())
            if not stop_reason and (r := risk.day_stop_reason(realized + unreal, base)):
                stop_reason = r
                for sym in list(pos):
                    close(sym, last_px[sym], t, "daystop")
        for sym in list(pos):  # data ended early
            close(sym, last_px[sym], "end", "eodflat")

        daily.append({"date": day, "base_unit": base, "day_pnl": realized, "trades": n_trades, "stop_reason": stop_reason})
        if scaling:
            risk.end_of_day(state, realized, traded=n_trades > 0)
            if risk.kill_check(state, 1.0, 0.0):
                daily[-1]["stop_reason"] = "KILL SWITCH"
                break
    return pd.DataFrame(daily), pd.DataFrame(trades)


def folds(start: date, end: date, train_months: int, test_months: int) -> list[tuple[date, date, date, date]]:
    out, t0 = [], pd.Timestamp(start)
    while True:
        tr_end = t0 + pd.DateOffset(months=train_months) - pd.Timedelta(days=1)
        te_end = tr_end + pd.DateOffset(months=test_months)
        if te_end.date() > end:
            break
        out.append((t0.date(), tr_end.date(), (tr_end + pd.Timedelta(days=1)).date(), te_end.date()))
        t0 += pd.DateOffset(months=test_months)
    return out


def _window(df: pd.DataFrame, a: date, b: date) -> pd.DataFrame:
    return df[(df["date"] >= a) & (df["date"] <= b)] if not df.empty else df


def walk_forward(m: Market, start: date, end: date, train_months: int, test_months: int) -> None:
    combos = [dict(strategy.PARAMS, **dict(zip(GRID, v))) for v in itertools.product(*GRID.values())]
    print(f"Simulating {len(combos)} parameter sets...")
    runs = [simulate(m, p) for p in combos]
    default = simulate(m, strategy.PARAMS)

    rows, oos_d, oos_t, def_d, def_t = [], [], [], [], []
    for tr_a, tr_b, te_a, te_b in folds(start, end, train_months, test_months):
        def score(i):
            d, t = _window(runs[i][0], tr_a, tr_b), _window(runs[i][1], tr_a, tr_b)
            return d["day_pnl"].sum() if len(t) >= MIN_TRAIN_TRADES else -math.inf
        best = max(range(len(combos)), key=score)
        d, t = _window(runs[best][0], te_a, te_b), _window(runs[best][1], te_a, te_b)
        oos_d.append(d), oos_t.append(t)
        def_d.append(_window(default[0], te_a, te_b)), def_t.append(_window(default[1], te_a, te_b))
        p = combos[best]
        rows.append(f"| {tr_a}..{tr_b} | {te_a}..{te_b} | rsi<{p['rsi_entry']:.0f}, dev {p['min_vwap_dev']:.1%}, "
                    f"stop {p['stop_atr']}xATR | ${score(best):.2f} | ${d['day_pnl'].sum():.2f} | {len(t)} |")

    if not rows:
        print("Range too short for one fold; widen --start/--end or shrink the windows.")
        return
    print("\n## Walk-forward folds (parameters chosen on train, scored on the unseen test window)\n")
    print("| train | test | chosen params | train P&L | test P&L | test trades |\n|---|---|---|---|---|---|")
    print("\n".join(rows))
    cat = lambda xs: pd.concat([x for x in xs if not x.empty]) if any(not x.empty for x in xs) else pd.DataFrame(
        columns=["date", "day_pnl", "trades", "stop_reason", "pnl"])
    print("\n## Out-of-sample, walk-forward selected ($10 base, no scaling)\n")
    print("\n".join(format_stats(stats(cat(oos_d), cat(oos_t)))))
    print("\n## Out-of-sample, fixed current PARAMS (same test windows)\n")
    print("\n".join(format_stats(stats(cat(def_d), cat(def_t)))))


def main() -> None:
    today = date.today()
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat, default=date(today.year - 2, today.month, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=today.replace(day=1) - timedelta(days=1))
    parser.add_argument("--train-months", type=int, default=6)
    parser.add_argument("--test-months", type=int, default=2)
    parser.add_argument("--fixed", action="store_true", help="current PARAMS with the scaling ladder, no walk-forward")
    args = parser.parse_args()

    warmup = args.start - timedelta(days=7)  # indicator history before the first session
    print(f"Loading IEX minute bars {warmup}..{args.end} for {', '.join(config.UNIVERSE)} (cached after first run)...")
    m = Market(data.history(config.UNIVERSE, warmup, args.end), data.calendar(args.start, args.end))
    if args.fixed:
        d, t = simulate(m, strategy.PARAMS, scaling=True)
        print("\n## Current PARAMS, full rules incl. scaling\n")
        print("\n".join(format_stats(stats(d, t))))
        print(f"\nFinal base unit: ${d['base_unit'].iloc[-1]:.0f}" if not d.empty else "")
    else:
        walk_forward(m, args.start, args.end, args.train_months, args.test_months)


if __name__ == "__main__":
    main()
