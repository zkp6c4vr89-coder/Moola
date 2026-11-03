from moola import logger


def test_first_day_with_no_positions_does_not_break_reads(tmp_path, monkeypatch):
    path = tmp_path / "positions.csv"
    monkeypatch.setattr(logger, "POSITIONS", path)
    logger.log_positions("2026-10-31", [])
    assert logger._read(path).empty
    path.write_text("\n")                      # the file the Oct 31 run left behind
    assert logger._read(path).empty
    logger.log_positions("2026-11-01", [{"symbol": "BTC/USD", "qty": 0.2}])
    logger.log_positions("2026-11-02", [])
    assert list(logger._read(path)["date"]) == ["2026-11-01"]
