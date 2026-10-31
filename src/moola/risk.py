"""Hard guardrails applied to every strategy decision. PROTECTED: reviews must not edit this file."""
import math

from moola.config import is_crypto
from moola.types import Portfolio


def kill_switch_triggered(portfolio: Portfolio, cfg: dict) -> bool:
    limit = cfg["risk"]["kill_switch_drawdown"]
    return portfolio.peak_equity > 0 and portfolio.equity < portfolio.peak_equity * (1 - limit)


def enforce(targets: dict[str, float], portfolio: Portfolio, cfg: dict) -> tuple[dict[str, float], list[str]]:
    """Clamp target weights to the risk limits. Returns (safe targets, flags describing what was changed)."""
    r = cfg["risk"]
    flags: list[str] = []

    if kill_switch_triggered(portfolio, cfg):
        flags.append(f"KILL_SWITCH: equity {portfolio.equity:,.0f} is more than "
                     f"{r['kill_switch_drawdown']:.0%} below peak {portfolio.peak_equity:,.0f}; going to cash")
        return {}, flags

    safe: dict[str, float] = {}
    for sym, w in targets.items():
        if not isinstance(w, (int, float)) or not math.isfinite(w) or w <= 0:
            if w != 0:
                flags.append(f"dropped {sym}: invalid weight {w!r} (no shorting)")
            continue
        if w > r["max_position_weight"]:
            flags.append(f"capped {sym} from {w:.1%} to {r['max_position_weight']:.0%}")
            w = r["max_position_weight"]
        safe[sym] = float(w)

    crypto_total = sum(w for s, w in safe.items() if is_crypto(s))
    if crypto_total > r["max_crypto_weight"]:
        scale = r["max_crypto_weight"] / crypto_total
        flags.append(f"scaled crypto from {crypto_total:.1%} to {r['max_crypto_weight']:.0%}")
        safe = {s: (w * scale if is_crypto(s) else w) for s, w in safe.items()}

    total = sum(safe.values())
    if total > 1:
        flags.append(f"scaled gross exposure from {total:.1%} to 100% (no leverage)")
        safe = {s: w / total for s, w in safe.items()}
    return safe, flags
