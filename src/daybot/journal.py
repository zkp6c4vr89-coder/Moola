"""Persistence: state.json, CSV logs and the text log file under logs/daybot/."""
import csv
import json
import logging
import sys
from datetime import date, datetime

from daybot import config, risk

def decisions_file(day: date) -> str:
    return f"decisions/{day.isoformat()}.csv"   # one row per symbol per loop


ORDERS = "orders.csv"         # every order submitted, with fill details
TRADES = "trades.csv"         # completed round trips (entry + exit)
DAILY = "daily_pnl.csv"       # one row per trading day


def setup_logging(day: date) -> logging.Logger:
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("daybot")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        fmt = logging.Formatter("%(asctime)s ET %(levelname)s %(message)s")
        fmt.converter = lambda secs: datetime.fromtimestamp(secs, config.ET).timetuple()  # market time, not the Mac's
        for h in (logging.FileHandler(config.LOG_DIR / f"{day.isoformat()}.log"), logging.StreamHandler(sys.stdout)):
            h.setFormatter(fmt)
            logger.addHandler(h)
    return logger


def load_state() -> dict:
    state = risk.new_state()
    if config.STATE_FILE.exists():
        state.update(json.loads(config.STATE_FILE.read_text()))
    return state


def save_state(state: dict) -> None:
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    config.STATE_FILE.write_text(json.dumps(state, indent=2, default=str))


def append(name: str, rows: list[dict]) -> None:
    if not rows:
        return
    path = config.LOG_DIR / name
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_header = None
    if path.exists() and path.stat().st_size:
        with open(path) as f:
            existing_header = next(csv.reader(f))
    fields = existing_header or list(dict.fromkeys(k for r in rows for k in r))
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if not existing_header:
            w.writeheader()
        w.writerows(rows)


def read(name: str) -> list[dict]:
    path = config.LOG_DIR / name
    if not path.exists() or not path.stat().st_size:
        return []
    with open(path) as f:
        return list(csv.DictReader(f))
