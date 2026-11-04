"""Decides whether GitHub's backup cron should skip Moola's daily run.

The scheduler Worker starts the run at 21:30 UTC; GitHub's own cron fires the same workflow hours
later as a backup. The backup must not trade twice for the same session. It skips when a run
already started after the anchor, where the anchor is:
  - the close of the last completed trading session, if the most recent 21:30 UTC slot fell on a
    trading day (so a backup that starts after midnight New York time still sees the right session);
  - otherwise that slot itself, so weekend/holiday crypto-only runs still happen once a day.

    python -m moola.backup_guard   # exit 0 = run, exit 10 = skip
"""
import sys
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from moola.logger import EQUITY, _read

ET = ZoneInfo("America/New_York")
SLOT_UTC = time(21, 30)
SKIP = 10


def last_slot(now: datetime) -> datetime:
    slot = datetime.combine(now.date(), SLOT_UTC, timezone.utc)
    return slot if slot <= now else slot - timedelta(days=1)


def anchor(now: datetime, closes: dict[date, datetime]) -> datetime:
    """closes: session date (ET) -> tz-aware close time."""
    slot = last_slot(now)
    close = closes.get(slot.astimezone(ET).date())
    return close if close is not None and close <= now else slot


def should_skip(last_run: datetime | None, now: datetime, closes: dict[date, datetime]) -> tuple[bool, str]:
    a = anchor(now, closes).astimezone(timezone.utc)
    if last_run is not None and last_run >= a:
        return True, f"a run already started at {last_run:%Y-%m-%d %H:%M} UTC, after the anchor {a:%Y-%m-%d %H:%M} UTC"
    return False, f"no run since the anchor {a:%Y-%m-%d %H:%M} UTC (last run: {last_run or 'never'})"


def _session_closes(now: datetime) -> dict[date, datetime]:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import GetCalendarRequest

    from moola.config import alpaca_keys

    key, secret = alpaca_keys()
    cal = TradingClient(key, secret, paper=True).get_calendar(
        GetCalendarRequest(start=now.date() - timedelta(days=10), end=now.date()))
    return {c.date: (c.close.replace(tzinfo=ET) if c.close.tzinfo is None else c.close) for c in cal}


def main() -> int:
    now = datetime.now(timezone.utc)
    eq = _read(EQUITY)
    last_run = pd.Timestamp(eq["run_time_utc"].iloc[-1]).to_pydatetime() if not eq.empty else None
    skip, why = should_skip(last_run, now, _session_closes(now))
    print(("SKIP: " if skip else "RUN: ") + why)
    return SKIP if skip else 0


if __name__ == "__main__":
    sys.exit(main())
