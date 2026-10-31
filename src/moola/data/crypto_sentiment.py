"""Crypto Fear & Greed index from alternative.me (no API key needed)."""
import pandas as pd
import requests

FNG_URL = "https://api.alternative.me/fng/"


def fetch_history() -> pd.Series:
    resp = requests.get(FNG_URL, params={"limit": 0, "format": "json"}, timeout=30)
    resp.raise_for_status()
    rows = resp.json()["data"]
    s = pd.Series({pd.Timestamp(int(r["timestamp"]), unit="s").normalize(): int(r["value"]) for r in rows})
    return s.sort_index().rename("fear_greed")


def asof(history: pd.Series, day: pd.Timestamp) -> int | None:
    past = history[history.index <= day]
    return int(past.iloc[-1]) if not past.empty else None
