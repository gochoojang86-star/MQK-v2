"""Scanner Code 테스트 - 5000→30 필터링 + 거래대금 컷오프"""
from codes.scanner import Scanner, CandidateScore
from codes.market_data import MarketSnapshot


def make_snapshot(ticker: str, trading_value: float, change_pct: float = 1.0) -> MarketSnapshot:
    return MarketSnapshot(
        ticker=ticker,
        name=f"종목_{ticker}",
        current_price=50000.0,
        change_pct=change_pct,
        volume=100000,
        trading_value=trading_value,
        foreign_net=0,
        institution_net=0,
    )


def test_trading_value_filter_removes_low_volume():
    scanner = Scanner()
    low_value = make_snapshot("A001", trading_value=1_000_000_000)   # 10억 (미달)
    high_value = make_snapshot("A002", trading_value=50_000_000_000) # 500억 (통과)
    result = scanner.scan([low_value, high_value], {}, {})
    tickers = [c.ticker for c in result]
    assert "A001" not in tickers
    assert "A002" in tickers


def test_candidate_count_capped_at_30():
    scanner = Scanner()
    # 50개 고거래대금 종목 생성
    snapshots = [
        make_snapshot(f"B{i:03d}", trading_value=50_000_000_000)
        for i in range(50)
    ]
    result = scanner.scan(snapshots, {}, {})
    assert len(result) <= 30


def test_empty_universe_returns_empty():
    scanner = Scanner()
    result = scanner.scan([], {}, {})
    assert result == []


def test_score_ordering():
    scanner = Scanner()
    low = make_snapshot("LOW", trading_value=6_000_000_000)
    high = make_snapshot("HIGH", trading_value=200_000_000_000)
    result = scanner.scan([low, high], {}, {})
    assert result[0].total_score >= result[-1].total_score
