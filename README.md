# Moola 💸

A paper-trading bot that trades **fake money on real markets**: US ETFs plus BTC/ETH, through Alpaca's free paper account.

- **Daily:** the bot logs its P&L and data from several sources.
- **Monthly:** Claude Code reviews the logs and evolves the strategy.

```
GitHub Actions, daily 21:30 UTC              You + Claude Code, monthly
  prices (Alpaca) ─┐                           /review-strategy
  news + VADER    ─┤                             ├─ read logs + results_summary.md
  macro (FRED)    ─┼─> strategy.decide()          ├─ diagnose, form ≤2 hypotheses
  Fear & Greed    ─┘     └─> risk.enforce()       ├─ backtest on train/validation
                           └─> paper orders      ├─ accept only if gates pass
  logs/ + results_summary.md committed back      └─ edit strategy.py, write reviews/YYYY-MM.md
```

## Setup

### 1. Alpaca paper account
1. Sign up at alpaca.markets.
2. Switch to **Paper Trading** and generate API keys there. Paper keys only work against the paper endpoint.
3. In the paper account settings, check that **crypto trading is enabled**.

### 2. Run locally
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env              # paste your paper keys
pytest -q
python -m moola.backtest          # baseline v1 on train + validation (first run downloads and caches the data)
python -m moola.run_daily --dry-run   # writes logs/daily/<today>.dryrun.json, places no orders
```

### 3. Automate with GitHub Actions
1. Create a **private** GitHub repo and push this project to it.
2. Add two repository secrets under Settings → Secrets → Actions:
   - `ALPACA_API_KEY`
   - `ALPACA_SECRET_KEY`
3. Under Actions → `daily-run` → **Run workflow**, do a test run with `dry_run` ticked, then a real run.
4. Check that the run committed `logs/` and `results_summary.md` back to the repo, and that its orders appear in Alpaca's paper dashboard.

### 4. Review monthly
Do this after about a month of live runs:
```bash
git pull
claude      # then: /review-strategy
```

## How the bot behaves
- **Universe:** set in `config.yaml`. 10 ETFs, plus BTC/USD and ETH/USD.
- **Strategy v1:** dual momentum with a trend filter and inverse-volatility sizing (see `src/moola/strategy.py`).
- **Risk limits** (in `risk.py` and `orders.py`):
  - 25% per position and 30% in crypto in total;
  - no leverage or shorting, and at most 10 orders a day;
  - kill switch at a 25% drawdown.
- **Resuming after the kill switch:** this needs you. Set `risk.peak_reset_date` in `config.yaml`.
- **Timing:**
  - Stock orders are placed after the close and fill at the next open.
  - Crypto orders fill immediately.
  - On weekends and holidays, only crypto trades.

## Logs
| file | contents |
|---|---|
| `logs/equity.csv` | one row per run: equity, cash, daily/cumulative P&L, exposure, benchmark prices, strategy version |
| `logs/trades.csv` | every order submitted, with the decision price and reason |
| `logs/fills.csv` | fills, with slippage vs the decision price (bps) |
| `logs/positions.csv` | daily positions with unrealized P&L |
| `logs/daily/YYYY-MM-DD.json` | everything for that day: indicators, rationale per symbol, news headlines and sentiment, macro, Fear & Greed, risk flags, errors |
| `results_summary.md` | auto-generated summary and chart |

## Data sources
| source | what | key needed |
|---|---|---|
| Alpaca market data | daily bars, ETFs (SIP) and crypto | yes (same paper keys) |
| Alpaca News API | headlines, scored with VADER sentiment | yes (same paper keys) |
| FRED | VIX, 10y yield, 2s10s curve | no |
| alternative.me | crypto Fear & Greed index | no |

## Caveats
- **Backtest windows:**
  - train: 2018–2022;
  - validation: 2023 to Oct 2025;
  - holdout: Nov 2025 to Oct 2026, which is never used for tuning.
- Alpaca's crypto history starts in 2021, so early training years are stocks-only.
- Paper fills are simulated and optimistic. Real fills would be somewhat worse.
- GitHub cron runs can start up to about an hour late. The actual run time is logged.
- On public repos, scheduled workflows pause after 60 days without commits. The bot's daily commits normally keep the repo active.
