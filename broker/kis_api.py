"""
KIS API Broker - 한국투자증권 API 연동
실전(production) / 모의(paper) 모드 자동 전환
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import requests
from dotenv import load_dotenv

load_dotenv()


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
        self._access_token: Optional[str] = None
        self._token_expires: float = 0
        self._token_cache_path = token_cache_path or (
            Path(__file__).parent.parent / "data" / "cache" / f"kis_token_{self._cfg.mode}.json"
        )

    def _get_token(self) -> str:
        """액세스 토큰 발급 (만료 시 자동 재발급)"""
        if self._access_token and time.time() < self._token_expires:
            return self._access_token

        cached = self._load_token_cache()
        if cached:
            return cached

        url = f"{self._cfg.base_url}/oauth2/tokenP"
        body = {
            "grant_type": "client_credentials",
            "appkey": self._cfg.app_key,
            "appsecret": self._cfg.app_secret,
        }
        resp = requests.post(url, json=body, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        self._access_token = data["access_token"]
        # 토큰 유효기간 - 30분 여유
        self._token_expires = time.time() + data.get("expires_in", 86400) - 1800
        self._save_token_cache()
        return self._access_token

    def _load_token_cache(self) -> Optional[str]:
        try:
            data = json.loads(self._token_cache_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None

        token = data.get("access_token")
        expires_at = float(data.get("expires_at", 0))
        if token and time.time() < expires_at:
            self._access_token = token
            self._token_expires = expires_at
            return token
        return None

    def _save_token_cache(self) -> None:
        self._token_cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._token_cache_path.write_text(
            json.dumps({
                "access_token": self._access_token,
                "expires_at": self._token_expires,
            }),
            encoding="utf-8",
        )

    def _headers(self, tr_id: str) -> dict:
        return {
            "content-type": "application/json",
            "authorization": f"Bearer {self._get_token()}",
            "appkey": self._cfg.app_key,
            "appsecret": self._cfg.app_secret,
            "tr_id": tr_id,
        }

    def get_ohlcv(self, ticker: str, period: int = 60) -> list:
        """일봉 데이터 조회"""
        url = f"{self._cfg.base_url}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
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
            headers=self._headers("FHKST03010100"),
            params=params,
            timeout=10,
        )
        return resp.json().get("output2", [])[:period]

    def get_snapshot(self, ticker: str) -> dict:
        """현재가 조회"""
        url = f"{self._cfg.base_url}/uapi/domestic-stock/v1/quotations/inquire-price"
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker}
        resp = self._get_with_retry(
            url,
            headers=self._headers("FHKST01010100"),
            params=params,
            timeout=10,
        )
        return resp.json().get("output", {})

    def get_index_status(self) -> dict:
        """KOSPI/KOSDAQ 지수 현황 조회."""
        kospi = self._get_index_quote("0001")
        kosdaq = self._get_index_quote("1001")
        return {
            "kospi": kospi.get("bstp_nmix_prpr") or kospi.get("bstp_nmix") or kospi.get("stck_prpr") or 0,
            "kosdaq": kosdaq.get("bstp_nmix_prpr") or kosdaq.get("bstp_nmix") or kosdaq.get("stck_prpr") or 0,
            "kospi_change_pct": kospi.get("bstp_nmix_prdy_ctrt") or kospi.get("prdy_ctrt") or 0,
            "kosdaq_change_pct": kosdaq.get("bstp_nmix_prdy_ctrt") or kosdaq.get("prdy_ctrt") or 0,
        }

    def _get_index_quote(self, index_code: str) -> dict:
        url = f"{self._cfg.base_url}/uapi/domestic-stock/v1/quotations/inquire-index-price"
        params = {
            "FID_COND_MRKT_DIV_CODE": "U",
            "FID_INPUT_ISCD": index_code,
        }
        resp = self._get_with_retry(
            url,
            headers=self._headers("FHPUP02100000"),
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

    def get_universe(self) -> list[str]:
        """운영 스캔 대상 종목 목록.

        KIS 전체 종목 목록 adapter가 구현되기 전까지 운영 universe는
        KIS_UNIVERSE=005930,000660 처럼 명시 설정한다.
        """
        raw = os.environ.get("KIS_UNIVERSE", "")
        tickers = [t.strip() for t in raw.split(",") if t.strip()]
        if not tickers:
            raise RuntimeError("KIS_UNIVERSE must be set for live scanner universe.")
        return tickers

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

    def _place_order(
        self, ticker: str, quantity: int, price: float, side: str, market: bool
    ) -> OrderResult:
        url = f"{self._cfg.base_url}/uapi/domestic-stock/v1/trading/order-cash"
        # 실전/모의 tr_id 분기
        if side == "BUY":
            tr_id = "TTTC0802U" if self._cfg.mode == KISMode.REAL else "VTTC0802U"
        else:
            tr_id = "TTTC0801U" if self._cfg.mode == KISMode.REAL else "VTTC0801U"

        acct_parts = self._cfg.account_no.split("-")
        body = {
            "CANO": acct_parts[0],
            "ACNT_PRDT_CD": acct_parts[1],
            "PDNO": ticker,
            "ORD_DVSN": "01" if market else "00",   # 01=시장가, 00=지정가
            "ORD_QTY": str(quantity),
            "ORD_UNPR": "0" if market else str(int(price)),
        }
        try:
            resp = requests.post(url, headers=self._headers(tr_id), json=body, timeout=10)
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

        반환: {"foreign_net": float, "institution_net": float, "individual_net": float}
        데이터 없거나 오류 시 0으로 채워 반환 (호출자가 빈 체크 불필요).
        """
        url = f"{self._cfg.base_url}/uapi/domestic-stock/v1/quotations/inquire-investor"
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": ticker,
        }
        try:
            resp = self._get_with_retry(
                url,
                headers=self._headers("FHKST01010900"),
                params=params,
                timeout=5,
            )
            out = resp.json().get("output", {})
            def _f(key: str) -> float:
                v = out.get(key, "0") or "0"
                return float(str(v).replace(",", ""))
            return {
                "foreign_net":      _f("frgn_ntby_qty"),
                "institution_net":  _f("orgn_ntby_qty"),
                "individual_net":   _f("indv_ntby_qty"),
            }
        except Exception:
            return {"foreign_net": 0.0, "institution_net": 0.0, "individual_net": 0.0}

    def get_news(self, ticker: str = "000000", limit: int = 20) -> list[dict]:
        """종목별/시장 전반 뉴스 조회 (ticker='000000'이면 전체 시장)"""
        url = f"{self._cfg.base_url}/uapi/domestic-stock/v1/quotations/news-title"
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
                headers=self._headers("FHKST01011800"),
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

    def get_balance(self) -> dict:
        """잔고 조회"""
        url = f"{self._cfg.base_url}/uapi/domestic-stock/v1/trading/inquire-balance"
        tr_id = "TTTC8434R" if self._cfg.mode == KISMode.REAL else "VTTC8434R"
        acct_parts = self._cfg.account_no.split("-")
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
        resp = requests.get(url, headers=self._headers(tr_id), params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()
