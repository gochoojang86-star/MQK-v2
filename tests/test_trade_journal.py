from datetime import datetime

import pytest
from pathlib import Path
from codes.trade_journal import TradeJournal


@pytest.fixture
def journal(tmp_path):
    return TradeJournal(db_path=tmp_path / "trades.db")


from datetime import datetime, timedelta

_TODAY = datetime.now().strftime("%Y-%m-%d")
_YESTERDAY = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")


def test_open_and_close_trade(journal):
    journal.open_trade(
        ticker="005930",
        name="삼성전자",
        entry_date=_YESTERDAY,
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
        exit_date=_TODAY,
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


def test_partial_close_keeps_remaining_position_and_marks_target1(journal):
    journal.open_trade(
        ticker="005930",
        name="삼성전자",
        entry_date=_YESTERDAY,
        entry_price=75000,
        quantity=10,
        stop_loss_price=73000,
        entry_reason="VCP 돌파",
        confidence=80,
    )

    journal.close_trade(
        ticker="005930",
        exit_date=_TODAY,
        exit_price=78000,
        exit_reason="TARGET_1",
        quantity=5,
    )

    open_pos = journal.get_open_positions()
    assert len(open_pos) == 1
    assert open_pos[0]["quantity"] == 5
    assert open_pos[0]["target1_hit"] == 1
    assert journal.get_closed_trades(days=7) == []
    executions = journal.get_trade_executions("005930")
    assert len(executions) == 1
    assert executions[0]["quantity"] == 5
    assert executions[0]["realized_pnl"] == pytest.approx((78000 - 75000) * 5)


def test_update_position_management_never_lowers_stop(journal):
    journal.open_trade(
        "005930",
        "삼성전자",
        _YESTERDAY,
        75000,
        10,
        73000,
        "VCP 돌파",
        80,
    )

    journal.update_position_management("005930", stop_loss_price=72000, highest_price=80000)
    journal.update_position_management("005930", stop_loss_price=75000, highest_price=79000)

    pos = journal.get_open_positions()[0]
    assert pos["stop_loss_price"] == 75000
    assert pos["highest_price"] == 80000


def test_update_position_management_persists_trailing_state(journal):
    journal.open_trade(
        "005930",
        "삼성전자",
        _YESTERDAY,
        75000,
        10,
        73000,
        "VCP 돌파",
        80,
    )

    journal.update_position_management(
        "005930",
        highest_price=83000,
        target1_hit=True,
        trailing_active=True,
    )

    pos = journal.get_open_positions()[0]
    assert pos["highest_price"] == 83000
    assert pos["target1_hit"] == 1
    assert pos["trailing_active"] == 1


def test_daily_summary(journal):
    journal.open_trade(
        "000660",
        "SK하이닉스",
        _YESTERDAY,
        200000,
        5,
        195000,
        "신고가",
        75,
        "ORD002",
    )
    journal.close_trade("000660", _TODAY, 195000, "STOP_LOSS")
    summary = journal.get_daily_summary(_TODAY)
    assert summary["total_trades"] == 1
    assert summary["win_trades"] == 0
    assert summary["total_pnl"] < 0
    closed = journal.get_closed_trades(days=7)
    assert closed[0]["result"] == "LOSS"


def test_today_summary_no_trades(tmp_path):
    journal = TradeJournal(db_path=tmp_path / "trades.db")
    summary = journal.today_summary()
    assert summary["trade_count"] == 0
    assert summary["realized_pnl_pct"] == 0.0
    assert summary["win"] == 0
    assert summary["loss"] == 0
    assert summary["last_trade"] is None


def test_today_summary_with_closed_trade(tmp_path):
    journal = TradeJournal(db_path=tmp_path / "trades.db")
    today = datetime.now().strftime("%Y-%m-%d")

    journal.open_trade(
        ticker="005930",
        name="삼성전자",
        entry_date=today,
        entry_price=70000,
        quantity=10,
        stop_loss_price=67000,
        entry_reason="TREND",
        confidence=80,
    )
    journal.close_trade(
        ticker="005930",
        exit_date=today,
        exit_price=69440,
        exit_reason="STOP_LOSS",
    )

    summary = journal.today_summary()
    assert summary["trade_count"] == 1
    assert summary["loss"] == 1
    assert summary["win"] == 0
    assert summary["last_trade"]["ticker"] == "005930"
    assert summary["last_trade"]["result"] == "LOSS"
    assert summary["last_trade"]["pct"] < 0
