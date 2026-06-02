"""Stop TakeProfit Code 테스트"""
from codes.stop_take_profit import StopTakeProfitManager, PositionStatus, ExitSignal, StopTakeProfitConfig


def make_position(**kwargs):
    defaults = {
        "ticker": "005930",
        "entry_price": 50000,
        "stop_loss_price": 47000,
        "quantity": 10,
        "atr": 1000,
        "highest_price": 50000,
        "target1_hit": False,
        "trailing_active": False,
        "config": StopTakeProfitConfig(),
    }
    defaults.update(kwargs)
    return PositionStatus(**defaults)


def test_stop_loss_triggered():
    mgr = StopTakeProfitManager()
    pos = make_position(entry_price=50000, stop_loss_price=47000)
    signal = mgr.evaluate(pos, current_price=46999)
    assert signal == ExitSignal.STOP_LOSS


def test_hold_at_normal_price():
    mgr = StopTakeProfitManager()
    pos = make_position(entry_price=50000, stop_loss_price=47000)
    signal = mgr.evaluate(pos, current_price=51000)
    assert signal == ExitSignal.HOLD


def test_target1_triggered():
    mgr = StopTakeProfitManager()
    # stop_distance = 3000, target1 = 50000 + 3000*1.5 = 54500
    pos = make_position(entry_price=50000, stop_loss_price=47000)
    signal = mgr.evaluate(pos, current_price=54500)
    assert signal == ExitSignal.TARGET_1


def test_target2_triggered_after_target1():
    mgr = StopTakeProfitManager()
    # stop_distance = 3000, target2 = 50000 + 3000*3.0 = 59000
    pos = make_position(entry_price=50000, stop_loss_price=47000, target1_hit=True)
    signal = mgr.evaluate(pos, current_price=59000)
    assert signal == ExitSignal.TARGET_2


def test_get_targets():
    mgr = StopTakeProfitManager()
    targets = mgr.get_targets(50000, 47000)
    assert targets["stop_loss"] == 47000
    assert targets["target1"] == 50000 + 3000 * 1.5
    assert targets["target2"] == 50000 + 3000 * 3.0
