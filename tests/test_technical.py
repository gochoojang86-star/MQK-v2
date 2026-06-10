"""Technical Code 테스트"""
from codes.technical import TechnicalAnalysis
from codes.market_data import OHLCVBar


def make_bars(count=60, base_price=50000, trend="up"):
    bars = []
    price = base_price
    for i in range(count):
        if trend == "up":
            price *= 1.005
        elif trend == "down":
            price *= 0.995
        bars.append(OHLCVBar(
            date=f"2026-01-{i+1:02d}",
            open=price * 0.99,
            high=price * 1.02,
            low=price * 0.98,
            close=price,
            volume=500_000,
            trading_value=price * 500_000,
        ))
    return bars


def test_atr_positive():
    ta = TechnicalAnalysis()
    bars = make_bars(20)
    atr = ta.calculate_atr(bars)
    assert atr > 0


def test_rsi_in_range():
    ta = TechnicalAnalysis()
    bars = make_bars(30)
    closes = [b.close for b in bars]
    rsi = ta.calculate_rsi(closes)
    assert 0 <= rsi <= 100


def test_ma_calculation():
    ta = TechnicalAnalysis()
    closes = [float(i) for i in range(1, 26)]
    ma20 = ta.calculate_ma(closes, 20)
    assert ma20 == sum(closes[-20:]) / 20


def test_analyze_full():
    ta = TechnicalAnalysis()
    bars = make_bars(130)
    signals = ta.analyze("005930", bars)
    assert signals.ticker == "005930"
    assert signals.atr > 0
    assert 0 <= signals.rsi <= 100
    assert isinstance(signals.is_vcp, bool)
    assert isinstance(signals.new_high_52w, bool)


def test_52w_high_detected():
    ta = TechnicalAnalysis()
    bars = make_bars(260, trend="up")
    highs = [b.high for b in bars]
    assert ta.is_52w_high(highs) is True


def test_52w_high_not_detected():
    ta = TechnicalAnalysis()
    bars = make_bars(260, trend="down")
    highs = [b.high for b in bars]
    assert ta.is_52w_high(highs) is False


def test_disparity_calculation():
    ta = TechnicalAnalysis()
    assert ta.calculate_disparity_pct(90, 100) == -10.0
    assert ta.calculate_disparity_pct(100, None) == 0.0
