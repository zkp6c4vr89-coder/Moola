"""Daily summary: P&L, win rate, max drawdown. Writes logs/daybot/summary.md.

    python -m daybot.report
"""
from datetime import datetime

import pandas as pd

from daybot import config, journal, risk


def stats(daily: pd.DataFrame, trades: pd.DataFrame) -> dict:
    """daily needs day_pnl (+ optional stop_reason); trades needs pnl."""
    pnl = daily["day_pnl"].astype(float) if not daily.empty else pd.Series(dtype=float)
    tp = trades["pnl"].astype(float) if not trades.empty else pd.Series(dtype=float)
    cum = pnl.cumsum()
    wins, losses = tp[tp > 0], tp[tp < 0]
    stops = daily.get("stop_reason", pd.Series(dtype=str)).fillna("").astype(str)
    return {
        "days": len(pnl),
        "days_traded": int((daily["trades"].astype(int) > 0).sum()) if "trades" in daily else 0,
        "total_pnl": float(pnl.sum()),
        "trades": len(tp),
        "win_rate": float(len(wins) / len(tp)) if len(tp) else 0.0,
        "avg_win": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss": float(losses.mean()) if len(losses) else 0.0,
        "profit_factor": float(wins.sum() / -losses.sum()) if len(losses) and losses.sum() else float("inf") if len(wins) else 0.0,
        "max_drawdown": float((cum - cum.cummax().clip(lower=0)).min()) if len(cum) else 0.0,
        "best_day": float(pnl.max()) if len(pnl) else 0.0,
        "worst_day": float(pnl.min()) if len(pnl) else 0.0,
        "days_at_ceiling": int(stops.str.startswith("profit ceiling").sum()),
        "days_at_loss_limit": int(stops.str.startswith("daily loss").sum()),
    }


def format_stats(s: dict) -> list[str]:
    return [
        f"- **Total P&L:** ${s['total_pnl']:.2f} over {s['days']} sessions ({s['days_traded']} with trades)",
        f"- **Trades:** {s['trades']}, win rate {s['win_rate']:.0%}, avg win ${s['avg_win']:.3f}, "
        f"avg loss ${s['avg_loss']:.3f}, profit factor {s['profit_factor']:.2f}",
        f"- **Max drawdown:** ${s['max_drawdown']:.2f}; best day ${s['best_day']:.2f}, worst day ${s['worst_day']:.2f}",
        f"- **Days stopped at profit ceiling / loss limit:** {s['days_at_ceiling']} / {s['days_at_loss_limit']}",
    ]


def write() -> None:
    daily = pd.DataFrame(journal.read(journal.DAILY))
    trades = pd.DataFrame(journal.read(journal.TRADES))
    state = journal.load_state()
    out = ["# Day-trader summary", "", f"_Generated {datetime.now(config.ET):%Y-%m-%d %H:%M} ET by `daybot.report`._", "",
           f"- **Base unit:** ${risk.base_unit(state):.0f} (ladder {config.LADDER})",
           f"- **Positive-day streak:** {state['streak']}/{config.SCALE_UP_STREAK}; level P&L ${state['level_pnl']:.2f}",
           f"- **Kill switch:** {state['killed'] or 'not triggered'}", ""]
    if daily.empty:
        out.append("No trading days recorded yet.")
    else:
        # Headline numbers are fee-adjusted: paper fills are free, real sells are not.
        adj_daily = daily.assign(day_pnl=daily["day_pnl_after_fees"])
        adj_trades = trades.assign(pnl=trades["pnl_after_fees"]) if not trades.empty else trades
        raw_total = daily["day_pnl"].astype(float).sum()
        out += ["## Totals (after estimated fees)", ""] + format_stats(stats(adj_daily, adj_trades))
        out.append(f"- **Raw paper P&L (no fees):** ${raw_total:.2f}; estimated fees "
                   f"${daily['est_fees'].astype(float).sum():.2f}")
        out += ["", "## Recent days", "",
                "| date | P&L after fees | raw P&L | % of base | trades | wins | stop | base after | streak | events |",
                "|---|---|---|---|---|---|---|---|---|---|"]
        for _, d in daily.tail(20).iloc[::-1].iterrows():
            out.append(f"| {d['date']} | ${float(d['day_pnl_after_fees']):.3f} | ${float(d['day_pnl']):.3f} | "
                       f"{float(d['pct_of_base']):.2%} | {d['trades']} | "
                       f"{d['wins']} | {d['stop_reason'] or '-'} | ${float(d['base_after']):.0f} | {d['streak']} | "
                       f"{d['events'] or '-'} |")
    if not trades.empty:
        out += ["", "## Recent trades", "", "| date | symbol | entry | exit | qty | raw P&L | est. fees | P&L after fees | exit reason |",
                "|---|---|---|---|---|---|---|---|---|"]
        for _, t in trades.tail(15).iloc[::-1].iterrows():
            out.append(f"| {t['date']} | {t['symbol']} | {float(t['entry_price']):.2f} | {float(t['exit_price']):.2f} | "
                       f"{float(t['qty']):.5f} | ${float(t['pnl']):.4f} | ${float(t['est_fees']):.2f} | "
                       f"${float(t['pnl_after_fees']):.4f} | {t['exit_reason']} |")
    out += ["", "_Fees are estimated (SEC + FINRA TAF, each rounded up to the cent) because paper charges none. "
            "The scaling ladder counts fee-adjusted P&L only._"]
    (config.LOG_DIR / "summary.md").write_text("\n".join(out) + "\n")


if __name__ == "__main__":
    write()
    print(f"Wrote {config.LOG_DIR / 'summary.md'}")
