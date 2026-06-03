"""
News Fetcher Code - 뉴스 수집 (LLM 미사용)
두 소스:
  1. Naver News API  - 키워드/테마 검색 (NAVER_CLIENT_ID, NAVER_CLIENT_SECRET)
  2. KIS News API    - 종목별/시장 전반 공식 뉴스 (KIS 토큰 공유)
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

import requests
from dotenv import load_dotenv

load_dotenv()


@dataclass
class NewsItem:
    title: str
    description: str
    url: str
    pub_date: str
    source: str       # "naver" | "kis" | "telegram"
    ticker: str = ""

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "content": self.description,
            "date": self.pub_date,
            "source": self.source,
            "url": self.url,
        }


class NaverNewsFetcher:
    """네이버 뉴스 검색 API — 키워드/테마 검색"""

    _ENDPOINT = "https://openapi.naver.com/v1/search/news.json"

    def __init__(self) -> None:
        self._client_id = os.environ.get("NAVER_CLIENT_ID", "")
        self._client_secret = os.environ.get("NAVER_CLIENT_SECRET", "")

    @property
    def available(self) -> bool:
        return bool(self._client_id and self._client_secret)

    def search(self, query: str, display: int = 5) -> list[NewsItem]:
        if not self.available:
            return []
        try:
            resp = requests.get(
                self._ENDPOINT,
                headers={
                    "X-Naver-Client-Id": self._client_id,
                    "X-Naver-Client-Secret": self._client_secret,
                },
                params={"query": query, "display": display, "sort": "date"},
                timeout=5,
            )
            resp.raise_for_status()
            return [
                NewsItem(
                    title=re.sub(r"<[^>]+>", "", item.get("title", "")),
                    description=re.sub(r"<[^>]+>", "", item.get("description", "")),
                    url=item.get("originallink") or item.get("link", ""),
                    pub_date=item.get("pubDate", ""),
                    source="naver",
                )
                for item in resp.json().get("items", [])
            ]
        except Exception:
            return []


class KISNewsFetcher:
    """KIS API 뉴스 조회 — 종목별 / 시장 전반 (ticker="000000")

    - 뉴스 데이터는 모의(paper) API에 없으므로 항상 실전(real) 엔드포인트 사용
    - 기존 kis_api 토큰을 재사용하면 토큰 발급 속도제한(1분/1회) 회피 가능
    - FID_INPUT_DATE_1=오늘날짜 필수 — 없으면 output 0건
    """

    _PATH = "/uapi/domestic-stock/v1/quotations/news-title"
    _REAL_URL = "https://openapi.koreainvestment.com:9443"

    def __init__(self, kis_api=None) -> None:
        self._kis = kis_api          # 토큰 재사용용 (KISApi 인스턴스)
        self._real_app_key = os.environ.get("KIS_REAL_APP_KEY", "")
        self._real_app_secret = os.environ.get("KIS_REAL_APP_SECRET", "")

    def _get_token(self) -> str:
        """기존 kis_api 토큰 재사용 → 없으면 실전 자격증명으로 신규 발급."""
        if self._kis is not None:
            return self._kis._get_token()
        if not self._real_app_key or not self._real_app_secret:
            raise RuntimeError("KIS_REAL_APP_KEY / KIS_REAL_APP_SECRET 미설정")
        resp = requests.post(
            f"{self._REAL_URL}/oauth2/tokenP",
            json={
                "grant_type": "client_credentials",
                "appkey": self._real_app_key,
                "appsecret": self._real_app_secret,
            },
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()["access_token"]

    def get_news(self, ticker: str = "000000", limit: int = 20) -> list[NewsItem]:
        if not self._real_app_key:
            return []
        try:
            from datetime import datetime as _dt
            token = self._get_token()
            headers = {
                "content-type": "application/json",
                "authorization": f"Bearer {token}",
                "appkey": self._real_app_key,
                "appsecret": self._real_app_secret,
                "tr_id": "FHKST01011800",
            }
            params = {
                "FID_NEWS_OFER_ENTP_CODE": "",
                "FID_COND_MRKT_CLS_CODE": "",
                "FID_INPUT_ISCD": ticker,
                "FID_TITL_CNTT": "",
                "FID_INPUT_DATE_1": _dt.now().strftime("%Y%m%d"),  # 필수
                "FID_INPUT_HOUR_1": "",
                "FID_RANK_SORT_CLS_CODE": "",
                "FID_INPUT_SRNO": "",
            }
            resp = requests.get(
                f"{self._REAL_URL}{self._PATH}",
                headers=headers,
                params=params,
                timeout=5,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("rt_cd") != "0":
                return []
            articles = data.get("output", [])[:limit]
            return [
                NewsItem(
                    title=a.get("hts_pbnt_titl_cntt", ""),
                    description=a.get("dorg", ""),   # 출처 언론사
                    url="",
                    pub_date=f"{a.get('data_dt', '')} {a.get('data_tm', '')}".strip(),
                    source="kis",
                    ticker=a.get("iscd1") or (ticker if ticker != "000000" else ""),
                )
                for a in articles
                if a.get("hts_pbnt_titl_cntt")
            ]
        except Exception:
            return []
