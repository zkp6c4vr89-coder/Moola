"""Daily run: fetch data, decide, enforce risk, trade on paper, log everything. PROTECTED.

    python -m moola.run_daily            # trade on the paper account
    python -m moola.run_daily --dry-run  # decide and write logs/daily/<day>.dryrun.json, no orders
"""
import argparse
import os
import traceback
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from moola import logger, report, risk, strategy
from moola.broker import PaperBroker
from moola.config import is_crypto, load_config
from moola.data import crypto_sentiment, macro, news, prices
from moola.orders import plan_orders
from moola.types import MarketSnapshot, Portfolio

ET = ZoneInfo("America/New_York")


def _try(errors: dict, name: str, fn, default=None):
    try:
        return fn()
    except Exception as e:  # one flaky source must not stop the run
        errors[name] = f"{type(e).__name__}: {e}"
        traceback.print_exc()
        return default


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    cfg = load_config()
    now = datetime.now(timezone.utc)
    now_et = now.astimezone(ET)
    day = now_et.date().isoformat()
    errors: dict[str, str] = {}
    universe = cfg["universe"]["stocks"] + cfg["universe"]["crypto"]

    broker = PaperBroker()

    # 1. Record fills for orders placed since the last run, with slippage vs the price we decided at.
    last = logger.last_run_time()
    since = datetime.fromisoformat(last) if last else now - timedelta(days=7)
    fills = _try(errors, "fills", lambda: broker.filled_orders_since(since), [])
    decided_at = logger.decision_prices()
    for f in fills:
        dp = decided_at.get(f["order_id"])
        if dp and f["filled_avg_price"]:
            sign = 1 if f["side"] == "buy" else -1  # positive slippage = we paid more / received less
            f["decision_price"] = dp
            f["slippage_bps"] = round(sign * (f["filled_avg_price"] - dp) / dp * 1e4, 1)

    # 2. Portfolio state.
    account = broker.account_summary()
    positions = broker.positions()
    portfolio = Portfolio(
        equity=account["equity"], cash=account["cash"],
        positions={p["symbol"]: p["market_value"] for p in positions},
        peak_equity=logger.peak_equity(account["equity"], cfg["risk"].get("peak_reset_date")),
    )

    # 3. Data. Prices are required; everything else is best-effort.
    d = cfg["data"]
    bars = prices.fetch_live_bars(cfg["universe"]["stocks"], cfg["universe"]["crypto"], d["history_days"], d["stock_feed"])
    macro_hist = _try(errors, "macro", lambda: macro.fetch_history(d["fred_series"]))
    fng_hist = _try(errors, "fear_greed", crypto_sentiment.fetch_history)
    snapshot = MarketSnapshot(
        asof=now_et.date(),
        bars=bars,
        macro=macro.asof(macro_hist, macro_hist.index.max()) if macro_hist is not None else {},
        crypto_fear_greed=crypto_sentiment.asof(fng_hist, fng_hist.index.max()) if fng_hist is not None else None,
        news=_try(errors, "news", lambda: news.fetch_news(universe, d["news_lookback_hours"]), {}),
    )

    # 4. Decide and enforce limits. Stocks only trade after a completed session.
    clock = broker.client.get_clock()
    stock_session = broker.was_trading_day(now_et.date()) and not clock.is_open and now_et.hour >= 16
    decision = strategy.decide(snapshot, portfolio)
    targets, flags = risk.enforce(decision.targets, portfolio, cfg)
    last_close = {s: float(df["close"].iloc[-1]) for s, df in bars.items() if not df.empty}
    orders = plan_orders(targets, portfolio, last_close, cfg, stock_session)

    # 5. Execute.
    submitted = []
    if not args.dry_run:
        if stock_session:
            _try(errors, "cancel_open_orders", broker.cancel_open_orders)  # stale orders from the last session
        for o in orders:
            row = {**asdict(o), "strategy_version": strategy.VERSION, "order_id": None, "status": None, "error": None}
            try:
                row.update(broker.submit(o))
            except Exception as e:
                row["error"] = f"{type(e).__name__}: {e}"
                errors[f"order_{o.symbol}"] = row["error"]
            submitted.append(row)

    # 6. Log.
    payload = {
        "date": day, "run_time_utc": now.isoformat(), "dry_run": args.dry_run,
        "strategy_version": strategy.VERSION, "strategy_params": strategy.PARAMS,
        "git_sha": os.environ.get("GITHUB_SHA"), "stock_session": stock_session,
        "account": account, "peak_equity": portfolio.peak_equity, "positions": positions,
        "data": {"macro": snapshot.macro, "crypto_fear_greed": snapshot.crypto_fear_greed, "news": snapshot.news,
                 "last_close": last_close},
        "indicators": decision.indicators, "rationale": decision.rationale,
        "raw_targets": decision.targets, "final_targets": targets, "risk_flags": flags,
        "orders": submitted if not args.dry_run else [asdict(o) for o in orders],
        "fills_since_last_run": fills, "errors": errors,
    }
    if args.dry_run:
        path = logger.log_daily(f"{day}.dryrun", payload)
        print(f"Dry run written to {path}")
        return

    logger.log_daily(day, payload)
    logger.log_fills(fills)
    logger.log_trades(day, submitted)
    logger.log_positions(day, positions)
    bench = cfg["benchmarks"]
    logger.log_equity({
        "date": day, "run_time_utc": now.isoformat(), "equity": account["equity"], "cash": account["cash"],
        "cum_pnl": round(account["equity"] - cfg["initial_cash"], 2),
        "gross_exposure": round(sum(portfolio.positions.values()) / account["equity"], 4),
        "crypto_exposure": round(sum(v for s, v in portfolio.positions.items() if is_crypto(s)) / account["equity"], 4),
        "bench_stock_close": last_close.get(bench["stock"]), "bench_crypto_close": last_close.get(bench["crypto"]),
        "strategy_version": strategy.VERSION, "stock_session": stock_session,
        "n_orders": len(submitted), "n_risk_flags": len(flags), "n_errors": len(errors),
    })
    report.write_summary(cfg)
    print(f"{day}: equity {account['equity']:,.2f}, {len(submitted)} orders, {len(flags)} risk flags, {len(errors)} errors")


if __name__ == "__main__":
    main()
