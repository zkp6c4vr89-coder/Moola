from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from moola.backup_guard import should_skip

ET = ZoneInfo("America/New_York")
UTC = timezone.utc


def closes(*days: str, early: str | None = None) -> dict:
    out = {date.fromisoformat(d): datetime.fromisoformat(f"{d}T16:00").replace(tzinfo=ET) for d in days}
    if early:
        out[date.fromisoformat(early)] = datetime.fromisoformat(f"{early}T13:00").replace(tzinfo=ET)
    return out


def utc(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


# Week of Mon 2 - Fri 6 Nov 2026 (EST: close = 21:00 UTC)
WEEK = closes("2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06")


def test_weekday_worker_ran_backup_skips():
    assert should_skip(utc("2026-11-04T21:31"), utc("2026-11-05T01:40"), WEEK)[0]


def test_weekday_worker_missed_backup_runs():
    assert not should_skip(utc("2026-11-03T21:31"), utc("2026-11-05T01:40"), WEEK)[0]


def test_backup_after_midnight_new_york_still_keys_on_that_session():
    # 05:30 UTC Thu = 00:30 ET Thu; Wednesday's session is the one that matters
    assert should_skip(utc("2026-11-04T21:31"), utc("2026-11-05T05:30"), WEEK)[0]
    assert not should_skip(utc("2026-11-03T21:31"), utc("2026-11-05T05:30"), WEEK)[0]


def test_saturday_crypto_run_is_not_skipped_because_friday_ran():
    assert not should_skip(utc("2026-11-06T21:31"), utc("2026-11-08T01:00"), WEEK)[0]
    assert should_skip(utc("2026-11-07T21:31"), utc("2026-11-08T01:00"), WEEK)[0]


def test_edt_session_and_half_day():
    edt = closes("2026-07-07")
    assert should_skip(utc("2026-07-07T21:31"), utc("2026-07-08T02:00"), edt)[0]  # close 20:00 UTC in EDT
    half = closes(early="2026-11-27")                                         # day after Thanksgiving
    assert should_skip(utc("2026-11-27T21:31"), utc("2026-11-28T01:00"), half)[0]
    assert not should_skip(utc("2026-11-25T21:31"), utc("2026-11-28T01:00"), half)[0]


def test_first_ever_run():
    assert not should_skip(None, utc("2026-11-05T01:40"), WEEK)[0]
