from scripts.update_universe import parse_master


def test_parse_master_extracts_ticker_name_and_market():
    line = "005930   KR7005930003삼성전자".ljust(80) + (" " * 228)
    rows = parse_master((line + "\n").encode("cp949"), market="KOSPI", meta_len=228)

    assert rows == [
        {
            "ticker": "005930",
            "name": "삼성전자",
            "market": "KOSPI",
            "standard_code": "KR7005930003",
        }
    ]
