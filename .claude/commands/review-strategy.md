---
description: Monthly strategy review. Analyze live P&L and logs, test up to 2 changes, evolve strategy.py if they pass the gates.
---

Run a Moola strategy review. Follow CLAUDE.md strictly. Extra focus from the human (may be empty): $ARGUMENTS

## 1. Gather
- `git pull` so you have the bot's latest logs. Then activate `.venv`, run `pytest -q` and `python -m moola.report`.
- Find the last review in `reviews/` and its date. Everything since that date is the review period.
- Read:
  - `results_summary.md`;
  - `logs/equity.csv`, `logs/trades.csv`, `logs/fills.csv` and `logs/positions.csv`;
  - the `logs/daily/*.json` files for the period (sample them if there are many);
  - the previous review's "what to watch" section.
- Count the live trading days since the current `VERSION` was deployed. If it is fewer than `review_gates.min_days_between_changes`, do the analysis but **do not change the strategy**. Say so in the write-up.

## 2. Diagnose
Answer each question with numbers from the logs:
- **Benchmarks:** how did the bot do against SPY and BTC buy-and-hold over the period? Was the gap from exposure (cash drag) or from asset selection?
- **Attribution:** which symbols made or lost the most?
- **Decision quality:** were the losses from bad signals (entries that reversed), late exits, whipsaw (in-out-in within a few days) or execution (slippage, rejected orders)?
- **Risk flags:** did any fire often? (For example, the crypto cap binding every day means the strategy constantly wants more crypto than it is allowed.)
- **Data health:** did errors leave gaps in the data?
- **Unused signals:** sentiment, macro and Fear & Greed are logged but not traded on. Did they line up with next-day or next-week returns? Treat anything based on fewer than about 60 observations as anecdotal and say so.

## 3. Hypothesize (before any backtest)
- Write at most 2 candidate changes. For each, state:
  - the hypothesis;
  - the expected effect;
  - which observation motivated it.
- Prefer simple changes: a parameter, a filter, or a sizing rule.

## 4. Test
- Copy `src/moola/strategy.py` to `candidates/<next-version>_<slug>.py`.
- Make the change there and bump `VERSION`.
- Run `python -m moola.backtest --strategy candidates/<file>.py --json candidates/<file>.json`.
- **Never use `--holdout`** unless the human asked.
- Check each candidate against `config.yaml → review_gates`, comparing its validation numbers with those of the current strategy.

## 5. Decide
**If a candidate passes every gate:**
1. Copy it over `src/moola/strategy.py`.
2. Run `pytest -q`.
3. Confirm that `python -m moola.backtest` reproduces the candidate's numbers.

**If none passes:** make no change. That is a normal, healthy outcome.

## 6. Write up
Create `reviews/YYYY-MM.md` with these sections:
- **Period and versions:** the period covered and the versions live during it.
- **Results vs benchmarks:** a performance table.
- **Diagnosis:** findings, each with evidence.
- **Hypotheses tested:** each hypothesis, with a backtest table comparing current and candidate on train and validation, and a gate-by-gate pass/fail.
- **Decision:** what changed, or "no change", and why.
- **What to watch next period:** concrete metrics to check next time.
- **Proposals for the human (optional):** changes to protected files or config.

## 7. Commit
- Stage `src/moola/strategy.py` (if changed) and `reviews/`.
- Commit with the message `review YYYY-MM: <one-line outcome>`.
- If the strategy changed, add the tag `strategy-<VERSION>`.
- Ask the human before pushing.
