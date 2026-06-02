"""Order Manager Code 테스트 - Telegram 승인 강제 + dry-run 경로"""
import pytest
from codes.order_manager import OrderManager, OrderRequest


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
        "approved_by": "telegram",
    }
    defaults.update(kwargs)
    return OrderRequest(**defaults)


def test_buy_without_telegram_approval_raises(tmp_path):
    manager = OrderManager(kis_api=None, log_dir=tmp_path)
    order = make_order(approved_by="manual")  # telegram 아님
    with pytest.raises(PermissionError):
        manager.execute_buy(order)


def test_buy_dry_run_succeeds(tmp_path):
    manager = OrderManager(kis_api=None, log_dir=tmp_path)
    order = make_order(approved_by="telegram")
    result = manager.execute_buy(order)
    assert result.success is True
    assert result.order_no == "DRY_RUN"
    assert result.ticker == "005930"


def test_sell_dry_run_succeeds(tmp_path):
    manager = OrderManager(kis_api=None, log_dir=tmp_path)
    order = make_order(side="SELL", approved_by="telegram")
    result = manager.execute_sell(order)
    assert result.success is True
    assert result.side == "SELL"


def test_buy_logs_to_file(tmp_path):
    manager = OrderManager(kis_api=None, log_dir=tmp_path)
    order = make_order(approved_by="telegram")
    manager.execute_buy(order)
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    log_path = tmp_path / today / "orders.jsonl"
    assert log_path.exists()
    content = log_path.read_text(encoding="utf-8")
    assert "005930" in content
