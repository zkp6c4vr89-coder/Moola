"""VWAP + RSI(14) mean reversion on 5-minute bars, long only.

Entry: a completed 5-min bar closes stretched below VWAP, RSI is oversold, and the bar closed green
(sellers easing). Stop: entry - stop_atr x ATR. Take-profit: VWAP, the price we expect to revert to.
Setups with too little room to VWAP relative to the stop are skipped. No setup means no trade.

Pure functions only, shared by the live loop and the backtester.
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd

PARAMS = {
    "bar_minutes": 5,
    "rsi_period": 14,
    "rsi_entry": 30.0,        # RSI must be below this
    "min_vwap_dev": 0.004,    # close at least 0.4% below VWAP
    "atr_period": 14,
    "stop_atr": 1.5,
    "min_reward_risk": 1.0,   # distance to VWAP / distance to stop
    "require_green_bar": True,
}


@dataclass(frozen=True)
class Signal:
    symbol: str
    bar_end: pd.Timestamp
    price: float
    stop: float
    take_profit: float
    score: float            # bigger = more stretched; used to rank simultaneous setups
    reason: str


def indicators(bars_1m: pd.DataFrame, params: dict = PARAMS) -> pd.DataFrame:
    """Resample 1-min bars to N-min bars and add vwap (daily reset), rsi, atr, dev, bar_end."""
    cols = ["open", "high", "low", "close", "volume", "vwap", "rsi", "atr", "dev", "bar_end"]
    if bars_1m.empty:
        return pd.DataFrame(columns=cols)
    n = params["bar_minutes"]
    b = (bars_1m.resample(f"{n}min", label="left", closed="left")
         .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
         .dropna(subset=["close"]))
    day = b.index.normalize()
    typical = (b["high"] + b["low"] + b["close"]) / 3
    b["vwap"] = (typical * b["volume"]).groupby(day).cumsum() / b["volume"].groupby(day).cumsum().replace(0, np.nan)

    delta = b["close"].diff()
    a = 1 / params["rsi_period"]
    gain = delta.clip(lower=0).ewm(alpha=a, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=a, adjust=False).mean()
    b["rsi"] = np.where(loss > 0, 100 - 100 / (1 + gain / loss.replace(0, np.nan)), 100.0)
    b.loc[b.index[: params["rsi_period"]], "rsi"] = np.nan  # not enough history yet

    prev = b["close"].shift()
    tr = pd.concat([b["high"] - b["low"], (b["high"] - prev).abs(), (b["low"] - prev).abs()], axis=1).max(axis=1)
    b["atr"] = tr.ewm(alpha=1 / params["atr_period"], adjust=False).mean()
    b.loc[b.index[: params["atr_period"]], "atr"] = np.nan

    b["dev"] = b["close"] / b["vwap"] - 1
    b["bar_end"] = b.index + pd.Timedelta(minutes=n)
    return b[cols]


def entry_mask(ind: pd.DataFrame, params: dict = PARAMS) -> pd.Series:
    m = (ind["dev"] <= -params["min_vwap_dev"]) & (ind["rsi"] < params["rsi_entry"]) & ind["atr"].notna()
    if params["require_green_bar"]:
        m &= ind["close"] > ind["open"]
    return m.fillna(False)


def make_signal(symbol: str, row: pd.Series, params: dict = PARAMS) -> tuple[Signal | None, str]:
    entry = float(row["close"])
    stop = entry - params["stop_atr"] * float(row["atr"])
    tp = float(row["vwap"])
    risk, reward = entry - stop, tp - entry
    if risk <= 0 or reward <= 0:
        return None, "no room between stop and VWAP"
    if reward / risk < params["min_reward_risk"]:
        return None, f"reward/risk {reward / risk:.2f} < {params['min_reward_risk']}"
    return Signal(symbol, row["bar_end"], entry, round(stop, 2), round(tp, 2), float(-row["dev"]),
                  f"dev {row['dev']:.2%}, rsi {row['rsi']:.0f}, r/r {reward / risk:.1f}"), "signal"


def evaluate(symbol: str, ind: pd.DataFrame, params: dict = PARAMS) -> tuple[Signal | None, str]:
    """Check the most recent completed bar (the caller drops any bar still forming)."""
    if ind.empty or ind["rsi"].isna().iloc[-1]:
        return None, "warming up"
    row = ind.iloc[-1]
    if not entry_mask(ind.iloc[[-1]], params).iloc[0]:
        return None, f"no setup (dev {row['dev']:.2%}, rsi {row['rsi']:.0f})"
    return make_signal(symbol, row, params)
