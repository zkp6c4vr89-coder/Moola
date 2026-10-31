import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
LOGS = ROOT / "logs"
CACHE = ROOT / "data_cache"

load_dotenv(ROOT / ".env")


def load_config(path: Path = ROOT / "config.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def alpaca_keys() -> tuple[str, str]:
    key, secret = os.environ.get("ALPACA_API_KEY"), os.environ.get("ALPACA_SECRET_KEY")
    if not key or not secret:
        raise RuntimeError("Set ALPACA_API_KEY and ALPACA_SECRET_KEY (in .env locally, or as GitHub secrets).")
    return key, secret


def is_crypto(symbol: str) -> bool:
    return "/" in symbol
