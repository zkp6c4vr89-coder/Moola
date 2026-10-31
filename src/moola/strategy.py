"""The trading strategy. This is the ONLY file a strategy review may edit.

Contract (do not change):
    VERSION: str
    PARAMS: dict  -- every tunable lives here
    decide(snapshot: MarketSnapshot, portfolio: Portfolio) -> Decision
decide() must be pure and deterministic: no I/O, no randomness, no clock reads.
It runs unchanged in the live bot and the backtester.

v1: dual-momentum trend following.
  - Score each asset by average return over two lookbacks (~3 and ~6 months).
  - Only hold assets above their long moving average with positive momentum.
  - Hold the top N, sized by inverse volatility. Anything not held is cash.
  - News sentiment, macro and Fear & Greed are logged but not used yet.
"""
import numpy as np

from moola.config import is_crypto
from moola.types import Decision, MarketSnapshot, Portfolio

VERSION = "v1"

PARAMS = {
    "stock_momentum_windows": [63, 126],    # trading days
    "crypto_momentum_windows": [90, 180],   # calendar days (crypto trades every day)
    "stock_sma": 200,
    "crypto_sma": 100,
    "vol_window": 60,
    "top_n": 4,
}


def decide(snapshot: MarketSnapshot, portfolio: Portfolio) -> Decision:
    p = PARAMS
    eligible: dict[str, tuple[float, float]] = {}
    indicators: dict[str, dict] = {}
    rationale: dict[str, str] = {}

    for sym, df in sorted(snapshot.bars.items()):
        crypto = is_crypto(sym)
        windows = p["crypto_momentum_windows"] if crypto else p["stock_momentum_windows"]
        sma_n = p["crypto_sma"] if crypto else p["stock_sma"]
        close = df["close"].to_numpy(dtype=float)
        need = max(max(windows), sma_n, p["vol_window"]) + 1
        if len(close) < need:
            rationale[sym] = f"out: only {len(close)} bars, need {need}"
            continue

        momentum = float(np.mean([close[-1] / close[-1 - w] - 1 for w in windows]))
        sma = float(close[-sma_n:].mean())
        log_rets = np.diff(np.log(close[-p["vol_window"] - 1:]))
        vol = float(log_rets.std() * np.sqrt(365 if crypto else 252))
        indicators[sym] = {"close": round(float(close[-1]), 4), "momentum": round(momentum, 4),
                           "sma": round(sma, 4), "above_sma": bool(close[-1] > sma), "vol": round(vol, 4)}

        if close[-1] <= sma:
            rationale[sym] = "out: below moving average"
        elif momentum <= 0:
            rationale[sym] = "out: negative momentum"
        else:
            eligible[sym] = (momentum, vol)

    ranked = sorted(eligible, key=lambda s: eligible[s][0], reverse=True)
    picks = ranked[: p["top_n"]]
    inv_vol = {s: 1 / max(eligible[s][1], 1e-6) for s in picks}
    total = sum(inv_vol.values())
    targets = {s: inv_vol[s] / total for s in picks}

    for rank, s in enumerate(ranked, 1):
        if s in targets:
            rationale[s] = f"in: momentum rank {rank}, weight {targets[s]:.1%}"
        else:
            rationale[s] = f"out: momentum rank {rank} > top_n {p['top_n']}"
    return Decision(targets=targets, rationale=rationale, indicators=indicators)
