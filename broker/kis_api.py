"""
KIS API Broker - 한국투자증권 API 연동
실전(production) / 모의(paper) 모드 자동 전환
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


class KISMode:
    REAL = "real"
    PAPER = "paper"


@dataclass
class KISConfig:
    mode: str = KISMode.PAPER  # 기본값: 모의투자 (안전)

    @property
    def app_key(self) -> str:
        if self.mode == KISMode.REAL:
            return os.environ["KIS_REAL_APP_KEY"]
        return os.environ["KIS_PAPER_APP_KEY"]

    @property
    def app_secret(self) -> str:
        if self.mode == KISMode.REAL:
            return os.environ["KIS_REAL_APP_SECRET"]
        return os.environ["KIS_PAPER_APP_SECRET"]

    @property
    def account_no(self) -> str:
        if self.mode == KISMode.REAL:
            return os.environ["KIS_REAL_ACCOUNT"]
        return os.environ["KIS_PAPER_ACCOUNT"]

    @property
    def base_url(self) -> str:
        if self.mode == KISMode.REAL:
            return "https://openapi.koreainvestment.com:9443"
        return "https://openapivts.koreainvestment.com:29443"

    def app_key_for(self, mode: str) -> str:
        return os.environ["KIS_REAL_APP_KEY"] if mode == KISMode.REAL else os.environ["KIS_PAPER_APP_KEY"]

    def app_secret_for(self, mode: str) -> str:
        return os.environ["KIS_REAL_APP_SECRET"] if mode == KISMode.REAL else os.environ["KIS_PAPER_APP_SECRET"]

    def account_no_for(self, mode: str) -> str:
        return os.environ["KIS_REAL_ACCOUNT"] if mode == KISMode.REAL else os.environ["KIS_PAPER_ACCOUNT"]

    def base_url_for(self, mode: str) -> str:
        if mode == KISMode.REAL:
            return "https://openapi.koreainvestment.com:9443"
        return "https://openapivts.koreainvestment.com:29443"


@dataclass
class OrderResult:
    success: bool
    order_no: str
    ticker: str
    quantity: int
    price: float
    side: str           # BUY / SELL
    timestamp: str
    error_msg: str = ""


class KISApi:
    """
    한국투자증권 REST API 클라이언트.
    모든 주문은 OrderManager를 통해 호출되어야 한다.
    직접 호출 금지.
    """

    def __init__(self, config: Optional[KISConfig] = None, token_cache_path: Optional[Path] = None):
        self._cfg = config or KISConfig(
            mode=os.environ.get("KIS_MODE", KISMode.PAPER)
        )
        # 주문은 KIS_MODE(기본 paper), 데이터는 KIS_DATA_MODE(기본 real)를 사용한다.
        self._data_mode = os.environ.get(
            "KIS_DATA_MODE",
            KISMode.REAL if config is None else self._cfg.mode,
        )
        self._order_admin_mode = os.environ.get(
            "KIS_ORDER_ADMIN_MODE",
            KISMode.REAL if self._cfg.mode == KISMode.PAPER else self._cfg.mode,
        )
        self._access_tokens: dict[str, Optional[str]] = {}
        self._token_expires: dict[str, float] = {}
        self._stock_info_cache: dict[str, dict] = {}
        self._token_cache_path = token_cache_path or (
            Path(__file__).parent.parent / "data" / "cache" / f"kis_token_{self._cfg.mode}.json"
        )

    def _get_token(self, mode: str | None = None) -> str:
        """액세스 토큰 발급 (만료 시 자동 재발급)"""
        mode = mode or self._cfg.mode
        if self._access_tokens.get(mode) and time.time() < self._token_expires.get(mode, 0):
            return self._access_tokens[mode] or ""

        cached = self._load_token_cache(mode)
        if cached:
            return cached

        url = f"{self._base_url_for(mode)}/oauth2/tokenP"
        body = {
            "grant_type": "client_credentials",
            "appkey": self._app_key_for(mode),
            "appsecret": self._app_secret_for(mode),
        }
        resp = requests.post(url, json=body, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        self._access_tokens[mode] = data["access_token"]
        # 토큰 유효기간 - 30분 여유
        self._token_expires[mode] = time.time() + data.get("expires_in", 86400) - 1800
        self._save_token_cache(mode)
        return self._access_tokens[mode] or ""

    def _token_cache_file(self, mode: str) -> Path:
        if mode == self._cfg.mode:
            return self._token_cache_path
        return self._token_cache_path.with_name(f"kis_token_{mode}.json")

    def _load_token_cache(self, mode: str) -> Optional[str]:
        try:
            data = json.loads(self._token_cache_file(mode).read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None

        token = data.get("access_token")
        expires_at = float(data.get("expires_at", 0))
        if token and time.time() < expires_at:
            self._access_tokens[mode] = token
            self._token_expires[mode] = expires_at
            return token
        return None

    def _save_token_cache(self, mode: str) -> None:
        path = self._token_cache_file(mode)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({
                "access_token": self._access_tokens.get(mode),
                "expires_at": self._token_expires.get(mode, 0),
            }),
            encoding="utf-8",
        )

    def _headers(self, tr_id: str, mode: str | None = None) -> dict:
        mode = mode or self._cfg.mode
        return {
            "content-type": "application/json",
            "authorization": f"Bearer {self._get_token(mode)}",
            "appkey": self._app_key_for(mode),
            "appsecret": self._app_secret_for(mode),
            "tr_id": tr_id,
            "custtype": "P",  # 개인 고객 — 일부 API(조건검색 등)에서 문서상 필수 헤더
        }

    def _app_key_for(self, mode: str) -> str:
        if hasattr(self._cfg, "app_key_for"):
            return self._cfg.app_key_for(mode)
        return self._cfg.app_key

    def _app_secret_for(self, mode: str) -> str:
        if hasattr(self._cfg, "app_secret_for"):
            return self._cfg.app_secret_for(mode)
        return self._cfg.app_secret

    def _account_no_for(self, mode: str) -> str:
        if hasattr(self._cfg, "account_no_for"):
            return self._cfg.account_no_for(mode)
        return self._cfg.account_no

    def _base_url_for(self, mode: str) -> str:
        if hasattr(self._cfg, "base_url_for"):
            return self._cfg.base_url_for(mode)
        return self._cfg.base_url

    def get_ohlcv(self, ticker: str, period: int = 60) -> list:
        """일봉 데이터 조회"""
        url = f"{self._base_url_for(self._data_mode)}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
        end_date = datetime.now()
        start_date = end_date - timedelta(days=max(period * 3, 30))
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": ticker,
            "FID_INPUT_DATE_1": start_date.strftime("%Y%m%d"),
            "FID_INPUT_DATE_2": end_date.strftime("%Y%m%d"),
            "FID_PERIOD_DIV_CODE": "D",
            "FID_ORG_ADJ_PRC": "0",
        }
        resp = self._get_with_retry(
            url,
            headers=self._headers("FHKST03010100", mode=self._data_mode),
            params=params,
            timeout=10,
        )
        return resp.json().get("output2", [])[:period]

    def get_snapshot(self, ticker: str) -> dict:
        """현재가 조회.

        현재가 API에는 투자자별 순매수 필드가 안정적으로 포함되지 않으므로
        별도 investor endpoint 결과를 병합해 MarketData의 flow 입력을 채운다.
        """
        url = f"{self._base_url_for(self._data_mode)}/uapi/domestic-stock/v1/quotations/inquire-price"
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker}
        resp = self._get_with_retry(
            url,
            headers=self._headers("FHKST01010100", mode=self._data_mode),
            params=params,
            timeout=10,
        )
        snapshot = resp.json().get("output", {})
        snapshot.update(self.get_stock_info(ticker))
        snapshot.update(self.get_investor_flow(ticker))
        return snapshot

    def get_stock_info(self, ticker: str) -> dict:
        """국내주식 기본정보 조회.

        KIS 문서 기준 `search-stock-info`는 실전만 지원된다. 데이터 모드는
        기본 real이므로 종목명/업종/상장주식수 보강에 사용한다.
        """
        if ticker in self._stock_info_cache:
            return dict(self._stock_info_cache[ticker])
        if self._data_mode != KISMode.REAL:
            return {}

        url = f"{self._base_url_for(self._data_mode)}/uapi/domestic-stock/v1/quotations/search-stock-info"
        params = {
            "PRDT_TYPE_CD": "300",
            "PDNO": ticker,
        }
        try:
            resp = self._get_with_retry(
                url,
                headers=self._headers("CTPF1002R", mode=self._data_mode),
                params=params,
                timeout=10,
            )
            data = resp.json()
            if data.get("rt_cd") != "0":
                return {}
            out = data.get("output", {}) or {}
            info = {
                "name": out.get("prdt_abrv_name") or out.get("prdt_name"),
                "sector": (
                    out.get("idx_bztp_scls_cd_name")
                    or out.get("idx_bztp_mcls_cd_name")
                    or out.get("std_idst_clsf_cd_name")
                ),
                "listed_shares": out.get("lstg_stqt"),
                "market_id": out.get("mket_id_cd"),
                "security_group": out.get("scty_grp_id_cd"),
                "trading_halted": out.get("tr_stop_yn") == "Y",
                "administrative_issue": out.get("admn_item_yn") == "Y",
            }
            self._stock_info_cache[ticker] = {k: v for k, v in info.items() if v not in (None, "")}
            return dict(self._stock_info_cache[ticker])
        except Exception:
            return {}

    def get_index_status(self) -> dict:
        """KOSPI/KOSDAQ 지수 현황 조회.

        실시간 데이터(inquire-index-price)와 일봉 데이터(inquire-daily-indexchartprice)를
        병합한다. 장전(08:00)에는 실시간 상승/하락 종목 수가 0이므로,
        전일 확정 등락률·거래대금을 추가로 제공해 Regime Agent 판단 품질을 높인다.
        """
        kospi = self._get_index_quote("0001")
        kosdaq = self._get_index_quote("1001")

        # 전일 일봉 데이터 (가장 최근 확정 영업일 = output2[0])
        prev_kospi = self._get_prev_index_day("0001")
        prev_kosdaq = self._get_prev_index_day("1001")

        return {
            "kospi": kospi.get("bstp_nmix_prpr") or kospi.get("bstp_nmix") or kospi.get("stck_prpr") or 0,
            "kosdaq": kosdaq.get("bstp_nmix_prpr") or kosdaq.get("bstp_nmix") or kosdaq.get("stck_prpr") or 0,
            "kospi_change_pct": kospi.get("bstp_nmix_prdy_ctrt") or kospi.get("prdy_ctrt") or 0,
            "kosdaq_change_pct": kosdaq.get("bstp_nmix_prdy_ctrt") or kosdaq.get("prdy_ctrt") or 0,
            "kospi_trading_value": self._million_krw(kospi, ["acml_tr_pbmn", "bstp_nmix_acml_tr_pbmn"]),
            "kosdaq_trading_value": self._million_krw(kosdaq, ["acml_tr_pbmn", "bstp_nmix_acml_tr_pbmn"]),
            "kospi_advancers": self._first(kospi, ["ascn_issu_cnt", "rise_issu_cnt", "up_issu_cnt"]),
            "kospi_decliners": self._first(kospi, ["down_issu_cnt", "decl_issu_cnt", "dn_issu_cnt"]),
            "kosdaq_advancers": self._first(kosdaq, ["ascn_issu_cnt", "rise_issu_cnt", "up_issu_cnt"]),
            "kosdaq_decliners": self._first(kosdaq, ["down_issu_cnt", "decl_issu_cnt", "dn_issu_cnt"]),
            # ── 전일 확정 데이터 ──────────────────────────────────────────────
            "prev_kospi_change_pct": self._to_float_safe(
                prev_kospi.get("bstp_nmix_prdy_ctrt") or prev_kospi.get("prdy_ctrt")
            ),
            "prev_kosdaq_change_pct": self._to_float_safe(
                prev_kosdaq.get("bstp_nmix_prdy_ctrt") or prev_kosdaq.get("prdy_ctrt")
            ),
            "prev_kospi_trading_value": self._million_krw(
                prev_kospi, ["acml_tr_pbmn", "bstp_nmix_acml_tr_pbmn"]
            ),
            "prev_kosdaq_trading_value": self._million_krw(
                prev_kosdaq, ["acml_tr_pbmn", "bstp_nmix_acml_tr_pbmn"]
            ),
        }

    def _get_prev_index_day(self, index_code: str) -> dict:
        """지수 일봉 API에서 가장 최근 확정 영업일 데이터 반환.

        장전(08:00)에 호출하면 전날 종가·거래대금이 들어 있어
        당일 실시간 API(inquire-index-price)의 0값 문제를 보완한다.
        실패 시 빈 dict 반환.
        """
        url = (
            f"{self._base_url_for(self._data_mode)}"
            "/uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice"
        )
        end = datetime.now()
        start = end - timedelta(days=10)   # 휴장일 포함 여유
        params = {
            "FID_COND_MRKT_DIV_CODE": "U",
            "FID_INPUT_ISCD": index_code,
            "FID_INPUT_DATE_1": start.strftime("%Y%m%d"),
            "FID_INPUT_DATE_2": end.strftime("%Y%m%d"),
            "FID_PERIOD_DIV_CODE": "D",
        }
        try:
            resp = self._get_with_retry(
                url,
                headers=self._headers("FHKUP03500100", mode=self._data_mode),
                params=params,
                timeout=10,
            )
            rows = resp.json().get("output2", [])
            return self._select_prev_index_day(rows)
        except Exception:
            return {}

    def _select_prev_index_day(self, rows: list[dict]) -> dict:
        """일봉 응답에서 장전 0값 당일 row를 제외한 최근 확정 거래일을 선택."""
        for idx, row in enumerate(rows):
            if self._to_float_safe(row.get("acml_tr_pbmn")) <= 0:
                continue
            selected = dict(row)
            previous_close = self._to_float_safe(
                rows[idx + 1].get("bstp_nmix_prpr")
                if idx + 1 < len(rows)
                else None
            )
            close = self._to_float_safe(selected.get("bstp_nmix_prpr"))
            if close > 0 and previous_close > 0:
                selected["prdy_ctrt"] = round((close - previous_close) / previous_close * 100, 2)
            return selected
        return {}

    def _to_float_safe(self, value) -> float:
        if value in (None, ""):
            return 0.0
        try:
            return float(str(value).replace(",", ""))
        except (ValueError, TypeError):
            return 0.0

    def _first(self, row: dict, keys: list[str]):
        for key in keys:
            value = row.get(key)
            if value not in (None, ""):
                return value
        return 0

    def _million_krw(self, row: dict, keys: list[str]) -> float:
        value = self._first(row, keys)
        if value in (None, ""):
            return 0.0
        return float(str(value).replace(",", "")) * 1_000_000

    def _get_index_quote(self, index_code: str) -> dict:
        url = f"{self._base_url_for(self._data_mode)}/uapi/domestic-stock/v1/quotations/inquire-index-price"
        params = {
            "FID_COND_MRKT_DIV_CODE": "U",
            "FID_INPUT_ISCD": index_code,
        }
        resp = self._get_with_retry(
            url,
            headers=self._headers("FHPUP02100000", mode=self._data_mode),
            params=params,
            timeout=10,
        )
        data = resp.json()
        if data.get("rt_cd") not in (None, "0"):
            raise RuntimeError(f"KIS index quote failed: {data.get('msg1', data)}")
        return data.get("output", {})

    def _get_with_retry(self, url: str, headers: dict, params: dict, timeout: int):
        last_error = None
        for attempt in range(3):
            try:
                resp = requests.get(url, headers=headers, params=params, timeout=timeout)
                resp.raise_for_status()
                return resp
            except requests.HTTPError as exc:
                last_error = exc
                status = exc.response.status_code if exc.response is not None else None
                if status is not None and status < 500:
                    raise
            except requests.RequestException as exc:
                last_error = exc
            if attempt < 2:
                time.sleep(0.5 * (attempt + 1))
        raise last_error

    def raw_get(self, tr_id: str, path: str, params: dict, mode: str | None = None) -> dict:
        """MIL 도구가 사용하는 범용 KIS REST GET 호출.

        Args:
            tr_id: KIS 거래ID (e.g. "FHPUP02140000")
            path: /uapi/ 이후 경로 (e.g. "domestic-stock/v1/quotations/inquire-index-category-price")
            params: 쿼리 파라미터
            mode: "real"|"paper". 미지정 시 self._data_mode 사용
        """
        mode = mode or self._data_mode
        url = f"{self._base_url_for(mode)}/uapi/{path}"
        resp = self._get_with_retry(
            url,
            headers=self._headers(tr_id, mode=mode),
            params=params,
            timeout=10,
        )
        return resp.json()

    def get_universe(self) -> list[str]:
        """운영 스캔 대상 종목 목록.

        우선순위:
        1. KIS_UNIVERSE=005930,000660 직접 지정
        2. KIS_UNIVERSE_FILE=data/universe.csv 파일 지정
        """
        raw = os.environ.get("KIS_UNIVERSE", "")
        tickers = [t.strip() for t in raw.split(",") if t.strip()]
        if not tickers:
            tickers = self._load_universe_file(os.environ.get("KIS_UNIVERSE_FILE", "data/universe.csv"))
        if not tickers:
            raise RuntimeError("KIS_UNIVERSE or KIS_UNIVERSE_FILE must be set for live scanner universe.")
        return tickers

    def _load_universe_file(self, file_path: str) -> list[str]:
        if not file_path:
            return []
        path = Path(file_path)
        if not path.is_absolute():
            path = Path(__file__).parent.parent / path
        if not path.exists():
            raise RuntimeError(f"KIS_UNIVERSE_FILE not found: {path}")
        tickers: list[str] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            code = line.split(",")[0].strip()
            if code and code.lower() not in {"ticker", "code", "종목코드"}:
                tickers.append(code)
        return tickers

    def get_theme_seed_tickers(self, limit: int = 60) -> list[str]:
        """테마/대장주 탐색용 seed 종목.

        수천 종목 전수 조회 대신 KIS 순위 API의 상승률 상위와 거래대금 상위를
        합쳐 오늘 돈이 몰리는 후보군을 만든다.
        """
        rows = (
            self.get_fluctuation_rank(limit=30)
            + self.get_trading_value_rank(limit=30)
        )
        tickers: list[str] = []
        seen: set[str] = set()
        for row in rows:
            ticker = str(
                row.get("ticker")
                or row.get("stck_shrn_iscd")
                or row.get("mksc_shrn_iscd")
                or ""
            ).strip()
            if ticker and ticker not in seen:
                seen.add(ticker)
                tickers.append(ticker)
            if len(tickers) >= limit:
                break
        return tickers

    def get_fluctuation_rank(self, limit: int = 30) -> list[dict]:
        """국내주식 등락률 순위. 실전 데이터 전용."""
        if self._data_mode != KISMode.REAL:
            return []
        url = f"{self._base_url_for(self._data_mode)}/uapi/domestic-stock/v1/ranking/fluctuation"
        params = {
            "fid_cond_mrkt_div_code": "J",
            "fid_cond_scr_div_code": "20170",
            "fid_input_iscd": "0000",
            "fid_rank_sort_cls_code": "0",
            "fid_input_cnt_1": "0",
            "fid_prc_cls_code": "0",
            "fid_input_price_1": "",
            "fid_input_price_2": "",
            "fid_vol_cnt": "",
            "fid_trgt_cls_code": "0",
            "fid_trgt_exls_cls_code": "0",
            "fid_div_cls_code": "0",
            "fid_rsfl_rate1": "",
            "fid_rsfl_rate2": "",
        }
        return self._rank_request(url, "FHPST01700000", params, limit)

    def get_trading_value_rank(self, limit: int = 30) -> list[dict]:
        """국내주식 거래대금 순위. volume-rank API의 거래금액순 정렬."""
        if self._data_mode != KISMode.REAL:
            return []
        url = f"{self._base_url_for(self._data_mode)}/uapi/domestic-stock/v1/quotations/volume-rank"
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_COND_SCR_DIV_CODE": "20171",
            "FID_INPUT_ISCD": "0000",
            "FID_DIV_CLS_CODE": "0",
            "FID_BLNG_CLS_CODE": "3",
            "FID_TRGT_CLS_CODE": "111111111",
            "FID_TRGT_EXLS_CLS_CODE": "1111111111",
            "FID_INPUT_PRICE_1": "",
            "FID_INPUT_PRICE_2": "",
            "FID_VOL_CNT": "",
            "FID_INPUT_DATE_1": "0",
        }
        return self._rank_request(url, "FHPST01710000", params, limit)

    def _rank_request(self, url: str, tr_id: str, params: dict, limit: int) -> list[dict]:
        try:
            resp = self._get_with_retry(
                url,
                headers=self._headers(tr_id, mode=self._data_mode),
                params=params,
                timeout=10,
            )
            data = resp.json()
            if data.get("rt_cd") != "0":
                return []
            output = data.get("output", [])
            rows = output if isinstance(output, list) else [output]
            return rows[:limit]
        except Exception:
            return []

    def buy_market(self, ticker: str, quantity: int) -> OrderResult:
        """시장가 매수"""
        return self._place_order(ticker, quantity, 0, "BUY", market=True)

    def buy_limit(self, ticker: str, quantity: int, price: float) -> OrderResult:
        """지정가 매수"""
        return self._place_order(ticker, quantity, price, "BUY", market=False)

    def sell_market(self, ticker: str, quantity: int) -> OrderResult:
        """시장가 매도"""
        return self._place_order(ticker, quantity, 0, "SELL", market=True)

    def sell_limit(self, ticker: str, quantity: int, price: float) -> OrderResult:
        """지정가 매도"""
        return self._place_order(ticker, quantity, price, "SELL", market=False)

    def sell_after_hours_close(self, ticker: str, quantity: int) -> OrderResult:
        """장후 시간외 매도 (ORD_DVSN 06) — 15:40~16:00 접수, 당일 종가로 체결.

        정규장 마감(15:30) 후 close phase의 청산 주문에 사용한다.
        """
        return self._place_order(ticker, quantity, 0, "SELL", market=False, order_type="06")

    def get_open_orders(self, side: str | None = None) -> list[dict]:
        """정정/취소 가능 미체결 주문 조회.

        문서상 일부 모의투자 환경에서 지원이 제한될 수 있으므로 실패 시
        빈 리스트를 반환한다. 실제 주문 상태의 source of truth는 KIS다.
        """
        order_mode = self._order_admin_mode
        url = f"{self._base_url_for(order_mode)}/uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl"
        acct_parts = self._account_no_for(order_mode).split("-")
        side_code = {"SELL": "1", "BUY": "2"}.get((side or "").upper(), "0")
        params = {
            "CANO": acct_parts[0],
            "ACNT_PRDT_CD": acct_parts[1],
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
            "INQR_DVSN_1": "0",
            "INQR_DVSN_2": side_code,
        }
        tr_id = "TTTC0084R" if order_mode == KISMode.REAL else "VTTC0084R"
        try:
            resp = self._get_with_retry(
                url,
                headers=self._headers(tr_id, mode=order_mode),
                params=params,
                timeout=10,
            )
            data = resp.json()
            if data.get("rt_cd") != "0":
                logger.warning("KIS open order query failed: %s", data.get("msg1", ""))
                return []
            output = data.get("output", [])
            rows = output if isinstance(output, list) else [output]
            return [self._coerce_open_order(row) for row in rows if row]
        except Exception as exc:
            logger.warning("KIS open order query failed: %s", exc)
            return []

    def cancel_order(
        self,
        order_no: str,
        quantity: int = 0,
        org_no: str = "",
        price: float = 0,
        all_quantity: bool = True,
        order_type: str = "00",
    ) -> OrderResult:
        """미체결 주문 취소."""
        order_mode = self._order_admin_mode
        url = f"{self._base_url_for(order_mode)}/uapi/domestic-stock/v1/trading/order-rvsecncl"
        acct_parts = self._account_no_for(order_mode).split("-")
        body = {
            "CANO": acct_parts[0],
            "ACNT_PRDT_CD": acct_parts[1],
            "KRX_FWDG_ORD_ORGNO": org_no,
            "ORGN_ODNO": order_no,
            "ORD_DVSN": order_type,
            "RVSE_CNCL_DVSN_CD": "02",
            "ORD_QTY": "0" if all_quantity else str(int(quantity)),
            "ORD_UNPR": str(int(price)),
            "QTY_ALL_ORD_YN": "Y" if all_quantity else "N",
        }
        tr_id = "TTTC0013U" if order_mode == KISMode.REAL else "VTTC0013U"
        try:
            resp = requests.post(
                url,
                headers=self._headers(tr_id, mode=order_mode),
                json=body,
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            success = data.get("rt_cd") == "0"
            output = data.get("output", {}) or {}
            return OrderResult(
                success=success,
                order_no=output.get("ODNO") or output.get("odno") or order_no,
                ticker="",
                quantity=quantity,
                price=price,
                side="CANCEL",
                timestamp=datetime.now().isoformat(),
                error_msg="" if success else data.get("msg1", ""),
            )
        except Exception as exc:
            return OrderResult(
                success=False,
                order_no=order_no,
                ticker="",
                quantity=quantity,
                price=price,
                side="CANCEL",
                timestamp=datetime.now().isoformat(),
                error_msg=str(exc),
            )

    def _place_order(
        self, ticker: str, quantity: int, price: float, side: str, market: bool,
        order_type: str | None = None,
    ) -> OrderResult:
        order_mode = self._cfg.mode
        url = f"{self._base_url_for(order_mode)}/uapi/domestic-stock/v1/trading/order-cash"
        # 실전/모의 tr_id 분기
        if side == "BUY":
            tr_id = "TTTC0802U" if order_mode == KISMode.REAL else "VTTC0802U"
        else:
            tr_id = "TTTC0801U" if order_mode == KISMode.REAL else "VTTC0801U"

        # order_type 명시 시 우선 (06=장후 시간외 등). 시장가/장후 시간외는 가격 0.
        ord_dvsn = order_type or ("01" if market else "00")
        no_price = market or ord_dvsn in ("01", "05", "06")
        acct_parts = self._account_no_for(order_mode).split("-")
        body = {
            "CANO": acct_parts[0],
            "ACNT_PRDT_CD": acct_parts[1],
            "PDNO": ticker,
            "ORD_DVSN": ord_dvsn,
            "ORD_QTY": str(quantity),
            "ORD_UNPR": "0" if no_price else str(int(price)),
        }
        try:
            resp = requests.post(url, headers=self._headers(tr_id, mode=order_mode), json=body, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            success = data.get("rt_cd") == "0"
            return OrderResult(
                success=success,
                order_no=data.get("output", {}).get("ODNO", ""),
                ticker=ticker,
                quantity=quantity,
                price=price,
                side=side,
                timestamp=datetime.now().isoformat(),
                error_msg="" if success else data.get("msg1", ""),
            )
        except Exception as e:
            return OrderResult(
                success=False,
                order_no="",
                ticker=ticker,
                quantity=quantity,
                price=price,
                side=side,
                timestamp=datetime.now().isoformat(),
                error_msg=str(e),
            )

    def get_investor_flow(self, ticker: str) -> dict:
        """종목별 당일 투자자 순매수 조회 (외국인/기관/개인).

        반환: {"foreign_net": float, "institution_net": float, "individual_net": float, "program_net": float}
        데이터 없거나 오류 시 0으로 채워 반환 (호출자가 빈 체크 불필요).
        """
        url = f"{self._base_url_for(self._data_mode)}/uapi/domestic-stock/v1/quotations/inquire-investor"
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": ticker,
        }
        try:
            resp = self._get_with_retry(
                url,
                headers=self._headers("FHKST01010900", mode=self._data_mode),
                params=params,
                timeout=5,
            )
            output = resp.json().get("output", {})
            rows = output if isinstance(output, list) else [output]
            rows = [row for row in rows if row]
            if not rows:
                return {
                    "foreign_net": 0.0,
                    "institution_net": 0.0,
                    "individual_net": 0.0,
                    "program_net": 0.0,
                }
            rows.sort(key=lambda row: str(row.get("stck_bsop_date") or row.get("date") or ""))
            flow = self._coerce_flow_row(ticker, rows[-1])
            self._sanitize_flow_record(flow)
            return {
                "foreign_net": flow["foreign_net"],
                "institution_net": flow["institution_net"],
                "individual_net": flow["individual_net"],
                "program_net": flow["program_net"],
            }
        except Exception:
            return {
                "foreign_net": 0.0,
                "institution_net": 0.0,
                "individual_net": 0.0,
                "program_net": 0.0,
            }

    def get_investor_flow_history(self, ticker: str, days: int = 3) -> list[dict]:
        """최근 N거래일 투자자 순매수 히스토리 조회.

        KIS 응답 형태가 계정/엔드포인트별로 다를 수 있어 output이 list이면
        그대로 파싱하고, 단일 dict이면 당일 1건으로 반환한다.
        """
        url = f"{self._base_url_for(self._data_mode)}/uapi/domestic-stock/v1/quotations/inquire-investor"
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": ticker,
        }
        try:
            resp = self._get_with_retry(
                url,
                headers=self._headers("FHKST01010900", mode=self._data_mode),
                params=params,
                timeout=5,
            )
            output = resp.json().get("output", [])
            rows = output if isinstance(output, list) else [output]
            records = [self._coerce_flow_row(ticker, row) for row in rows if row]
            records.sort(key=lambda r: r["date"])
            records = records[-days:]
            program_by_date = {
                r["date"]: r
                for r in self.get_program_trade_history(ticker, days=days)
            }
            for record in records:
                program = program_by_date.get(record["date"])
                if program:
                    record["program_net"] = program["program_net"]
                    if not record["trading_value"]:
                        record["trading_value"] = program["trading_value"]
                self._sanitize_flow_record(record)
            return records
        except Exception:
            return []

    def get_program_trade_history(self, ticker: str, days: int = 3) -> list[dict]:
        """최근 N거래일 종목별 프로그램 순매수 금액 조회.

        KIS 문서 기준 `/program-trade-by-stock-daily`는 실전만 지원된다.
        모의투자에서는 빈 리스트를 반환해 호출자가 명시적으로 0 처리한다.
        """
        if self._data_mode != KISMode.REAL:
            return []

        url = f"{self._base_url_for(self._data_mode)}/uapi/domestic-stock/v1/quotations/program-trade-by-stock-daily"
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": ticker,
            "FID_INPUT_DATE_1": datetime.now().strftime("%Y%m%d"),
        }
        try:
            resp = self._get_with_retry(
                url,
                headers=self._headers("FHPPG04650201", mode=self._data_mode),
                params=params,
                timeout=5,
            )
            output = resp.json().get("output", [])
            rows = output if isinstance(output, list) else [output]
            records = [self._coerce_program_row(ticker, row) for row in rows if row]
            records.sort(key=lambda r: r["date"])
            return records[-days:]
        except Exception:
            return []

    def _coerce_flow_row(self, ticker: str, row: dict) -> dict:
        def _f(*keys: str) -> float:
            for key in keys:
                value = row.get(key)
                if value not in (None, ""):
                    return float(str(value).replace(",", ""))
            return 0.0

        def _million_krw(*keys: str, fallback_keys: tuple[str, ...] = ()) -> float:
            for key in keys:
                value = row.get(key)
                if value not in (None, ""):
                    return float(str(value).replace(",", "")) * 1_000_000
            return _f(*fallback_keys)

        trading_value = _f("trading_value", "acml_tr_pbmn")
        if not trading_value:
            trading_value = (
                _million_krw("prsn_shnu_tr_pbmn")
                + _million_krw("frgn_shnu_tr_pbmn")
                + _million_krw("orgn_shnu_tr_pbmn")
            )

        return {
            "date": str(row.get("date") or row.get("stck_bsop_date") or datetime.now().strftime("%Y%m%d")),
            "ticker": str(row.get("ticker") or row.get("mksc_shrn_iscd") or ticker),
            "foreign_net": _million_krw("frgn_ntby_tr_pbmn", fallback_keys=("foreign_net", "frgn_ntby_qty")),
            "institution_net": _million_krw("orgn_ntby_tr_pbmn", fallback_keys=("institution_net", "orgn_ntby_qty")),
            "individual_net": _million_krw("prsn_ntby_tr_pbmn", fallback_keys=("individual_net", "prsn_ntby_qty", "indv_ntby_qty")),
            "program_net": _f("program_net"),
            "trading_value": trading_value,
        }

    def _coerce_open_order(self, row: dict) -> dict:
        def _s(*keys: str) -> str:
            for key in keys:
                value = row.get(key)
                if value not in (None, ""):
                    return str(value)
            return ""

        def _i(*keys: str) -> int:
            value = _s(*keys)
            return int(float(value.replace(",", ""))) if value else 0

        def _f(*keys: str) -> float:
            value = _s(*keys)
            return float(value.replace(",", "")) if value else 0.0

        return {
            "order_no": _s("odno", "ODNO"),
            "org_no": _s("ord_gno_brno", "KRX_FWDG_ORD_ORGNO"),
            "ticker": _s("pdno", "PDNO"),
            "name": _s("prdt_name", "PRDT_NAME"),
            "side": {"01": "SELL", "02": "BUY"}.get(_s("sll_buy_dvsn_cd", "SLL_BUY_DVSN_CD"), ""),
            "quantity": _i("ord_qty", "ORD_QTY"),
            "filled_quantity": _i("tot_ccld_qty", "TOT_CCLD_QTY"),
            "cancelable_quantity": _i("psbl_qty", "PSBL_QTY"),
            "price": _f("ord_unpr", "ORD_UNPR"),
            "order_type": _s("ord_dvsn_cd", "ORD_DVSN_CD"),
        }

    def _sanitize_flow_record(self, record: dict) -> None:
        """거래대금 대비 수급액 단위가 100만 배 튄 경우 보정한다."""
        trading_value = float(record.get("trading_value") or 0)
        if trading_value <= 0:
            return

        for key in ("foreign_net", "institution_net", "individual_net", "program_net"):
            value = float(record.get(key) or 0)
            if not value:
                continue
            if abs(value) <= trading_value * 3:
                continue
            corrected = value / 1_000_000
            if abs(corrected) <= trading_value * 3:
                logger.warning(
                    "KIS flow unit adjusted: ticker=%s date=%s key=%s raw=%s corrected=%s",
                    record.get("ticker"),
                    record.get("date"),
                    key,
                    value,
                    corrected,
                )
                record[key] = corrected
            else:
                logger.warning(
                    "KIS flow amount looks abnormal: ticker=%s date=%s key=%s value=%s trading_value=%s",
                    record.get("ticker"),
                    record.get("date"),
                    key,
                    value,
                    trading_value,
                )

    def _coerce_program_row(self, ticker: str, row: dict) -> dict:
        def _f(*keys: str) -> float:
            for key in keys:
                value = row.get(key)
                if value not in (None, ""):
                    return float(str(value).replace(",", ""))
            return 0.0

        return {
            "date": str(row.get("date") or row.get("stck_bsop_date") or datetime.now().strftime("%Y%m%d")),
            "ticker": str(row.get("ticker") or row.get("mksc_shrn_iscd") or ticker),
            "program_net": _f("program_net", "whol_smtn_ntby_tr_pbmn"),
            "trading_value": _f("trading_value", "acml_tr_pbmn"),
        }

    def get_news(self, ticker: str = "000000", limit: int = 20) -> list[dict]:
        """종목별/시장 전반 뉴스 조회 (ticker='000000'이면 전체 시장)"""
        url = f"{self._base_url_for(self._data_mode)}/uapi/domestic-stock/v1/quotations/news-title"
        params = {
            "FID_NEWS_OFER_ENTP_CODE": "",
            "FID_COND_MRKT_CLS_CODE": "",
            "FID_INPUT_ISCD": ticker,
            "FID_TITL_CNTT": "",
            "FID_INPUT_DATE_1": datetime.now().strftime("%Y%m%d"),  # 필수 — 없으면 0건
            "FID_INPUT_HOUR_1": "",
            "FID_RANK_SORT_CLS_CODE": "",
            "FID_INPUT_SRNO": "",
        }
        try:
            resp = requests.get(
                url,
                headers=self._headers("FHKST01011800", mode=self._data_mode),
                params=params,
                timeout=5,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("rt_cd") != "0":
                return []
            return data.get("output", [])[:limit]
        except Exception:
            return []

    def get_buyable_cash(self, ticker: str = "", price: float = 0) -> Optional[dict]:
        """매수가능조회 (TTTC8908R/VTTC8908R).

        주문 모드(`_cfg.mode`)와 동일한 계좌를 조회한다 (주문이 실제로
        체결될 계좌의 가용 현금을 확인해야 의미가 있다).

        조회 실패 시 None을 반환한다 (raise하지 않음). 호출자는 None을
        "확인 불가"로 간주하고 가드를 건너뛰며 경고를 남긴다 — 이 가드는
        보조 안전망이고 주문 자체는 KIS가 최종 거부한다 (fail-open).
        """
        order_mode = self._cfg.mode
        url = f"{self._base_url_for(order_mode)}/uapi/domestic-stock/v1/trading/inquire-psbl-order"
        tr_id = "TTTC8908R" if order_mode == KISMode.REAL else "VTTC8908R"
        acct_parts = self._account_no_for(order_mode).split("-")
        params = {
            "CANO": acct_parts[0],
            "ACNT_PRDT_CD": acct_parts[1],
            "PDNO": ticker,
            "ORD_UNPR": str(int(price)) if price else "",
            "ORD_DVSN": "01",
            "CMA_EVLU_AMT_ICLD_YN": "N",
            "OVRS_ICLD_YN": "N",
        }
        try:
            resp = requests.get(url, headers=self._headers(tr_id, mode=order_mode), params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if data.get("rt_cd") != "0":
                logger.warning("KIS buyable cash query failed: %s", data.get("msg1", ""))
                return None
            output = data.get("output", {}) or {}
            return {
                "buyable_cash_krw": self._to_float_safe(output.get("ord_psbl_cash")),
                "max_buy_qty": int(self._to_float_safe(output.get("max_buy_qty"))),
            }
        except Exception as exc:
            logger.warning("KIS buyable cash query failed: %s", exc)
            return None

    def get_daily_minute_candles(self, ticker: str, date: str, time: str = "130000") -> list[dict]:
        """주식일별분봉조회 (FHKST03010230) - 회고/백테스트용 유틸.

        지정 일자(date, YYYYMMDD)의 특정 시각(time, HHMMSS) 기준
        직전 분봉들을 반환한다.
        """
        url = f"{self._base_url_for(self._data_mode)}/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice"
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": ticker,
            "FID_INPUT_DATE_1": date,
            "FID_INPUT_HOUR_1": time,
            "FID_PW_DATA_INCU_YN": "Y",
            "FID_FAKE_TICK_INCU_YN": "",
        }
        resp = self._get_with_retry(
            url,
            headers=self._headers("FHKST03010230", mode=self._data_mode),
            params=params,
            timeout=10,
        )
        rows = resp.json().get("output2", [])
        return [
            {
                "date": row.get("stck_bsop_date"),
                "time": row.get("stck_cntg_hour"),
                "open": self._to_float_safe(row.get("stck_oprc")),
                "high": self._to_float_safe(row.get("stck_hgpr")),
                "low": self._to_float_safe(row.get("stck_lwpr")),
                "close": self._to_float_safe(row.get("stck_prpr")),
                "volume": self._to_float_safe(row.get("cntg_vol")),
            }
            for row in rows
        ]

    def get_balance(self) -> dict:
        """잔고 조회"""
        # 잔고/손익은 주문 계좌 기준으로 조회해야 한다.
        # 시세는 real, 주문은 paper인 혼합 운영에서 _data_mode를 쓰면
        # 실전 계좌 잔고 API를 잘못 호출하게 된다.
        account_mode = self._cfg.mode
        url = f"{self._base_url_for(account_mode)}/uapi/domestic-stock/v1/trading/inquire-balance"
        tr_id = "TTTC8434R" if account_mode == KISMode.REAL else "VTTC8434R"
        acct_parts = self._account_no_for(account_mode).split("-")
        params = {
            "CANO": acct_parts[0],
            "ACNT_PRDT_CD": acct_parts[1],
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "01",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        resp = requests.get(url, headers=self._headers(tr_id, mode=account_mode), params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()
