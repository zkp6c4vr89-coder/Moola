# Moola: rules for Claude Code

Moola is a paper-trading bot that trades fake money on real markets through Alpaca. The account holds US ETFs and BTC/ETH. A GitHub Actions job runs it once a day and commits its logs back to this repo. Once a month the human runs `/review-strategy`: Claude reads the logs, works out what helped and what hurt, and evolves the strategy.

## What you may edit
- **During a strategy review, you may only edit `src/moola/strategy.py`**, plus new files under `candidates/` and `reviews/`.
- `strategy.py` must keep its contract:
  - `VERSION` (str);
  - `PARAMS` (dict, holding every tunable);
  - `decide(snapshot, portfolio) -> Decision`, which must be pure and deterministic, with no I/O, no randomness and no clock reads.
- The data available in a `MarketSnapshot` is defined in `src/moola/types.py`:
  - **Bars, macro and `crypto_fear_greed`** exist in both the backtest and the live bot.
  - **`news`** only exists live. It is always empty in backtests, so a strategy that relies on news sentiment cannot be validated by backtest. Evaluate it against the logged live data in `logs/daily/*.json`, and treat it as unproven.

## Never touch
- **Protected files:** `broker.py`, `risk.py`, `orders.py`, `run_daily.py`, `backtest.py`, `logger.py`, `report.py`, `metrics.py`, `data/*`, `.github/`, `.env`.
- **Protected config:** the `risk`, `backtest` and `review_gates` sections of `config.yaml`.
- **Logs:** the existing files under `logs/` are the record of what happened, so never rewrite them.

If an improvement needs a protected file changed (a new data source, for example), propose it in the review write-up and let the human decide.

## Overfitting rules
1. **Never run `python -m moola.backtest --holdout`** unless the human explicitly asks in this conversation. Never tune against holdout numbers you have seen.
2. Write the hypothesis down **before** you backtest it.
3. Test at most 2 candidate changes per review. Don't quietly try 20 variants and report the best one. If you test more, report every one.
4. Prefer fewer parameters. A change must pass every gate in `config.yaml → review_gates`.
5. Leave at least `min_days_between_changes` live trading days between strategy versions, so each version gets a real live test.

## daybot (separate experiment)
- `src/daybot/` is an intraday $10-base-unit trader. It runs on its **own** paper account, with the `DAYBOT_*` keys. See `src/daybot/README.md`.
- The Moola review rules above don't apply to it.
- **Never set `LIVE=true`**, and never touch the `DAYBOT_*` keys.
- Always run `python -m daybot.backtest` (walk-forward) before changing `src/daybot/strategy.py` or `config.py`.

## Commands
```bash
source .venv/bin/activate
pytest -q                                           # strategy contract + risk tests (must pass)
python -m moola.report                              # regenerate results_summary.md
python -m moola.backtest                            # current strategy: train + validation
python -m moola.backtest --strategy candidates/X.py # compare a candidate side by side
python -m moola.run_daily --dry-run                 # one decision with live data, no orders
```
