import numpy as np
import pandas as pd

PERIODS_PER_YEAR = 365  # equity is marked every calendar day (crypto trades weekends)


def summarize(equity: pd.Series) -> dict:
    """Standard performance stats for an equity curve indexed by date."""
    equity = equity.dropna()
    if len(equity) < 2:
        return {"total_return": 0.0, "cagr": 0.0, "vol": 0.0, "sharpe": 0.0, "max_drawdown": 0.0, "days": len(equity)}
    rets = equity.pct_change().dropna()
    days = (pd.Timestamp(equity.index[-1]) - pd.Timestamp(equity.index[0])).days or 1
    total = equity.iloc[-1] / equity.iloc[0] - 1
    vol = rets.std() * np.sqrt(PERIODS_PER_YEAR)
    sharpe = rets.mean() / rets.std() * np.sqrt(PERIODS_PER_YEAR) if rets.std() > 0 else 0.0
    return {
        "total_return": float(total),
        "cagr": float((1 + total) ** (365 / days) - 1),
        "vol": float(vol),
        "sharpe": float(sharpe),
        "max_drawdown": float((equity / equity.cummax() - 1).min()),
        "days": int(days),
    }
