"""Writes the daily logs Claude Code reads during reviews. PROTECTED: reviews must not edit this file."""
import json
from pathlib import Path

import pandas as pd

from moola.config import LOGS

EQUITY = LOGS / "equity.csv"
TRADES = LOGS / "trades.csv"
FILLS = LOGS / "fills.csv"
POSITIONS = LOGS / "positions.csv"
DAILY = LOGS / "daily"


def _read(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:  # a file with no header, e.g. written on a day with zero positions
        return pd.DataFrame()


def _upsert(path: Path, rows: list[dict], day: str, key: list[str] | None = None) -> None:
    """Replace any rows for `day` (so re-running a day is idempotent), then append."""
    df = _read(path)
    if not df.empty and "date" in df:
        df = df[df["date"].astype(str) != day]
    new = pd.DataFrame(rows)
    out = pd.concat([df, new], ignore_index=True) if not df.empty else new
    if len(out.columns) == 0:
        return  # nothing logged yet and nothing to log (e.g. first day, no positions)
    if key:
        out = out.drop_duplicates(subset=key, keep="last")
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(path, index=False)


def peak_equity(current: float, reset_date: str | None = None) -> float:
    df = _read(EQUITY)
    if df.empty:
        return current
    if reset_date:
        df = df[df["date"].astype(str) >= str(reset_date)]
    return max([current] + df["equity"].tolist())


def last_run_time() -> str | None:
    df = _read(EQUITY)
    return None if df.empty else str(df["run_time_utc"].iloc[-1])


def decision_prices() -> dict[str, float]:
    df = _read(TRADES)
    if df.empty or "order_id" not in df:
        return {}
    return dict(zip(df["order_id"].astype(str), df["decision_price"]))


def log_equity(row: dict) -> None:
    df = _read(EQUITY)
    prev = df[df["date"].astype(str) < row["date"]] if not df.empty else df
    row["daily_pnl"] = round(row["equity"] - prev["equity"].iloc[-1], 2) if not prev.empty else 0.0
    _upsert(EQUITY, [row], row["date"])


def log_positions(day: str, positions: list[dict]) -> None:
    _upsert(POSITIONS, [{"date": day, **p} for p in positions], day)


def log_trades(day: str, trades: list[dict]) -> None:
    if trades:  # append-only: submitted orders are real even if the day is re-run
        _upsert(TRADES, [{"date": day, **t} for t in trades], day="__none__", key=["order_id"])


def log_fills(fills: list[dict]) -> None:
    if fills:
        _upsert(FILLS, fills, day="__none__", key=["order_id"])


def log_daily(day: str, payload: dict) -> Path:
    DAILY.mkdir(parents=True, exist_ok=True)
    path = DAILY / f"{day}.json"
    path.write_text(json.dumps(payload, indent=2, default=str))
    return path
