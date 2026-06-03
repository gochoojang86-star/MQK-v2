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
    """KIS API 뉴스 조회 — 종목별 / 시장 전반 (ticker="000000")"""

    _PATH = "/uapi/domestic-stock/v1/quotations/news-title"

    def __init__(self, kis_api=None) -> None:
        self._kis = kis_api  # broker.kis_api.KISApi 인스턴스

    def get_news(self, ticker: str = "000000", limit: int = 20) -> list[NewsItem]:
        if self._kis is None:
            return []
        try:
            token = self._kis._get_token()
            url = f"{self._kis._cfg.base_url}{self._PATH}"
            headers = {
                "content-type": "application/json",
                "authorization": f"Bearer {token}",
                "appkey": self._kis._cfg.app_key,
                "appsecret": self._kis._cfg.app_secret,
                "tr_id": "FHKST01011800",
            }
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
            resp = requests.get(url, headers=headers, params=params, timeout=5)
            resp.raise_for_status()
            data = resp.json()
            if data.get("rt_cd") != "0":
                return []
            articles = data.get("output", [])[:limit]
            return [
                NewsItem(
                    title=a.get("HDON_TITL_CNTT", ""),
                    description="",
                    url="",
                    pub_date=f"{a.get('DATA_DT', '')} {a.get('DATA_TM', '')}".strip(),
                    source="kis",
                    ticker=ticker if ticker != "000000" else "",
                )
                for a in articles
                if a.get("HDON_TITL_CNTT")
            ]
        except Exception:
            return []
