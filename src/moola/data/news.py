"""Recent headlines from the Alpaca News API, scored with VADER sentiment."""
from datetime import datetime, timedelta, timezone

import requests
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from moola.config import alpaca_keys

NEWS_URL = "https://data.alpaca.markets/v1beta1/news"
_analyzer = SentimentIntensityAnalyzer()


def _news_symbol(symbol: str) -> str:
    return symbol.replace("/", "")  # Alpaca news tags crypto as BTCUSD


def fetch_news(symbols: list[str], lookback_hours: int) -> dict[str, dict]:
    key, secret = alpaca_keys()
    by_news_symbol = {_news_symbol(s): s for s in symbols}
    params = {
        "symbols": ",".join(by_news_symbol),
        "start": (datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).isoformat(),
        "limit": 50,
        "sort": "desc",
    }
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    articles, token = [], None
    for _ in range(5):  # up to 250 articles
        if token:
            params["page_token"] = token
        resp = requests.get(NEWS_URL, params=params, headers=headers, timeout=20)
        resp.raise_for_status()
        body = resp.json()
        articles += body.get("news", [])
        token = body.get("next_page_token")
        if not token:
            break

    out = {s: {"count": 0, "sentiment": None, "headlines": []} for s in symbols}
    scores: dict[str, list[float]] = {s: [] for s in symbols}
    for a in articles:
        text = f"{a.get('headline', '')}. {a.get('summary', '')}"
        score = _analyzer.polarity_scores(text)["compound"]
        for tag in a.get("symbols", []):
            sym = by_news_symbol.get(tag)
            if sym is None:
                continue
            scores[sym].append(score)
            if len(out[sym]["headlines"]) < 5:
                out[sym]["headlines"].append({"headline": a.get("headline"), "source": a.get("source"),
                                              "created_at": a.get("created_at"), "sentiment": round(score, 3)})
    for sym, vals in scores.items():
        out[sym]["count"] = len(vals)
        out[sym]["sentiment"] = round(sum(vals) / len(vals), 3) if vals else None
    return out
