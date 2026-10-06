from tradebot.journal import Journal

from .conftest import make_trade

SPEC_FIELDS = ["timestamp", "asset", "entry", "exit", "position_size", "agents_agreed", "agents_disagreed",
               "confidence", "reason", "expected_return_pct", "actual_return_pct", "market_conditions",
               "slippage_bps", "result"]


def test_trade_lifecycle_records_every_spec_field(journal: Journal, tmp_path):
    rec = make_trade(0).model_copy(update={"entry_slippage_bps": 3.0, "fees_usd": 2.5})
    journal.open_trade(rec)
    assert [t.trade_id for t in journal.open_trades()] == [rec.trade_id]

    closed = journal.close_trade(rec.trade_id, exit_price=110.0, exit_reason="take_profit", exit_fee_usd=2.75,
                                 exit_reference_price=110.2)
    assert closed.status == "CLOSED" and closed.result == "WIN"
    assert closed.pnl_usd == (110 - 100) * 10 - 5.25
    assert round(closed.actual_return_pct, 4) == round(closed.pnl_usd / 1000 * 100, 4)
    assert closed.slippage_bps > 3.0                         # entry + adverse exit slippage
    assert closed.r_multiple == closed.pnl_usd / 100.0
    for f in SPEC_FIELDS:
        assert getattr(closed, f) is not None, f

    path = tmp_path / "t.csv"
    assert journal.export_csv(path) == 1
    header = path.read_text().splitlines()[0].split(",")
    for f in SPEC_FIELDS:
        assert f in header


def test_state_and_llm_spend(journal: Journal):
    journal.set_state("halt", {"reason": "x"})
    assert journal.get_state("halt") == {"reason": "x"}
    journal.record_llm_usage("news", "claude-haiku-4-5", {"input": 1000, "output": 100}, 0.0015)
    assert abs(journal.llm_spend_today() - 0.0015) < 1e-9
