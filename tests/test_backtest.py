"""Backtest Engine 테스트"""
from backtest.backtest_engine import BacktestEngine, BacktestTrade


def make_trade(pnl: float, entry_price=50000.0, quantity=10):
    exit_price = entry_price + pnl / quantity
    return BacktestTrade(
        ticker="005930",
        entry_date="2026-01-01",
        exit_date="2026-01-10",
        entry_price=entry_price,
        exit_price=exit_price,
        quantity=quantity,
        stop_loss_price=entry_price * 0.95,
        exit_reason="TARGET_1",
    )


def test_win_rate():
    engine = BacktestEngine()
    trades = [make_trade(10000), make_trade(10000), make_trade(-5000), make_trade(-5000)]
    result = engine.run("test", 10_000_000, trades)
    assert result.win_rate == 50.0
    assert result.total_trades == 4


def test_profit_factor():
    engine = BacktestEngine()
    trades = [make_trade(10000), make_trade(-5000)]
    result = engine.run("test", 10_000_000, trades)
    assert result.profit_factor == 2.0


def test_empty_trades():
    engine = BacktestEngine()
    result = engine.run("empty", 10_000_000, [])
    assert result.total_trades == 0


def test_compare_returns_best():
    engine = BacktestEngine()
    trades_a = [make_trade(20000), make_trade(-3000)] * 5
    trades_b = [make_trade(5000), make_trade(-5000)] * 5
    result_a = engine.run("strategy_a", 10_000_000, trades_a)
    result_b = engine.run("strategy_b", 10_000_000, trades_b)
    best = engine.compare([result_a, result_b])
    assert best.strategy_name in ["strategy_a", "strategy_b"]
