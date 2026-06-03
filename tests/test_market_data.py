import pytest

from codes.market_data import IndexStatus, MarketData, MarketDataSourceRequired, MarketSnapshot, OHLCVBar


class RawKISSource:
    def get_ohlcv(self, ticker: str, period: int = 60):
        return [
            {
                "stck_bsop_date": "20260601",
                "stck_oprc": "70000",
                "stck_hgpr": "72000",
                "stck_lwpr": "69000",
                "stck_clpr": "71000",
                "acml_vol": "123456",
                "acml_tr_pbmn": "8765432100",
            }
        ]

    def get_snapshot(self, ticker: str):
        return {
            "stck_prpr": "71000",
            "prdy_ctrt": "1.23",
            "acml_vol": "123456",
            "acml_tr_pbmn": "8765432100",
            "hts_kor_isnm": "삼성전자",
            "frgn_ntby_qty": "1000",
            "orgn_ntby_qty": "2000",
        }

    def get_index_status(self):
        return {
            "kospi": "2800.50",
            "kosdaq": "900.25",
            "kospi_change_pct": "0.75",
            "kosdaq_change_pct": "1.05",
        }

    def get_universe(self):
        return ["005930", {"code": "000660"}, {"ticker": "035420"}]


def test_market_data_requires_real_source():
    market_data = MarketData()

    with pytest.raises(MarketDataSourceRequired):
        market_data.get_universe()

    with pytest.raises(MarketDataSourceRequired):
        market_data.get_snapshot("005930")

    with pytest.raises(MarketDataSourceRequired):
        market_data.get_ohlcv("005930")

    with pytest.raises(MarketDataSourceRequired):
        market_data.get_index_status()


def test_market_data_converts_kis_raw_ohlcv_to_bars():
    bars = MarketData(RawKISSource()).get_ohlcv("005930")

    assert bars == [
        OHLCVBar(
            date="20260601",
            open=70000.0,
            high=72000.0,
            low=69000.0,
            close=71000.0,
            volume=123456,
            trading_value=8765432100.0,
        )
    ]


def test_market_data_converts_kis_raw_snapshot():
    snapshot = MarketData(RawKISSource()).get_snapshot("005930")

    assert snapshot == MarketSnapshot(
        ticker="005930",
        name="삼성전자",
        current_price=71000.0,
        change_pct=1.23,
        volume=123456,
        trading_value=8765432100.0,
        foreign_net=1000.0,
        institution_net=2000.0,
    )


def test_market_data_converts_raw_index_status_and_universe():
    market_data = MarketData(RawKISSource())

    assert market_data.get_index_status() == IndexStatus(
        kospi=2800.50,
        kosdaq=900.25,
        kospi_change_pct=0.75,
        kosdaq_change_pct=1.05,
    )
    assert market_data.get_universe() == ["005930", "000660", "035420"]
