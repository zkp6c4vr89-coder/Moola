from dataclasses import dataclass, field
from datetime import date

import pandas as pd


@dataclass(frozen=True)
class MarketSnapshot:
    """Everything the strategy may look at. Bars never extend past `asof`."""
    asof: date
    bars: dict[str, pd.DataFrame]          # symbol -> daily OHLCV indexed by date, oldest first
    macro: dict[str, float | None] = field(default_factory=dict)   # vix, us10y, curve_2s10s
    crypto_fear_greed: int | None = None    # 0 (extreme fear) .. 100 (extreme greed)
    news: dict[str, dict] = field(default_factory=dict)  # symbol -> {count, sentiment, headlines}; live only


@dataclass(frozen=True)
class Portfolio:
    equity: float
    cash: float
    positions: dict[str, float]             # symbol -> market value in $
    peak_equity: float


@dataclass
class Decision:
    targets: dict[str, float]               # symbol -> target weight of equity (0..1)
    rationale: dict[str, str] = field(default_factory=dict)
    indicators: dict[str, dict] = field(default_factory=dict)


@dataclass
class Order:
    symbol: str
    side: str                               # "buy" | "sell"
    notional: float                         # $ amount
    close_all: bool                         # sell the whole position
    decision_price: float | None
    reason: str = ""
