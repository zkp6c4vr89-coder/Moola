"""Day-trader settings. Secrets come from .env (or GitHub secrets); every rule lives here."""
import os
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = ROOT / "logs" / "daybot"
STATE_FILE = LOG_DIR / "state.json"
CACHE_DIR = ROOT / "data_cache" / "daybot"
ET = ZoneInfo("America/New_York")

load_dotenv(ROOT / ".env")

# --- Universe and data -------------------------------------------------------
UNIVERSE = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN"]
FEED = "iex"                        # free real-time feed; VWAP uses IEX volume only

# --- Capital and scaling -----------------------------------------------------
LADDER = [10, 25, 50, 100, 250, 500, 1000]   # base unit steps in $
DAILY_PROFIT_CEILING = 0.02         # x base unit: stop for the day at or above (never compounded)
DAILY_LOSS_LIMIT = 0.03             # x base unit: stop for the day at or below minus this
MAX_POSITION_FRACTION = 0.25        # x base unit per trade
MAX_TRADES_PER_DAY = 3              # entries per day
SCALE_UP_STREAK = 20                # consecutive positive trading days to step up
SCALE_DOWN_DRAWDOWN = 0.25          # step down if level P&L falls 25% below the level's start

# --- Schedule (all ET) ---------------------------------------------------------
SKIP_OPEN_MINUTES = 15
LAST_ENTRY = time(15, 30)           # no new entries after this
FLATTEN_BEFORE_CLOSE_MINUTES = 10
HANDOFF = time(12, 45)              # GitHub: morning job ends (flat), afternoon job starts
LOOP_SECONDS = 60
LOOP_OFFSET_SECONDS = 10            # run at hh:mm:10 so the previous minute's bar exists
SIGNAL_MAX_AGE_SECONDS = 90         # only act on a 5-min bar that just completed

# --- Safety --------------------------------------------------------------------
KILL_DRAWDOWN = 0.30                # from peak, for both the account and the bot's own balance
MAX_CONSECUTIVE_API_ERRORS = 3
MAX_SPREAD_BPS = 10                 # skip an entry if bid/ask spread is wider
FILL_TIMEOUT_SECONDS = 20

# --- Backtest cost model ---------------------------------------------------------
SPREAD_BPS = {"SPY": 1.0, "QQQ": 1.0}   # full bid/ask spread; others use the default
DEFAULT_SPREAD_BPS = 3.0
SLIPPAGE_BPS = 2.0                  # per side, on top of half the spread
COMMISSION_PER_ORDER = 0.0          # Alpaca charges no commission on stocks
SEC_FEE_RATE = 0.0000278            # x sell notional (check current SEC Section 31 rate)
TAF_PER_SHARE = 0.000166            # FINRA TAF on shares sold
TAF_MAX = 8.30


def credentials(data_only: bool = False) -> tuple[str, str, bool]:
    """Trading always needs the day-trader's own keys. Market data may fall back to Moola's keys."""
    key, secret = os.environ.get("DAYBOT_ALPACA_API_KEY"), os.environ.get("DAYBOT_ALPACA_SECRET_KEY")
    if data_only and not (key and secret):
        key, secret = os.environ.get("ALPACA_API_KEY"), os.environ.get("ALPACA_SECRET_KEY")
    if not key or not secret:
        raise RuntimeError("Set DAYBOT_ALPACA_API_KEY and DAYBOT_ALPACA_SECRET_KEY (a separate paper account).")
    return key, secret, os.environ.get("LIVE", "false").strip().lower() == "true"


def spread_bps(symbol: str) -> float:
    return SPREAD_BPS.get(symbol, DEFAULT_SPREAD_BPS)
