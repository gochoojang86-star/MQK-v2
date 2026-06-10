import json
import time
from dataclasses import dataclass

from broker.kis_api import KISApi


@dataclass
class FakeKISConfig:
    mode: str = "paper"
    app_key: str = "app-key"
    app_secret: str = "app-secret"
    account_no: str = "12345678-01"
    base_url: str = "https://example.test"

    def app_key_for(self, mode: str) -> str:
        return self.app_key

    def app_secret_for(self, mode: str) -> str:
        return self.app_secret

    def account_no_for(self, mode: str) -> str:
        return self.account_no

    def base_url_for(self, mode: str) -> str:
        if mode == "real":
            return "https://openapi.koreainvestment.com:9443"
        return "https://openapivts.koreainvestment.com:29443"


def test_get_token_uses_valid_file_cache(tmp_path):
    cache_path = tmp_path / "kis_token.json"
    cache_path.write_text(
        json.dumps({
            "access_token": "cached-token",
            "expires_at": time.time() + 3600,
        }),
        encoding="utf-8",
    )
    api = KISApi(config=FakeKISConfig(), token_cache_path=cache_path)

    assert api._get_token() == "cached-token"


def test_get_token_saves_new_token_to_cache(tmp_path, monkeypatch):
    cache_path = tmp_path / "kis_token.json"
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"access_token": "fresh-token", "expires_in": 7200}

    def fake_post(url, json, timeout):
        calls.append((url, json, timeout))
        return FakeResponse()

    monkeypatch.setattr("broker.kis_api.requests.post", fake_post)

    api = KISApi(config=FakeKISConfig(), token_cache_path=cache_path)

    assert api._get_token() == "fresh-token"
    assert len(calls) == 1
    cached = json.loads(cache_path.read_text(encoding="utf-8"))
    assert cached["access_token"] == "fresh-token"
    assert cached["expires_at"] > time.time()


def test_get_index_quote_retries_transient_server_error(tmp_path, monkeypatch):
    cache_path = tmp_path / "kis_token.json"
    cache_path.write_text(
        json.dumps({
            "access_token": "cached-token",
            "expires_at": time.time() + 3600,
        }),
        encoding="utf-8",
    )
    calls = []

    class FakeResponse:
        def __init__(self, status_code):
            self.status_code = status_code

        def raise_for_status(self):
            if self.status_code >= 500:
                import requests
                raise requests.HTTPError(f"{self.status_code} Server Error")

        def json(self):
            return {
                "rt_cd": "0",
                "output": {
                    "bstp_nmix_prpr": "2800.50",
                    "bstp_nmix_prdy_ctrt": "0.75",
                },
            }

    def fake_get(url, headers, params, timeout):
        calls.append((url, headers, params, timeout))
        return FakeResponse(500 if len(calls) == 1 else 200)

    monkeypatch.setattr("broker.kis_api.requests.get", fake_get)
    monkeypatch.setattr("broker.kis_api.time.sleep", lambda _: None)

    api = KISApi(config=FakeKISConfig(), token_cache_path=cache_path)

    assert api._get_index_quote("0001")["bstp_nmix_prpr"] == "2800.50"
    assert len(calls) == 2


def test_get_prev_index_day_skips_today_zero_row_and_computes_change_pct(tmp_path, monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "rt_cd": "0",
                "output2": [
                    {
                        "stck_bsop_date": "20260609",
                        "bstp_nmix_prpr": "7484.41",
                        "acml_vol": "0",
                        "acml_tr_pbmn": "0",
                    },
                    {
                        "stck_bsop_date": "20260608",
                        "bstp_nmix_prpr": "7484.41",
                        "acml_vol": "452204",
                        "acml_tr_pbmn": "48338891",
                    },
                    {
                        "stck_bsop_date": "20260605",
                        "bstp_nmix_prpr": "8160.59",
                        "acml_vol": "463197",
                        "acml_tr_pbmn": "48519528",
                    },
                ],
            }

    monkeypatch.setattr("broker.kis_api.requests.get", lambda *args, **kwargs: FakeResponse())
    monkeypatch.setattr(KISApi, "_get_token", lambda self, mode=None: "token")
    api = KISApi(config=FakeKISConfig(), token_cache_path=tmp_path / "token.json")
    api._data_mode = "real"

    prev = api._get_prev_index_day("0001")

    assert prev["stck_bsop_date"] == "20260608"
    assert prev["acml_tr_pbmn"] == "48338891"
    assert prev["prdy_ctrt"] == -8.29


def test_get_universe_loads_codes_from_file(tmp_path, monkeypatch):
    universe = tmp_path / "universe.csv"
    universe.write_text(
        "ticker,name\n005930,삼성전자\n000660,SK하이닉스\n#comment\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("KIS_UNIVERSE", raising=False)
    monkeypatch.setenv("KIS_UNIVERSE_FILE", str(universe))

    api = KISApi(config=FakeKISConfig(), token_cache_path=tmp_path / "token.json")

    assert api.get_universe() == ["005930", "000660"]


def test_get_stock_info_parses_real_only_basic_info(tmp_path, monkeypatch):
    cache_path = tmp_path / "kis_token_paper.json"
    (tmp_path / "kis_token_real.json").write_text(
        json.dumps({
            "access_token": "cached-real-token",
            "expires_at": time.time() + 3600,
        }),
        encoding="utf-8",
    )

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "rt_cd": "0",
                "output": {
                    "pdno": "00000A005930",
                    "prdt_name": "삼성전자보통주",
                    "prdt_abrv_name": "삼성전자",
                    "lstg_stqt": "5846278608",
                    "mket_id_cd": "STK",
                    "scty_grp_id_cd": "ST",
                    "idx_bztp_scls_cd_name": "전기,전자",
                    "tr_stop_yn": "N",
                    "admn_item_yn": "N",
                },
            }

    calls = []

    def fake_get(url, headers, params, timeout):
        calls.append((url, headers, params, timeout))
        return FakeResponse()

    monkeypatch.setattr("broker.kis_api.requests.get", fake_get)
    api = KISApi(config=FakeKISConfig(), token_cache_path=cache_path)
    api._data_mode = "real"

    info = api.get_stock_info("005930")
    cached = api.get_stock_info("005930")

    assert info["name"] == "삼성전자"
    assert info["sector"] == "전기,전자"
    assert info["listed_shares"] == "5846278608"
    assert info["trading_halted"] is False
    assert info["administrative_issue"] is False
    assert cached == info
    assert len(calls) == 1


def test_get_investor_flow_history_parses_rows(tmp_path, monkeypatch):
    cache_path = tmp_path / "kis_token.json"
    cache_path.write_text(
        json.dumps({
            "access_token": "cached-token",
            "expires_at": time.time() + 3600,
        }),
        encoding="utf-8",
    )

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "output": [
                    {
                        "stck_bsop_date": "20260601",
                        "frgn_ntby_qty": "100",
                        "orgn_ntby_qty": "200",
                        "pgtr_ntby_qty": "300",
                        "acml_tr_pbmn": "1000",
                    },
                    {
                        "stck_bsop_date": "20260602",
                        "frgn_ntby_qty": "110",
                        "orgn_ntby_qty": "210",
                        "pgtr_ntby_qty": "310",
                        "acml_tr_pbmn": "1100",
                    },
                ]
            }

    monkeypatch.setattr("broker.kis_api.requests.get", lambda *args, **kwargs: FakeResponse())
    api = KISApi(config=FakeKISConfig(), token_cache_path=cache_path)

    records = api.get_investor_flow_history("005930", days=3)

    assert len(records) == 2
    assert records[0]["foreign_net"] == 100
    assert records[1]["program_net"] == 0


def test_coerce_flow_row_prefers_trade_amount_over_quantity():
    api = KISApi(config=FakeKISConfig())

    row = {
        "stck_bsop_date": "20260604",
        "frgn_ntby_qty": "-12414744",
        "orgn_ntby_qty": "3271194",
        "frgn_ntby_tr_pbmn": "-4394220",
        "orgn_ntby_tr_pbmn": "1160064",
        "prsn_ntby_tr_pbmn": "3177117",
        "prsn_shnu_tr_pbmn": "4697269",
        "frgn_shnu_tr_pbmn": "3345348",
        "orgn_shnu_tr_pbmn": "4003432",
    }

    record = api._coerce_flow_row("005930", row)

    assert record["foreign_net"] == -4_394_220_000_000
    assert record["institution_net"] == 1_160_064_000_000
    assert record["individual_net"] == 3_177_117_000_000
    assert record["trading_value"] == 12_046_049_000_000


def test_sanitize_flow_record_corrects_million_unit_overflow():
    api = KISApi(config=FakeKISConfig())
    record = {
        "date": "20260604",
        "ticker": "005930",
        "foreign_net": 4_000_000_000_000_000,
        "institution_net": 100_000_000,
        "individual_net": 0,
        "program_net": 0,
        "trading_value": 4_000_000_000_000,
    }

    api._sanitize_flow_record(record)

    assert record["foreign_net"] == 4_000_000_000
    assert record["institution_net"] == 100_000_000


def test_coerce_program_row_uses_program_trade_amount():
    api = KISApi(config=FakeKISConfig())

    record = api._coerce_program_row("005930", {
        "stck_bsop_date": "20240517",
        "acml_tr_pbmn": "1220563293000",
        "whol_smtn_ntby_tr_pbmn": "-266814763800",
    })

    assert record["program_net"] == -266_814_763_800
    assert record["trading_value"] == 1_220_563_293_000


def test_theme_seed_tickers_combines_rankings_without_duplicates(tmp_path, monkeypatch):
    cache_path = tmp_path / "kis_token_paper.json"
    (tmp_path / "kis_token_real.json").write_text(
        json.dumps({
            "access_token": "cached-real-token",
            "expires_at": time.time() + 3600,
        }),
        encoding="utf-8",
    )

    class FakeResponse:
        def __init__(self, output):
            self._output = output

        def raise_for_status(self):
            return None

        def json(self):
            return {"rt_cd": "0", "output": self._output}

    def fake_get(url, headers, params, timeout):
        if "fluctuation" in url:
            return FakeResponse([
                {"stck_shrn_iscd": "111111"},
                {"stck_shrn_iscd": "222222"},
            ])
        return FakeResponse([
            {"mksc_shrn_iscd": "222222"},
            {"mksc_shrn_iscd": "333333"},
        ])

    monkeypatch.setattr("broker.kis_api.requests.get", fake_get)
    api = KISApi(config=FakeKISConfig(), token_cache_path=cache_path)
    api._data_mode = "real"

    assert api.get_theme_seed_tickers(limit=10) == ["111111", "222222", "333333"]


def test_get_open_orders_uses_real_admin_endpoint_by_default(tmp_path, monkeypatch):
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "rt_cd": "0",
                "output": [
                    {
                        "ord_gno_brno": "00000",
                        "odno": "123456",
                        "pdno": "005930",
                        "prdt_name": "삼성전자",
                        "ord_qty": "10",
                        "tot_ccld_qty": "0",
                        "psbl_qty": "10",
                        "ord_unpr": "75000",
                        "sll_buy_dvsn_cd": "02",
                        "ord_dvsn_cd": "00",
                    }
                ],
            }

    def fake_get(url, headers, params, timeout):
        calls.append((url, headers, params, timeout))
        return FakeResponse()

    monkeypatch.setattr("broker.kis_api.requests.get", fake_get)
    monkeypatch.setattr(KISApi, "_get_token", lambda self, mode=None: "token")
    api = KISApi(config=FakeKISConfig(), token_cache_path=tmp_path / "token.json")

    orders = api.get_open_orders(side="BUY")

    assert calls[0][1]["tr_id"] == "TTTC0084R"
    assert "openapi.koreainvestment.com:9443" in calls[0][0]
    assert calls[0][2]["INQR_DVSN_2"] == "2"
    assert orders[0]["order_no"] == "123456"
    assert orders[0]["side"] == "BUY"
    assert orders[0]["cancelable_quantity"] == 10


def test_cancel_order_uses_real_admin_endpoint_by_default(tmp_path, monkeypatch):
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"rt_cd": "0", "output": {"ODNO": "123456"}}

    def fake_post(url, headers, json, timeout):
        calls.append((url, headers, json, timeout))
        return FakeResponse()

    monkeypatch.setattr("broker.kis_api.requests.post", fake_post)
    monkeypatch.setattr(KISApi, "_get_token", lambda self, mode=None: "token")
    api = KISApi(config=FakeKISConfig(), token_cache_path=tmp_path / "token.json")

    result = api.cancel_order("123456", org_no="00000", all_quantity=True)

    assert result.success is True
    assert result.side == "CANCEL"
    assert calls[0][1]["tr_id"] == "TTTC0013U"
    assert "openapi.koreainvestment.com:9443" in calls[0][0]
    assert calls[0][2]["RVSE_CNCL_DVSN_CD"] == "02"
    assert calls[0][2]["QTY_ALL_ORD_YN"] == "Y"


def test_raw_get_calls_kis_api_with_tr_id_and_returns_json(tmp_path, monkeypatch):
    cache_path = tmp_path / "kis_token.json"
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"rt_cd": "0", "output": {"foo": "bar"}}

    def fake_get(url, headers, params, timeout):
        calls.append((url, headers, params, timeout))
        return FakeResponse()

    monkeypatch.setattr("broker.kis_api.requests.get", fake_get)
    monkeypatch.setattr(KISApi, "_get_token", lambda self, mode=None: "token")

    api = KISApi(config=FakeKISConfig(), token_cache_path=cache_path)
    result = api.raw_get(
        "FHPUP02140000",
        "domestic-stock/v1/quotations/inquire-index-category-price",
        {"FID_COND_MRKT_DIV_CODE": "U"},
    )

    assert result == {"rt_cd": "0", "output": {"foo": "bar"}}
    url, headers, params, timeout = calls[0]
    assert url.endswith("/uapi/domestic-stock/v1/quotations/inquire-index-category-price")
    assert headers["tr_id"] == "FHPUP02140000"
    assert params == {"FID_COND_MRKT_DIV_CODE": "U"}
