"""Daily bars from Alpaca for stocks/ETFs and crypto."""
from datetime import datetime, timedelta, timezone

import pandas as pd
from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.historical import CryptoHistoricalDataClient, StockHistoricalDataClient
from alpaca.data.requests import CryptoBarsRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame

from moola.config import CACHE, alpaca_keys

COLUMNS = ["open", "high", "low", "close", "volume"]


def _split(df: pd.DataFrame, tz: str) -> dict[str, pd.DataFrame]:
    out = {}
    if df.empty:
        return out
    for symbol, sub in df.groupby(level="symbol"):
        sub = sub.droplevel("symbol")[COLUMNS]
        sub.index = pd.DatetimeIndex(sub.index).tz_convert(tz).tz_localize(None).normalize()
        out[symbol] = sub[~sub.index.duplicated(keep="last")].sort_index()
    return out


def fetch_stock_bars(symbols: list[str], start: datetime, end: datetime, feed: str = "sip") -> dict[str, pd.DataFrame]:
    if not symbols:
        return {}
    key, secret = alpaca_keys()
    client = StockHistoricalDataClient(key, secret)
    # The free plan only serves SIP data older than 15 minutes.
    end = min(end, datetime.now(timezone.utc) - timedelta(minutes=16))
    req = StockBarsRequest(symbol_or_symbols=symbols, timeframe=TimeFrame.Day, start=start, end=end,
                           adjustment=Adjustment.ALL, feed=DataFeed(feed))
    return _split(client.get_stock_bars(req).df, "America/New_York")


def fetch_crypto_bars(symbols: list[str], start: datetime, end: datetime) -> dict[str, pd.DataFrame]:
    if not symbols:
        return {}
    client = CryptoHistoricalDataClient()
    req = CryptoBarsRequest(symbol_or_symbols=symbols, timeframe=TimeFrame.Day, start=start, end=end)
    return _split(client.get_crypto_bars(req).df, "UTC")


def fetch_live_bars(stocks: list[str], crypto: list[str], history_days: int, feed: str) -> dict[str, pd.DataFrame]:
    """Bars for today's run. Today's crypto bar is still forming, so it's dropped to match the backtest."""
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=history_days)
    bars = fetch_stock_bars(stocks, start, now, feed)
    today_utc = pd.Timestamp(now.date())
    for sym, df in fetch_crypto_bars(crypto, start, now).items():
        bars[sym] = df[df.index < today_utc]
    return bars


def fetch_history(stocks: list[str], crypto: list[str], start: str, end: str, feed: str) -> dict[str, pd.DataFrame]:
    """Historical bars for backtesting, cached on disk per date range."""
    CACHE.mkdir(exist_ok=True)
    cache = CACHE / f"bars_{start}_{end}_{'-'.join(stocks + crypto).replace('/', '')}.pkl"
    if cache.exists():
        return pd.read_pickle(cache)
    s = datetime.fromisoformat(str(start)).replace(tzinfo=timezone.utc)
    e = datetime.fromisoformat(str(end)).replace(tzinfo=timezone.utc) + timedelta(days=1)
    bars = fetch_stock_bars(stocks, s, e, feed)
    bars.update(fetch_crypto_bars(crypto, s, e))
    end_ts = pd.Timestamp(end)
    bars = {k: v[v.index <= end_ts] for k, v in bars.items()}
    pd.to_pickle(bars, cache)
    return bars
