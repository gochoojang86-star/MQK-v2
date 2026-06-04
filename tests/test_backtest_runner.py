"""
Test BacktestRunner - historical_loader와 strategy_runner 테스트
"""
from backtest.historical_loader import HistoricalLoader
from backtest.strategy_runner import StrategyRunner, StrategyConfig
from codes.market_data import OHLCVBar
from backtest.backtest_engine import BacktestEngine


def _make_bars(prices: list[float]) -> list[OHLCVBar]:
    return [
        OHLCVBar(
            date=f"2026-{str(i+1).zfill(2)}-01",
            open=p, high=p * 1.02, low=p * 0.98,
            close=p, volume=1_000_000, trading_value=p * 1_000_000,
        )
        for i, p in enumerate(prices)
    ]


def test_strategy_runner_no_crash():
    bars = _make_bars([100, 105, 110, 108, 115, 112, 120, 118, 125, 130])
    cfg = StrategyConfig(
        entry_condition="new_high",
        stop_atr_multiplier=1.5,
        target1_ratio=1.5,
        target2_ratio=3.0,
        risk_per_trade_pct=0.5,
    )
    runner = StrategyRunner(config=cfg)
    trades = runner.run(ticker="TEST", bars=bars, initial_capital=10_000_000)
    assert isinstance(trades, list)


def test_backtest_engine_with_runner_output():
    bars = _make_bars([100, 105, 110, 108, 115, 112, 120, 118, 125, 130])
    cfg = StrategyConfig(entry_condition="new_high", stop_atr_multiplier=1.5,
                         target1_ratio=1.5, target2_ratio=3.0, risk_per_trade_pct=0.5)
    runner = StrategyRunner(config=cfg)
    bt_trades = runner.run("TEST", bars, 10_000_000)
    engine = BacktestEngine()
    result = engine.run("신고가전략", 10_000_000, bt_trades)
    assert result.total_trades == len(bt_trades)
    assert 0 <= result.win_rate <= 100


def test_historical_loader_cache(tmp_path):
    loader = HistoricalLoader(cache_dir=tmp_path)
    bars = _make_bars([100, 105, 110])
    loader.save_cache("005930", bars)
    loaded = loader.load_cache("005930")
    assert len(loaded) == 3
    assert loaded[0].close == 100
