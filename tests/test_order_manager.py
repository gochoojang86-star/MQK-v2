"""Order Manager Code 테스트 - Telegram 승인 강제 + dry-run 경로"""
import pytest
from codes.order_manager import OrderManager, OrderRequest
from codes.trade_journal import TradeJournal


def make_order(**kwargs):
    defaults = {
        "ticker": "005930",
        "name": "삼성전자",
        "side": "BUY",
        "quantity": 10,
        "price": 70000.0,
        "stop_loss_price": 67000.0,
        "reason": "테스트",
        "confidence": 80,
        "approval_request_id": "005930_1234567890",
    }
    defaults.update(kwargs)
    return OrderRequest(**defaults)


def test_buy_without_telegram_approval_raises(tmp_path):
    manager = OrderManager(kis_api=None, dry_run=True, log_dir=tmp_path)
    order = make_order(approval_request_id=None)
    with pytest.raises(PermissionError):
        manager.execute_buy(order)


def test_buy_without_kis_requires_explicit_dry_run(tmp_path):
    manager = OrderManager(kis_api=None, log_dir=tmp_path)
    order = make_order()
    with pytest.raises(RuntimeError, match="dry_run"):
        manager.execute_buy(order)


def test_buy_dry_run_succeeds(tmp_path):
    manager = OrderManager(kis_api=None, dry_run=True, log_dir=tmp_path)
    order = make_order()
    result = manager.execute_buy(order)
    assert result.success is True
    assert result.order_no == "DRY_RUN"
    assert result.ticker == "005930"


def test_sell_dry_run_succeeds(tmp_path):
    manager = OrderManager(kis_api=None, dry_run=True, log_dir=tmp_path)
    order = make_order(side="SELL")
    result = manager.execute_sell(order)
    assert result.success is True
    assert result.side == "SELL"


def test_buy_logs_to_file(tmp_path):
    manager = OrderManager(kis_api=None, dry_run=True, log_dir=tmp_path)
    order = make_order()
    manager.execute_buy(order)
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    log_path = tmp_path / today / "orders.jsonl"
    assert log_path.exists()
    content = log_path.read_text(encoding="utf-8")
    assert "005930" in content


def test_execute_buy_records_to_journal(tmp_path):
    journal = TradeJournal(db_path=tmp_path / "trades.db")
    om = OrderManager(dry_run=True, journal=journal)
    order = OrderRequest(
        ticker="005930", name="삼성전자", side="BUY",
        quantity=10, price=75000, stop_loss_price=73000,
        reason="VCP", confidence=80,
        approval_request_id="005930_1234567890",
        entry_date="2026-06-04",
    )
    om.execute_buy(order)
    assert len(journal.get_open_positions()) == 1


def test_execute_sell_closes_journal(tmp_path):
    journal = TradeJournal(db_path=tmp_path / "trades.db")
    om = OrderManager(dry_run=True, journal=journal)
    buy = OrderRequest("005930", "삼성전자", "BUY", 10, 75000, 73000, "VCP", 80,
                       approval_request_id="005930_1234567890", entry_date="2026-06-04")
    om.execute_buy(buy)
    sell = OrderRequest("005930", "삼성전자", "SELL", 10, 78000, 0, "TARGET_1", 0,
                        entry_date="2026-06-05")
    om.execute_sell(sell)
    assert len(journal.get_open_positions()) == 0
    closed = journal.get_closed_trades(days=1)
    assert closed[0]["result"] == "WIN"
