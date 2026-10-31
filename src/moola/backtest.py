"""Backtester. PROTECTED: reviews run it but must not edit it.

Replays a strategy file through the same decide -> risk.enforce -> plan_orders path as the live bot.
Orders decided on day d fill at the next open of that symbol (stocks: next session, crypto: next day).

    python -m moola.backtest                                   # current strategy, train + validation
    python -m moola.backtest --strategy candidates/v2.py       # compare a candidate against current
    python -m moola.backtest --holdout                         # ONLY when the human explicitly asks
"""
import argparse
import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd

from moola import risk
from moola.config import ROOT, is_crypto, load_config
from moola.data import crypto_sentiment, macro, prices
from moola.metrics import summarize
from moola.orders import plan_orders
from moola.types import MarketSnapshot, Order, Portfolio


def data_end(cfg: dict, holdout: bool) -> pd.Timestamp:
    """The last date the backtester may load. The holdout window is invisible unless asked for."""
    b = cfg["backtest"]
    return pd.Timestamp(b["holdout_end"] if holdout else b["validation_end"])


def splits(cfg: dict, holdout: bool) -> dict[str, tuple[pd.Timestamp, pd.Timestamp]]:
    b = cfg["backtest"]
    out = {
        "train": (pd.Timestamp(b["start"]), pd.Timestamp(b["train_end"])),
        "validation": (pd.Timestamp(b["train_end"]) + pd.Timedelta(days=1), pd.Timestamp(b["validation_end"])),
    }
    if holdout:
        out["holdout"] = (pd.Timestamp(b["validation_end"]) + pd.Timedelta(days=1), pd.Timestamp(b["holdout_end"]))
    return out


def load_strategy(path: Path):
    spec = importlib.util.spec_from_file_location(f"strategy_{abs(hash(str(path)))}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def simulate(bars: dict[str, pd.DataFrame], decide, cfg: dict, start: pd.Timestamp, end: pd.Timestamp,
             macro_daily: pd.DataFrame | None = None, fng_daily: pd.Series | None = None,
             initial_cash: float = 100_000.0) -> dict:
    cost = cfg["backtest"]["cost_bps"] / 1e4
    bars = {s: df[df.index <= end] for s, df in bars.items()}
    dates = sorted(d for d in set().union(*(df.index for df in bars.values())) if start <= d <= end)
    closes = pd.DataFrame({s: df["close"] for s, df in bars.items()}).sort_index().ffill()
    opens = pd.DataFrame({s: df["open"] for s, df in bars.items()}).sort_index()

    cash, qty = initial_cash, {s: 0.0 for s in bars}
    pending: dict[str, Order] = {}
    equity, traded, weights = {}, 0.0, {}
    peak = initial_cash

    for d in dates:
        # Fill pending orders for symbols that actually trade today.
        for sym in [s for s in pending if d in opens.index and not np.isnan(opens.at[d, s])]:
            o, px = pending.pop(sym), float(opens.at[d, sym])
            if o.side == "sell":
                shares = qty[sym] if o.close_all else min(qty[sym], o.notional / px)
                qty[sym] -= shares
                cash += shares * px * (1 - cost)
                traded += shares * px
            else:
                qty[sym] += o.notional / px
                cash -= o.notional * (1 + cost)
                traded += o.notional

        row = closes.loc[d] if d in closes.index else closes[closes.index <= d].iloc[-1]
        values = {s: qty[s] * float(row[s]) for s in bars if qty[s] > 1e-12 and not np.isnan(row[s])}
        eq = cash + sum(values.values())
        equity[d] = eq
        peak = max(peak, eq)
        weights[d] = {s: v / eq for s, v in values.items()}

        snap_bars = {s: df.iloc[: df.index.searchsorted(d, side="right")] for s, df in bars.items()}
        snapshot = MarketSnapshot(
            asof=d.date(),
            bars={s: b for s, b in snap_bars.items() if not b.empty},
            macro=({k: (None if pd.isna(v) else float(v)) for k, v in macro_daily.loc[d].items()}
                   if macro_daily is not None and d in macro_daily.index else {}),
            crypto_fear_greed=(int(fng_daily.loc[d]) if fng_daily is not None and d in fng_daily.index
                               and not pd.isna(fng_daily.loc[d]) else None),
        )
        portfolio = Portfolio(equity=eq, cash=cash, positions=values, peak_equity=peak)
        targets, _ = risk.enforce(decide(snapshot, portfolio).targets, portfolio, cfg)
        stock_session = any(d in df.index for s, df in bars.items() if not is_crypto(s))
        last_close = {s: float(row[s]) for s in bars if not np.isnan(row[s])}
        new_orders = plan_orders(targets, portfolio, last_close, cfg, stock_session)
        if stock_session:  # like the live bot, a stock session replaces any stale orders
            pending = {s: o for s, o in pending.items() if is_crypto(s)}
        pending.update({o.symbol: o for o in new_orders})

    eq_series = pd.Series(equity)
    years = max((dates[-1] - dates[0]).days / 365, 1e-9) if dates else 1
    return {"equity": eq_series, "turnover": traded / eq_series.mean() / years if len(eq_series) else 0.0,
            "weights": weights}


def _split_stats(equity: pd.Series, window: tuple[pd.Timestamp, pd.Timestamp]) -> dict:
    return summarize(equity[(equity.index >= window[0]) & (equity.index <= window[1])])


def run(strategy_paths: list[Path], holdout: bool = False) -> dict:
    cfg = load_config()
    b, u = cfg["backtest"], cfg["universe"]
    end = data_end(cfg, holdout)
    bars = prices.fetch_history(u["stocks"], u["crypto"], b["data_start"], end.date().isoformat(), cfg["data"]["stock_feed"])

    all_dates = pd.DatetimeIndex(sorted(set().union(*(df.index for df in bars.values()))))
    try:
        macro_daily = macro.fetch_history(cfg["data"]["fred_series"]).reindex(all_dates, method="ffill")
    except Exception as e:
        print(f"warning: macro history unavailable ({e})")
        macro_daily = None
    try:
        fng_daily = crypto_sentiment.fetch_history().reindex(all_dates, method="ffill")
    except Exception as e:
        print(f"warning: fear & greed history unavailable ({e})")
        fng_daily = None

    windows = splits(cfg, holdout)
    results = {}
    for path in strategy_paths:
        mod = load_strategy(path)
        sim = simulate(bars, mod.decide, cfg, pd.Timestamp(b["start"]), end, macro_daily, fng_daily)
        results[f"{path.name} ({mod.VERSION})"] = {
            **{name: _split_stats(sim["equity"], w) for name, w in windows.items()},
            "turnover_per_year": round(sim["turnover"], 2),
        }
    for sym in (cfg["benchmarks"]["stock"], cfg["benchmarks"]["crypto"]):
        if sym in bars:
            close = bars[sym]["close"].reindex(all_dates).ffill()  # same calendar as the strategy's equity
            results[f"buy & hold {sym}"] = {name: _split_stats(close, w) for name, w in windows.items()}
    return results


def to_markdown(results: dict) -> str:
    lines = ["| strategy | split | return | CAGR | Sharpe | max DD | turnover/yr |", "|---|---|---|---|---|---|---|"]
    for name, res in results.items():
        for split, s in res.items():
            if split == "turnover_per_year":
                continue
            lines.append(f"| {name} | {split} | {s['total_return']:.1%} | {s['cagr']:.1%} | {s['sharpe']:.2f} | "
                         f"{s['max_drawdown']:.1%} | {res.get('turnover_per_year', '-')} |")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy", action="append", type=Path, default=[],
                        help="extra strategy file(s) to compare against src/moola/strategy.py")
    parser.add_argument("--holdout", action="store_true", help="include the held-out window. Human request only.")
    parser.add_argument("--json", type=Path, help="also write raw results to this file")
    args = parser.parse_args()
    paths = [ROOT / "src/moola/strategy.py"] + args.strategy
    results = run(paths, args.holdout)
    print(to_markdown(results))
    if args.json:
        args.json.write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
