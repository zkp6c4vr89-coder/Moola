"""Contract tests every strategy version must pass. Reviews must keep these green."""
import math
from datetime import date

from moola import strategy
from moola.types import Decision, MarketSnapshot, Portfolio

PORTFOLIO = Portfolio(equity=100_000, cash=100_000, positions={}, peak_equity=100_000)


def snap(bars):
    return MarketSnapshot(asof=date(2021, 6, 1), bars=bars)


def test_contract_exports():
    assert isinstance(strategy.VERSION, str) and strategy.VERSION
    assert isinstance(strategy.PARAMS, dict)


def test_returns_valid_weights(bars):
    d = strategy.decide(snap(bars), PORTFOLIO)
    assert isinstance(d, Decision)
    assert all(math.isfinite(w) and w >= 0 for w in d.targets.values())
    assert sum(d.targets.values()) <= 1 + 1e-9
    assert set(d.targets) <= set(bars)


def test_deterministic(bars):
    a = strategy.decide(snap(bars), PORTFOLIO)
    b = strategy.decide(snap(bars), PORTFOLIO)
    assert a.targets == b.targets


def test_does_not_mutate_inputs(bars):
    before = {s: df.copy() for s, df in bars.items()}
    strategy.decide(snap(bars), PORTFOLIO)
    for s, df in bars.items():
        assert df.equals(before[s])


def test_handles_short_history_and_empty(bars):
    short = {s: df.iloc[:10] for s, df in bars.items()}
    assert strategy.decide(snap(short), PORTFOLIO).targets == {}
    assert strategy.decide(snap({}), PORTFOLIO).targets == {}


def test_every_symbol_has_rationale(bars):
    d = strategy.decide(snap(bars), PORTFOLIO)
    assert set(d.rationale) == set(bars)
