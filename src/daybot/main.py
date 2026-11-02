"""Day-trader entry point.

    python -m daybot.main run                     # local: 9:45 ET -> close, then end-of-day
    python -m daybot.main run --session morning   # GitHub job 1: until 12:45 ET, flat at the handoff
    python -m daybot.main run --session afternoon # GitHub job 2: 12:45 ET -> close, then end-of-day
    python -m daybot.main run --dry-run           # scan and log decisions, place no orders
    python -m daybot.main eod                     # end-of-day bookkeeping only (safe to repeat)
    python -m daybot.main status
    python -m daybot.main reset-kill              # human only: clear a fired kill switch
"""
import argparse
import logging
import sys
import time as _time
from datetime import datetime, timedelta

import pandas as pd

from daybot import config, data, journal, report, risk, strategy
from daybot.execution import Broker

log = logging.getLogger("daybot")


class KillSwitch(Exception):
    pass


def now_et() -> datetime:
    return datetime.now(config.ET)


def sleep_until(t: datetime) -> None:
    while (left := (t - now_et()).total_seconds()) > 0:
        _time.sleep(min(left, 30))


def next_loop(now: datetime) -> datetime:
    return now.replace(second=config.LOOP_OFFSET_SECONDS, microsecond=0) + timedelta(seconds=config.LOOP_SECONDS)


class Trader:
    def __init__(self, session: str = "full", dry_run: bool = False):
        self.session, self.dry_run = session, dry_run
        self.state = journal.load_state()
        self.broker = Broker()
        self.today = now_et().date()
        cal = data.calendar(self.today - timedelta(days=10), self.today)
        self.cal = next((c for c in cal if c["date"] == self.today), None)
        prior = [c for c in cal if c["date"] < self.today]
        self.prev_open = prior[-1]["open"] if prior else None

    # --- helpers -------------------------------------------------------------------
    def stamp(self) -> str:
        return now_et().strftime("%Y%m%d%H%M%S")

    def stopped_today(self) -> bool:
        st = self.state.get("day_stopped")
        return bool(st and st.get("date") == self.today.isoformat())

    def record_order(self, kind: str, symbol: str, order=None, error: str | None = None, **extra) -> None:
        row = {"time": now_et().isoformat(timespec="seconds"), "kind": kind, "symbol": symbol,
               "client_order_id": getattr(order, "client_order_id", None),
               "status": getattr(getattr(order, "status", None), "value", None),
               "filled_qty": getattr(order, "filled_qty", None),
               "filled_avg_price": getattr(order, "filled_avg_price", None),
               "notional": extra.pop("notional", None), "stop": extra.pop("stop", None),
               "take_profit": extra.pop("take_profit", None), "reason": extra.pop("reason", ""), "error": error}
        journal.append(journal.ORDERS, [row])
        log.info("order %s %s: %s %s", kind, symbol, row["status"], error or row["reason"])

    def window(self) -> tuple[datetime, datetime]:
        open_, close = self.cal["open"], self.cal["close"]
        start = open_ + timedelta(minutes=config.SKIP_OPEN_MINUTES)
        handoff = open_.replace(hour=config.HANDOFF.hour, minute=config.HANDOFF.minute)
        return {"morning": (start, handoff), "afternoon": (handoff, close), "full": (start, close)}[self.session]

    # --- trading actions -------------------------------------------------------------
    def exit(self, symbol: str, reason: str, price: float | None = None) -> None:
        tr = self.state["open_trades"].get(symbol, {})
        if tr.get("stop_order_id"):
            self.broker.cancel(tr["stop_order_id"])
            _time.sleep(1)
        pos = self.broker.positions().get(symbol)
        if pos:
            o = self.broker.sell(symbol, pos["qty"], f"db-{reason}-{symbol}-{self.stamp()}")
            self.record_order(reason, symbol, o, reason=f"trigger price {price}")
        self.state["open_trades"].pop(symbol, None)
        journal.save_state(self.state)

    def flatten(self, reason: str) -> None:
        for o in self.broker.flatten(reason, self.stamp()):
            self.record_order(reason, o.symbol, o, reason="flatten")
        self.state["open_trades"] = {}
        journal.save_state(self.state)

    def enter(self, sig: strategy.Signal) -> None:
        notional = risk.position_notional(self.state)
        if self.dry_run:
            log.info("[dry run] would buy $%.2f %s (%s)", notional, sig.symbol, sig.reason)
            return
        bid, ask = data.latest_quote(sig.symbol)
        spread = (ask - bid) / ((ask + bid) / 2) * 1e4 if bid > 0 and ask > 0 else float("inf")
        if spread > config.MAX_SPREAD_BPS:
            self.record_order("skip", sig.symbol, reason=f"spread {spread:.1f} bps > {config.MAX_SPREAD_BPS}")
            return
        ok, why = self.broker.preflight(sig.symbol, notional)
        if not ok:
            self.record_order("skip", sig.symbol, reason=f"preflight: {why}")
            return
        stamp = self.stamp()
        o = self.broker.buy(sig.symbol, notional, f"db-entry-{sig.symbol}-{stamp}")
        self.record_order("entry", sig.symbol, o, notional=notional, stop=sig.stop, take_profit=sig.take_profit,
                          reason=sig.reason)
        if o.status.value != "filled":
            return
        qty, entry = float(o.filled_qty), float(o.filled_avg_price)
        stop_id = None
        try:
            so = self.broker.place_stop(sig.symbol, qty, sig.stop, f"db-stop-{sig.symbol}-{stamp}")
            stop_id = str(so.id)
        except Exception as e:  # the bot still enforces the stop itself every loop
            log.warning("server-side stop for %s failed (%s); using managed stop only", sig.symbol, e)
        self.state["open_trades"][sig.symbol] = {
            "date": self.today.isoformat(), "entry": entry, "qty": qty, "stop": sig.stop,
            "take_profit": sig.take_profit, "stop_order_id": stop_id, "entry_order_id": str(o.id)}
        journal.save_state(self.state)

    # --- one loop iteration ------------------------------------------------------------
    def step(self, now: datetime) -> None:
        s = self.state
        acct = self.broker.account()
        day_pnl = acct["equity"] - acct["last_equity"]
        base = risk.base_unit(s)
        if reason := risk.kill_check(s, acct["equity"], day_pnl):
            raise KillSwitch(reason)

        positions = self.broker.positions()
        for sym in list(s["open_trades"]):
            if sym not in positions:
                log.info("%s position closed on Alpaca (server-side stop filled)", sym)
                s["open_trades"].pop(sym)
        for sym in positions:
            if sym not in s["open_trades"]:
                log.warning("untracked position in %s; closing it", sym)
                self.exit(sym, "orphan")

        if s["open_trades"]:
            prices = data.latest_trades(list(s["open_trades"]))
            for sym, tr in list(s["open_trades"].items()):
                px = prices.get(sym)
                if px is not None and px <= tr["stop"]:
                    self.exit(sym, "stop", px)
                elif px is not None and px >= tr["take_profit"]:
                    self.exit(sym, "tp", px)

        if now >= self.cal["close"] - timedelta(minutes=config.FLATTEN_BEFORE_CLOSE_MINUTES):
            if self.broker.positions():
                self.flatten("eodflat")
            return

        if (reason := risk.day_stop_reason(day_pnl, base)) and not self.stopped_today():
            log.info("stopping for the day: %s", reason)
            self.flatten("daystop")
            s["day_stopped"] = {"date": self.today.isoformat(), "reason": reason}

        trades_today = self.broker.entries_today(self.cal["open"])
        ok, why = risk.can_enter(now, self.cal["open"], self.cal["close"], trades_today, day_pnl, base,
                                 self.stopped_today())
        if ok and self.session == "morning" and now >= self.window()[1] - timedelta(minutes=15):
            ok, why = False, "too close to the midday handoff"

        bars = data.minute_bars(config.UNIVERSE, self.prev_open, now)
        signals, rows = [], []
        for sym in config.UNIVERSE:
            ind = strategy.indicators(bars.get(sym, pd.DataFrame(columns=data.COLUMNS)))
            ind = ind[ind["bar_end"] <= now]  # drop the bar still forming
            sig, reason = strategy.evaluate(sym, ind)
            if sig and (now - sig.bar_end).total_seconds() > config.SIGNAL_MAX_AGE_SECONDS:
                sig, reason = None, "setup already seen on an earlier loop"
            last = ind.iloc[-1] if not ind.empty else {}
            action = "skip"
            if sig and not ok:
                reason = f"signal blocked: {why}"
            elif sig and sym in s["open_trades"]:
                reason, sig = "signal ignored: already holding", None
            elif sig:
                signals.append(sig)
                action = "candidate"
            rows.append({"time": now.isoformat(timespec="seconds"), "symbol": sym,
                         "close": last.get("close"), "vwap": last.get("vwap"), "dev": last.get("dev"),
                         "rsi": last.get("rsi"), "atr": last.get("atr"), "action": action, "reason": reason,
                         "day_pnl": round(day_pnl, 4), "trades_today": trades_today, "base_unit": base,
                         "dry_run": self.dry_run})
        if ok and signals:
            best = max(signals, key=lambda x: x.score)
            for r in rows:
                if r["symbol"] == best.symbol:
                    r["action"] = "enter"
            self.enter(best)
        journal.append(journal.decisions_file(self.today), rows)

    # --- session ---------------------------------------------------------------------------
    def kill(self, reason: str) -> None:
        log.critical("KILL SWITCH: %s", reason)
        self.state["killed"] = {"time": now_et().isoformat(), "reason": reason}
        journal.save_state(self.state)
        try:
            if not self.dry_run:
                self.flatten("kill")
        except Exception:
            log.exception("flatten during kill failed; check the Alpaca dashboard")

    def run(self) -> int:
        s = self.state
        if s.get("killed"):
            log.error("kill switch is active (%s). Run `python -m daybot.main reset-kill` after reviewing.", s["killed"])
            return 2
        if self.cal is None:
            upcoming = data.calendar(self.today, self.today + timedelta(days=10))
            nxt = next((c for c in upcoming if c["date"] > self.today), None)
            when = ""
            if nxt:
                start = nxt["open"] + timedelta(minutes=config.SKIP_OPEN_MINUTES)
                when = (f"; next session {nxt['date']:%a %Y-%m-%d}, trading from {start:%H:%M} ET "
                        f"({start.astimezone():%a %H:%M} your local time)")
            log.info("%s (New York date) is not a trading day%s", self.today.strftime("%a %Y-%m-%d"), when)
            return 0
        if self.cal["close"].hour != 16 or self.cal["close"].minute != 0:
            log.info("half-day session (closes %s); skipping", self.cal["close"].time())
            return 0
        # positions from an earlier day should never exist; treat them as orphans to close
        s["open_trades"] = {k: v for k, v in s["open_trades"].items() if v.get("date") == self.today.isoformat()}

        start, end = self.window()
        if now_et() < start:
            log.info("waiting until %s", start.time())
            sleep_until(start)
        log.info("session %s running until %s (base unit $%.0f, dry run %s)", self.session, end.time(),
                 risk.base_unit(s), self.dry_run)
        while (now := now_et()) < end:
            try:
                self.step(now)
                s["consecutive_errors"] = 0
            except KillSwitch as e:
                self.kill(str(e))
                return 2
            except Exception as e:
                s["consecutive_errors"] += 1
                log.exception("loop error %d/%d", s["consecutive_errors"], config.MAX_CONSECUTIVE_API_ERRORS)
                if s["consecutive_errors"] >= config.MAX_CONSECUTIVE_API_ERRORS:
                    self.kill(f"{s['consecutive_errors']} consecutive errors, last: {type(e).__name__}: {e}")
                    return 2
            journal.save_state(s)
            sleep_until(next_loop(now))

        if not self.dry_run and self.broker.positions():
            self.flatten("handoff" if self.session == "morning" else "eodflat")
        if self.session in ("afternoon", "full") and not self.dry_run:
            self.eod()
        journal.save_state(s)
        return 0

    def eod(self) -> None:
        s = self.state
        if s.get("last_eod") == self.today.isoformat() or self.cal is None:
            return
        acct = self.broker.account()
        day_pnl = round(acct["equity"] - acct["last_equity"], 4)
        base_before = risk.base_unit(s)
        trades = round_trips(self.broker.orders_since(self.cal["open"] - timedelta(hours=1)), self.today)
        # Paper charges no fees, so estimate what a live account would have paid. Live equity already includes them.
        for t in trades:
            t["est_fees"] = risk.sell_fees(t["exit_price"] * t["qty"], t["qty"])
            t["pnl_after_fees"] = round(t["pnl"] - (0 if self.broker.live else t["est_fees"]), 4)
        est_fees = round(sum(t["est_fees"] for t in trades), 4)
        adj_pnl = round(day_pnl - (0 if self.broker.live else est_fees), 4)
        journal.append(journal.TRADES, trades)
        events = risk.end_of_day(s, adj_pnl, traded=bool(trades))  # the ladder only counts fee-adjusted P&L
        stop = s.get("day_stopped") or {}
        journal.append(journal.DAILY, [{
            "date": self.today.isoformat(), "base_unit": base_before, "day_pnl": day_pnl,
            "est_fees": est_fees, "day_pnl_after_fees": adj_pnl,
            "pct_of_base": round(adj_pnl / base_before, 4), "trades": len(trades),
            "wins": sum(1 for t in trades if t["pnl_after_fees"] > 0),
            "stop_reason": stop.get("reason") if stop.get("date") == self.today.isoformat() else "",
            "level_after": s["level"], "base_after": risk.base_unit(s), "streak": s["streak"],
            "level_pnl": s["level_pnl"], "bot_balance": round(risk.virtual_balance(s), 4),
            "account_equity": acct["equity"], "events": "; ".join(events)}])
        for e in events:
            log.info("scaling: %s", e)
        s["last_eod"] = self.today.isoformat()
        journal.save_state(s)
        report.write()
        log.info("end of day: P&L $%.4f raw, $%.4f after est. fees, %d round trips", day_pnl, adj_pnl, len(trades))


def round_trips(orders: list, day) -> list[dict]:
    """Pair today's filled db- buys and sells per symbol in time order."""
    fills = sorted((o for o in orders if o.filled_at and float(o.filled_qty or 0) > 0
                    and (o.client_order_id or "").startswith("db-")), key=lambda o: o.filled_at)
    open_buys: dict[str, list] = {}
    out = []
    for o in fills:
        if o.side.value == "buy":
            open_buys.setdefault(o.symbol, []).append(o)
        elif open_buys.get(o.symbol):
            b = open_buys[o.symbol].pop(0)
            qty = min(float(b.filled_qty), float(o.filled_qty))
            entry, exit_ = float(b.filled_avg_price), float(o.filled_avg_price)
            out.append({"date": day.isoformat(), "symbol": o.symbol,
                        "entry_time": b.filled_at.astimezone(config.ET).isoformat(timespec="seconds"),
                        "entry_price": entry,
                        "exit_time": o.filled_at.astimezone(config.ET).isoformat(timespec="seconds"),
                        "exit_price": exit_, "qty": qty, "pnl": round((exit_ - entry) * qty, 4),
                        "exit_reason": o.client_order_id.split("-")[1]})
    return out


def main() -> int:
    parser = argparse.ArgumentParser(prog="daybot")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run")
    run.add_argument("--session", choices=["full", "morning", "afternoon"], default="full")
    run.add_argument("--dry-run", action="store_true")
    sub.add_parser("eod")
    sub.add_parser("status")
    sub.add_parser("reset-kill")
    args = parser.parse_args()
    journal.setup_logging(now_et().date())

    if args.cmd == "reset-kill":
        st = journal.load_state()
        log.info("clearing kill switch: %s", st.get("killed"))
        st.update(killed=None, consecutive_errors=0, account_peak=None, virtual_peak=None)
        journal.save_state(st)
        return 0
    if args.cmd == "status":
        st = journal.load_state()
        acct = Broker().account()
        print(f"base unit ${risk.base_unit(st):.0f} | streak {st['streak']}/{config.SCALE_UP_STREAK} | "
              f"level P&L ${st['level_pnl']:.2f} | bot balance ${risk.virtual_balance(st):.2f}")
        print(f"account equity ${acct['equity']:,.2f}, today ${acct['equity'] - acct['last_equity']:+.2f}")
        print(f"killed: {st['killed']} | open trades: {st['open_trades']}")
        return 0
    trader = Trader(getattr(args, "session", "full"), getattr(args, "dry_run", False))
    if args.cmd == "eod":
        trader.eod()
        return 0
    return trader.run()


if __name__ == "__main__":
    sys.exit(main())
