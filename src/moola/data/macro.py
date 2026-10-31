"""Macro series from FRED's public CSV endpoint (no API key needed)."""
import io

import pandas as pd
import requests

FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv"


def fetch_series(series_id: str) -> pd.Series:
    resp = requests.get(FRED_CSV, params={"id": series_id}, timeout=30)
    resp.raise_for_status()
    df = pd.read_csv(io.StringIO(resp.text), na_values=".")
    df.columns = ["date", "value"]
    return pd.Series(df["value"].values, index=pd.to_datetime(df["date"]), name=series_id).dropna()


def fetch_history(series: dict[str, str]) -> pd.DataFrame:
    """name -> FRED id mapping in, one column per name out."""
    return pd.DataFrame({name: fetch_series(sid) for name, sid in series.items()}).sort_index()


def asof(history: pd.DataFrame, day: pd.Timestamp) -> dict[str, float | None]:
    past = history[history.index <= day].ffill()
    if past.empty:
        return {c: None for c in history.columns}
    return {c: (None if pd.isna(v) else float(v)) for c, v in past.iloc[-1].items()}
