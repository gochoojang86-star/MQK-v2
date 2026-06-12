"""telegram_news 종목 매핑 테스트"""
import broker.telegram_news as tn


def _make_csv(tmp_path, rows):
    p = tmp_path / "universe.csv"
    lines = ["ticker,name,market,standard_code"] + [f"{t},{n},KOSPI,KR" for t, n in rows]
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def test_extract_ticker_from_full_universe(tmp_path, monkeypatch):
    csv = _make_csv(tmp_path, [("000250", "삼천당제약"), ("005930", "삼성전자"), ("005935", "삼성전자우")])
    monkeypatch.setattr(tn, "_NAME_MAP", None)
    monkeypatch.setattr(tn, "_UNIVERSE_CSV", csv)
    tn._load_name_map(path=csv, force=True)

    assert tn._extract_ticker("삼천당제약, 점안제 유럽 공급 계약") == "000250"
    # 긴 이름 우선 — 우선주가 본주로 오매핑되지 않아야 한다
    assert tn._extract_ticker("삼성전자우 배당 확대") == "005935"
    assert tn._extract_ticker("삼성전자 신규 수주") == "005930"
    assert tn._extract_ticker("매핑 안 되는 잡담") == ""


def test_extract_ticker_url_code_wins(tmp_path, monkeypatch):
    csv = _make_csv(tmp_path, [("005930", "삼성전자")])
    monkeypatch.setattr(tn, "_NAME_MAP", None)
    tn._load_name_map(path=csv, force=True)

    text = "카티스 수주 https://m.stock.naver.com/investment/261900 삼성전자도 언급"
    assert tn._extract_ticker(text) == "261900"  # 본문 6자리 코드가 이름 매칭보다 우선


def test_name_map_falls_back_to_company_map(tmp_path, monkeypatch):
    missing = tmp_path / "no_such.csv"
    pairs = tn._load_name_map(path=missing, force=True)
    assert ("삼성전자", "005930") in pairs  # COMPANY_MAP 폴백
