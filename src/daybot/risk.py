"""Capital scaling, daily limits and kill switch. Pure functions over a small state dict."""
import math
from datetime import datetime, timedelta

from daybot import config


def new_state() -> dict:
    return {
        "level": 0,                 # index into config.LADDER
        "level_pnl": 0.0,           # P&L accumulated since this level started
        "streak": 0,                # consecutive positive trading days at this level
        "account_peak": None,
        "virtual_peak": None,
        "killed": None,             # {"time", "reason"} once the kill switch fires; a human clears it
        "consecutive_errors": 0,
        "day_stopped": None,        # {"date", "reason"}
        "last_eod": None,
        "open_trades": {},          # symbol -> {entry, qty, stop, take_profit, stop_order_id, ...}
    }


def base_unit(state: dict) -> float:
    return float(config.LADDER[state["level"]])


def virtual_balance(state: dict, day_pnl: float = 0.0) -> float:
    return base_unit(state) + state["level_pnl"] + day_pnl


def position_notional(state: dict) -> float:
    return round(config.MAX_POSITION_FRACTION * base_unit(state), 2)


def sell_fees(proceeds: float, qty: float) -> float:
    """SEC + FINRA TAF on a sell, each rounded up to the cent as Alpaca bills them. Paper charges none."""
    sec = math.ceil(proceeds * config.SEC_FEE_RATE * 100) / 100
    taf = math.ceil(min(qty * config.TAF_PER_SHARE, config.TAF_MAX) * 100) / 100
    return sec + taf + config.COMMISSION_PER_ORDER


def day_stop_reason(day_pnl: float, base: float) -> str | None:
    if day_pnl >= config.DAILY_PROFIT_CEILING * base:
        return f"profit ceiling reached (${day_pnl:.2f} >= ${config.DAILY_PROFIT_CEILING * base:.2f})"
    if day_pnl <= -config.DAILY_LOSS_LIMIT * base:
        return f"daily loss limit hit (${day_pnl:.2f} <= -${config.DAILY_LOSS_LIMIT * base:.2f})"
    return None


def entry_window(now: datetime, session_open: datetime, session_close: datetime) -> tuple[bool, str]:
    if now < session_open + timedelta(minutes=config.SKIP_OPEN_MINUTES):
        return False, f"first {config.SKIP_OPEN_MINUTES} minutes after the open"
    if now.time() > config.LAST_ENTRY or now >= session_close - timedelta(minutes=config.FLATTEN_BEFORE_CLOSE_MINUTES):
        return False, "past last entry time"
    return True, "ok"


def can_enter(now: datetime, session_open: datetime, session_close: datetime, trades_today: int,
              day_pnl: float, base: float, stopped_today: bool) -> tuple[bool, str]:
    if stopped_today:
        return False, "stopped for the day"
    ok, why = entry_window(now, session_open, session_close)
    if not ok:
        return False, why
    if trades_today >= config.MAX_TRADES_PER_DAY:
        return False, f"max {config.MAX_TRADES_PER_DAY} trades reached"
    reason = day_stop_reason(day_pnl, base)
    return (False, reason) if reason else (True, "ok")


def kill_check(state: dict, account_equity: float, day_pnl: float) -> str | None:
    """Update peaks and return a reason if either the account or the bot's balance is down 30% from peak."""
    virtual = virtual_balance(state, day_pnl)
    state["account_peak"] = max(state["account_peak"] or account_equity, account_equity)
    state["virtual_peak"] = max(state["virtual_peak"] or virtual, virtual)
    if account_equity <= state["account_peak"] * (1 - config.KILL_DRAWDOWN):
        return f"account equity ${account_equity:,.2f} is 30%+ below peak ${state['account_peak']:,.2f}"
    if virtual <= state["virtual_peak"] * (1 - config.KILL_DRAWDOWN):
        return f"bot balance ${virtual:.2f} is 30%+ below peak ${state['virtual_peak']:.2f}"
    return None


def end_of_day(state: dict, day_pnl: float, traded: bool) -> list[str]:
    """Apply one day's result to the scaling ladder. Days without trades neither extend nor reset the streak."""
    events = []
    base = base_unit(state)
    state["level_pnl"] = round(state["level_pnl"] + day_pnl, 6)
    if traded:
        state["streak"] = state["streak"] + 1 if day_pnl > 0 else 0

    if state["level_pnl"] <= -config.SCALE_DOWN_DRAWDOWN * base:
        if state["level"] > 0:
            state["level"] -= 1
            events.append(f"step down to ${base_unit(state):.0f} (level P&L ${state['level_pnl']:.2f})")
        else:
            events.append(f"bottom level lost 25% (${state['level_pnl']:.2f}); restarting the $10 level")
        state.update(level_pnl=0.0, streak=0, virtual_peak=base_unit(state))
    elif state["streak"] >= config.SCALE_UP_STREAK and state["level"] < len(config.LADDER) - 1:
        state["level"] += 1
        events.append(f"step up to ${base_unit(state):.0f} after {config.SCALE_UP_STREAK} positive days")
        state.update(level_pnl=0.0, streak=0, virtual_peak=base_unit(state))
    return events
