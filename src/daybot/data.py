"""Free IEX market data from Alpaca: minute bars, latest trades/quotes, and the trading calendar."""
from datetime import date, datetime, time, timedelta

import pandas as pd
from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestQuoteRequest, StockLatestTradeRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetCalendarRequest

from daybot import config

COLUMNS = ["open", "high", "low", "close", "volume"]


def _client() -> StockHistoricalDataClient:
    key, secret, _ = config.credentials(data_only=True)
    return StockHistoricalDataClient(key, secret)


def regular_hours(df: pd.DataFrame) -> pd.DataFrame:
    t = df.index.time
    return df[(t >= time(9, 30)) & (t < time(16, 0))]


def minute_bars(symbols: list[str], start: datetime, end: datetime) -> dict[str, pd.DataFrame]:
    """1-minute regular-hours bars, indexed by tz-aware ET timestamps (bar start)."""
    req = StockBarsRequest(symbol_or_symbols=symbols, timeframe=TimeFrame.Minute, start=start, end=end,
                           feed=DataFeed(config.FEED))
    df = _client().get_stock_bars(req).df
    out = {}
    if df.empty:
        return out
    for sym, sub in df.groupby(level="symbol"):
        sub = sub.droplevel("symbol")[COLUMNS]
        sub.index = pd.DatetimeIndex(sub.index).tz_convert(config.ET)
        out[sym] = regular_hours(sub[~sub.index.duplicated(keep="last")].sort_index())
    return out


def latest_trades(symbols: list[str]) -> dict[str, float]:
    if not symbols:
        return {}
    res = _client().get_stock_latest_trade(StockLatestTradeRequest(symbol_or_symbols=symbols, feed=DataFeed(config.FEED)))
    return {s: float(t.price) for s, t in res.items()}


def latest_quote(symbol: str) -> tuple[float, float]:
    q = _client().get_stock_latest_quote(StockLatestQuoteRequest(symbol_or_symbols=symbol, feed=DataFeed(config.FEED)))[symbol]
    return float(q.bid_price), float(q.ask_price)


def calendar(start: date, end: date) -> list[dict]:
    """Trading sessions with tz-aware ET open/close. Holidays are absent; half-days close early."""
    key, secret, live = config.credentials(data_only=True)
    rows = TradingClient(key, secret, paper=not live).get_calendar(GetCalendarRequest(start=start, end=end))
    out = []
    for c in rows:
        o, cl = c.open, c.close
        out.append({"date": c.date,
                    "open": o.replace(tzinfo=config.ET) if o.tzinfo is None else o.astimezone(config.ET),
                    "close": cl.replace(tzinfo=config.ET) if cl.tzinfo is None else cl.astimezone(config.ET)})
    return out


def history(symbols: list[str], start: date, end: date) -> dict[str, pd.DataFrame]:
    """Minute bars for backtesting, cached per symbol per month."""
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out = {}
    for sym in symbols:
        parts = []
        for month in pd.period_range(start, end, freq="M"):
            path = config.CACHE_DIR / f"{sym}_{month}.pkl"
            complete = month.end_time.date() < date.today()
            if path.exists():
                parts.append(pd.read_pickle(path))
                continue
            s = datetime.combine(month.start_time.date(), time(0), config.ET)
            e = datetime.combine(month.end_time.date(), time(23, 59), config.ET)
            e = min(e, datetime.now(config.ET) - timedelta(minutes=16))
            df = minute_bars([sym], s, e).get(sym, pd.DataFrame(columns=COLUMNS))
            if complete:
                df.to_pickle(path)
            parts.append(df)
        df = pd.concat([p for p in parts if not p.empty]) if parts else pd.DataFrame(columns=COLUMNS)
        lo, hi = pd.Timestamp(start, tz=config.ET), pd.Timestamp(end, tz=config.ET) + pd.Timedelta(days=1)
        out[sym] = df[(df.index >= lo) & (df.index < hi)]
    return out
