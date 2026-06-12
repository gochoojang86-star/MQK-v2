"""Order Manager Code 테스트 - Telegram 승인 강제 + dry-run 경로"""
from datetime import datetime, timedelta

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
    om = OrderManager(dry_run=True, journal=journal, log_dir=tmp_path)
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
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    today = datetime.now().strftime("%Y-%m-%d")

    journal = TradeJournal(db_path=tmp_path / "trades.db")
    om = OrderManager(dry_run=True, journal=journal, log_dir=tmp_path)
    buy = OrderRequest("005930", "삼성전자", "BUY", 10, 75000, 73000, "VCP", 80,
                       approval_request_id="005930_1234567890", entry_date=yesterday)
    om.execute_buy(buy)
    sell = OrderRequest("005930", "삼성전자", "SELL", 10, 78000, 0, "TARGET_1", 0,
                        entry_date=today)
    om.execute_sell(sell)
    assert len(journal.get_open_positions()) == 0
    closed = journal.get_closed_trades(days=1)
    assert closed[0]["result"] == "WIN"


def test_list_open_orders_delegates_to_kis(tmp_path):
    class FakeKis:
        def __init__(self):
            self.side = None

        def get_open_orders(self, side=None):
            self.side = side
            return [{"order_no": "123456", "ticker": "005930"}]

    kis = FakeKis()
    manager = OrderManager(kis_api=kis, log_dir=tmp_path)

    orders = manager.list_open_orders(side="BUY")

    assert kis.side == "BUY"
    assert orders[0]["order_no"] == "123456"


def test_cancel_stale_orders_delegates_cancelable_orders(tmp_path):
    class FakeResult:
        success = True
        ticker = ""
        side = "CANCEL"
        quantity = 10
        price = 75000
        order_no = "123456"
        timestamp = "2026-06-04T09:00:00"
        error_msg = ""

    class FakeKis:
        def __init__(self):
            self.cancel_call = None

        def get_open_orders(self, side=None):
            return [{
                "order_no": "123456",
                "org_no": "00000",
                "cancelable_quantity": 10,
                "price": 75000,
                "order_type": "00",
            }]

        def cancel_order(self, **kwargs):
            self.cancel_call = kwargs
            return FakeResult()

    kis = FakeKis()
    manager = OrderManager(kis_api=kis, log_dir=tmp_path)

    results = manager.cancel_stale_orders()

    assert len(results) == 1
    assert results[0].success is True
    assert kis.cancel_call["order_no"] == "123456"
    assert kis.cancel_call["all_quantity"] is True

def test_execute_sell_after_hours_routes_to_close_price_order(tmp_path):
    class FakeKis:
        def __init__(self):
            self.after_hours_calls = []

        def sell_after_hours_close(self, ticker, quantity):
            self.after_hours_calls.append((ticker, quantity))
            from broker.kis_api import OrderResult
            return OrderResult(success=True, order_no="AH1", ticker=ticker,
                               quantity=quantity, price=230000.0, side="SELL",
                               timestamp="2026-06-12T15:42:00")

        def sell_market(self, ticker, quantity):
            raise AssertionError("after_hours 주문이 시장가로 라우팅되면 안 된다")

    journal = TradeJournal(db_path=tmp_path / "trades.db")
    seed = OrderManager(dry_run=True, journal=journal, log_dir=tmp_path)
    seed.execute_buy(OrderRequest("095340", "ISC", "BUY", 6, 243000, 205607, "test", 80,
                                   approval_request_id="REQ1"))
    kis = FakeKis()
    om = OrderManager(kis_api=kis, dry_run=False, journal=journal, log_dir=tmp_path)
    sell = OrderRequest("095340", "ISC", "SELL", 6, 230000, 0, "손절", 100, after_hours=True)
    result = om.execute_sell(sell)

    assert result.success is True
    assert kis.after_hours_calls == [("095340", 6)]
    assert journal.get_open_positions() == []

