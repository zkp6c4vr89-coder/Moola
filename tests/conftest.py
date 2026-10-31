import numpy as np
import pandas as pd
import pytest

from moola.config import load_config


@pytest.fixture
def cfg():
    return load_config()


def make_bars(n: int, drift: float, start="2020-01-01", freq="D", seed=0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq=freq)
    close = 100 * np.exp(np.cumsum(drift + 0.01 * rng.standard_normal(n)))
    return pd.DataFrame({"open": close, "high": close, "low": close, "close": close, "volume": 1.0}, index=idx)


@pytest.fixture
def bars():
    return {
        "SPY": make_bars(400, 0.002, freq="B", seed=1),   # uptrend
        "TLT": make_bars(400, -0.002, freq="B", seed=2),  # downtrend
        "GLD": make_bars(400, 0.001, freq="B", seed=3),
        "BTC/USD": make_bars(500, 0.003, seed=4),
    }
