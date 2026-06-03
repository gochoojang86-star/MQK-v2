"""
KIS API Broker - 한국투자증권 API 연동
실전(production) / 모의(paper) 모드 자동 전환
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime
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

    def __init__(self, config: Optional[KISConfig] = None):
        self._cfg = config or KISConfig(
            mode=os.environ.get("KIS_MODE", KISMode.PAPER)
        )
        self._access_token: Optional[str] = None
        self._token_expires: float = 0

    def _get_token(self) -> str:
        """액세스 토큰 발급 (만료 시 자동 재발급)"""
        if self._access_token and time.time() < self._token_expires:
            return self._access_token

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
        return self._access_token

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
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": ticker,
            "FID_INPUT_DATE_1": "",
            "FID_INPUT_DATE_2": datetime.now().strftime("%Y%m%d"),
            "FID_PERIOD_DIV_CODE": "D",
            "FID_ORG_ADJ_PRC": "0",
        }
        resp = requests.get(
            url,
            headers=self._headers("FHKST03010100"),
            params=params,
            timeout=10
        )
        resp.raise_for_status()
        return resp.json().get("output2", [])

    def get_snapshot(self, ticker: str) -> dict:
        """현재가 조회"""
        url = f"{self._cfg.base_url}/uapi/domestic-stock/v1/quotations/inquire-price"
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker}
        resp = requests.get(
            url,
            headers=self._headers("FHKST01010100"),
            params=params,
            timeout=10
        )
        resp.raise_for_status()
        return resp.json().get("output", {})

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

    def get_news(self, ticker: str = "000000", limit: int = 20) -> list[dict]:
        """종목별/시장 전반 뉴스 조회 (ticker='000000'이면 전체 시장)"""
        url = f"{self._cfg.base_url}/uapi/domestic-stock/v1/quotations/news-title"
        params = {
            "FID_NEWS_OFER_ENTP_CODE": "",
            "FID_COND_MRKT_CLS_CODE": "",
            "FID_INPUT_ISCD": ticker,
            "FID_TITL_CNTT": "",
            "FID_INPUT_DATE_1": "",
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
