"""get_intraday_volume_trend 테스트"""
import pytest
from market_intelligence.base import MILContext
from market_intelligence.stock import get_intraday_volume_trend


class StubKisApi:
    def __init__(self, candles):
        self._candles = candles

    def raw_get(self, tr_id, path, params):
        # 10분봉 조회 응답 형식
        return {"output2": [
            {"stck_bsop_date": c["date"], "stck_cntg_hour": c["time"],
             "acml_tr_pbmn": str(int(c["vol"]))}
            for c in self._candles
        ]}


def _make_ctx(candles):
    return MILContext(kis_api=StubKisApi(candles))


def _candle(vol):
    return {"date": "20260623", "time": "0900", "vol": vol}


def test_volume_dry_when_recent_drops_40pct():
    # 직전 3봉 평균 1000억, 최근 3봉 평균 500억 → -50% → VOLUME_DRY
    candles = [
        _candle(400_0000_0000), _candle(500_0000_0000), _candle(600_0000_0000),  # 최근 3봉
        _candle(900_0000_0000), _candle(1000_0000_0000), _candle(1100_0000_0000),  # 직전 3봉
    ]
    ctx = _make_ctx(candles)
    result = get_intraday_volume_trend(ctx, "INTRADAY", "000660")
    assert result["signal"] == "VOLUME_DRY"
    assert result["trend"] == "DECLINING"


def test_stable_when_volume_maintained():
    # 직전 3봉 평균 1000억, 최근 3봉 평균 950억 → -5% → STABLE
    candles = [
        _candle(900_0000_0000), _candle(950_0000_0000), _candle(1000_0000_0000),
        _candle(950_0000_0000), _candle(1000_0000_0000), _candle(1050_0000_0000),
    ]
    ctx = _make_ctx(candles)
    result = get_intraday_volume_trend(ctx, "INTRADAY", "000660")
    assert result["signal"] is None
    assert result["trend"] == "STABLE"


def test_increasing_when_recent_higher():
    candles = [
        _candle(1200_0000_0000), _candle(1300_0000_0000), _candle(1400_0000_0000),
        _candle(800_0000_0000),  _candle(900_0000_0000),  _candle(1000_0000_0000),
    ]
    ctx = _make_ctx(candles)
    result = get_intraday_volume_trend(ctx, "INTRADAY", "000660")
    assert result["trend"] == "INCREASING"
    assert result["signal"] is None


def test_returns_ticker_in_result():
    candles = [_candle(100_0000_0000)] * 6
    ctx = _make_ctx(candles)
    result = get_intraday_volume_trend(ctx, "INTRADAY", "005930")
    assert result["ticker"] == "005930"
