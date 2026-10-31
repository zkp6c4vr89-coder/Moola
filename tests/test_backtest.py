import pandas as pd

from moola import strategy
from moola.backtest import data_end, simulate, splits
from moola.types import Decision


def test_holdout_hidden_by_default(cfg):
    assert data_end(cfg, holdout=False) == pd.Timestamp(cfg["backtest"]["validation_end"])
    assert "holdout" not in splits(cfg, holdout=False)
    assert data_end(cfg, holdout=True) == pd.Timestamp(cfg["backtest"]["holdout_end"])


def test_simulation_never_sees_future(bars, cfg):
    seen = []

    def spy(snapshot, portfolio):
        for df in snapshot.bars.values():
            assert df.index.max() <= pd.Timestamp(snapshot.asof)
        seen.append(snapshot.asof)
        return Decision(targets={})

    end = pd.Timestamp("2020-12-31")
    sim = simulate(bars, spy, cfg, pd.Timestamp("2020-06-01"), end)
    assert max(seen) <= end.date()
    assert sim["equity"].index.max() <= end


def test_simulation_deterministic_and_conserves_cash_when_flat(bars, cfg):
    start, end = pd.Timestamp("2020-09-01"), pd.Timestamp("2021-03-01")
    a = simulate(bars, strategy.decide, cfg, start, end)
    b = simulate(bars, strategy.decide, cfg, start, end)
    assert a["equity"].equals(b["equity"])
    flat = simulate(bars, lambda s, p: Decision(targets={}), cfg, start, end)
    assert (flat["equity"] == 100_000).all()


def test_weights_respect_risk_limits(bars, cfg):
    sim = simulate(bars, lambda s, p: Decision(targets={"BTC/USD": 1.0}), cfg,
                   pd.Timestamp("2020-09-01"), pd.Timestamp("2021-03-01"))
    # price drift between rebalances can push weights slightly over the cap
    assert max(w.get("BTC/USD", 0) for w in sim["weights"].values()) <= cfg["risk"]["max_crypto_weight"] * 1.15
