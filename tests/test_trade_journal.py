import pytest
from pathlib import Path
from codes.trade_journal import TradeJournal


@pytest.fixture
def journal(tmp_path):
    return TradeJournal(db_path=tmp_path / "trades.db")


def test_open_and_close_trade(journal):
    journal.open_trade(
        ticker="005930",
        name="삼성전자",
        entry_date="2026-06-04",
        entry_price=75000,
        quantity=10,
        stop_loss_price=73000,
        entry_reason="VCP 돌파",
        confidence=80,
        order_no="ORD001",
    )
    open_pos = journal.get_open_positions()
    assert len(open_pos) == 1
    assert open_pos[0]["ticker"] == "005930"

    journal.close_trade(
        ticker="005930",
        exit_date="2026-06-05",
        exit_price=78000,
        exit_reason="TARGET_1",
    )
    closed = journal.get_closed_trades(days=7)
    assert len(closed) == 1
    assert closed[0]["pnl"] == pytest.approx((78000 - 75000) * 10)
    assert closed[0]["result"] == "WIN"
    assert len(journal.get_open_positions()) == 0


def test_get_open_positions_empty(journal):
    assert journal.get_open_positions() == []


def test_daily_summary(journal):
    journal.open_trade(
        "000660",
        "SK하이닉스",
        "2026-06-04",
        200000,
        5,
        195000,
        "신고가",
        75,
        "ORD002",
    )
    journal.close_trade("000660", "2026-06-05", 195000, "STOP_LOSS")
    summary = journal.get_daily_summary("2026-06-05")
    assert summary["total_trades"] == 1
    assert summary["win_trades"] == 0
    assert summary["total_pnl"] < 0
    closed = journal.get_closed_trades(days=7)
    assert closed[0]["result"] == "LOSS"
