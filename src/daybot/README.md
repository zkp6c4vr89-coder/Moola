# daybot: a small-capital intraday paper trader

daybot is an intraday experiment that runs next to Moola. It trades a **$10 base unit** on large-cap stocks and ETFs, on its **own paper account**:

- **Strategy:** VWAP + RSI(14) mean reversion.
- **Exits:** a stop-loss and take-profit on every trade.
- **Overnight:** never holds a position.

## Files
| file | role |
|---|---|
| `config.py` | every rule and number (ladder, limits, schedule, costs); keys come from `.env` |
| `data.py` | free IEX minute bars, latest trade/quote, trading calendar |
| `strategy.py` | indicators + entry signal (pure, shared with the backtest) |
| `risk.py` | base unit and scaling ladder, daily ceiling/loss stop, trade cap, kill switch |
| `execution.py` | Alpaca orders, pre-order checks, managed stop/take-profit, flatten |
| `journal.py` / `report.py` | CSV + log files, state, daily summary |
| `backtest.py` | cost-aware minute-bar backtest with walk-forward parameter selection |
| `main.py` | the trading loop and CLI |

## Setup
1. In Alpaca, open the account switcher, **create a second paper account** and generate keys for it.
2. Put them in `.env` as `DAYBOT_ALPACA_API_KEY` and `DAYBOT_ALPACA_SECRET_KEY`, and keep `LIVE=false`.
   - FINRA retired the pattern-day-trader rule (and its $25k minimum) on June 4, 2026; Alpaca now uses intraday margin. Any balance works. If the paper account still flags day trades, raise it to $25k as a workaround.
3. To run it on GitHub, add the same two keys as repository secrets.

## Run
```bash
source .venv/bin/activate
python -m daybot.backtest                   # walk-forward, last ~2 years
python -m daybot.backtest --fixed           # current params with the scaling ladder
python -m daybot.main run --dry-run         # live scan, logs decisions, no orders
python -m daybot.main run                   # trade on paper (9:45 ET -> close), then end-of-day
python -m daybot.main status
python -m daybot.report                     # rebuild logs/daybot/summary.md
python -m daybot.main reset-kill            # after reviewing why the kill switch fired
```
On GitHub, `.github/workflows/daybot.yml` runs a morning job and an afternoon job each trading day. To try it, start the workflow by hand with `dry_run` ticked first.

## How the rules are implemented
- **Base unit:** starts at $10.
  - **Step up** the ladder ($10 → $25 → … → $1,000) after 20 consecutive positive trading days.
  - **Step down** if P&L at the current level falls 25% below its starting balance.
  - **Quiet days** with no trades neither extend nor reset the streak.
- **Per trade:** at most 25% of the base unit, placed as a notional (fractional) market order.
- **Per day:** stop at +2% of the base (the ceiling, never compounded) or −3% of it, with at most 3 entries.
- **Timing:**
  - no trading in the first 15 minutes after the open;
  - no new entries after 15:30;
  - everything is sold at 15:50;
  - holidays and half-days are skipped.
- **Exits:** Alpaca's bracket orders can't be fractional, so exits work like this:
  - every entry immediately gets a **real stop order on Alpaca**, which protects you if the bot dies;
  - the bot checks the **take-profit** (and the stop again, as a backup) every minute.
- **Before every order, the bot checks:**
  - the market is open and buying power is enough;
  - there is no existing order or position in that symbol;
  - the spread is no wider than 10 bps.
- **Kill switch:**
  - fires if the account *or* the bot's own balance falls 30% from its peak, or after 3 loop errors in a row;
  - when it fires, the bot sells everything and refuses to run until you run `reset-kill`.
- **Live trading:** only with `LIVE=true` in `.env`. The bot refuses to start if the keys don't match the mode, and GitHub always runs with `LIVE=false`.

## Logs (`logs/daybot/`)
| file | contents |
|---|---|
| `decisions/YYYY-MM-DD.csv` | every symbol, every minute: price, VWAP, RSI, ATR, action, reason |
| `orders.csv` | every order, with its fill |
| `trades.csv` | completed round trips with P&L and exit reason |
| `daily_pnl.csv` | one row per day: P&L, % of base, stop reason, ladder state |
| `YYYY-MM-DD.log` | the full text log for that day |
| `summary.md` | P&L, win rate and max drawdown |
| `state.json` | current state: ladder, streak, peaks and kill switch |

## Caveats
- VWAP uses IEX volume only (a few % of market volume), so it's an approximation.
- **Paper fills are free. Live sells pay SEC and FINRA fees, each rounded up to the cent.** On a $2.50 position that is about 2¢ per round trip, which outweighs a typical edge at this size.
  - The backtest charges these fees.
  - On paper, the bot logs an estimated fee and a fee-adjusted P&L next to the raw number.
  - `summary.md` and the scaling ladder use the fee-adjusted figure.
