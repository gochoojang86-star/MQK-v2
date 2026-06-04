"""
Disclosure Fetcher Code - 오픈다트 공시 수집 (LLM 미사용)

DART 공시 유형:
  B = 주요사항보고 — CB/BW/유증/수주/공급계약/자기주식 등 트레이딩 핵심
  D = 지분공시     — 대량보유/임원소유 변동 (수급 단서)

corp_code 조회 결과는 파일 캐시에 저장해 API 호출 최소화.
"""
from __future__ import annotations

import io
import json
import os
import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

# 핵심 공시 키워드 — 주변 컨텍스트를 추출할 기준
_KEY_PATTERNS = [
    r"발행가액",      # 주당 가격
    r"발행금액",      # 총 발행 금액
    r"납입금액",      # 실제 납입액
    r"증자규모",
    r"발행주식수",
    r"신주\s*수",
    r"시설자금",      # 자금 목적
    r"운영자금",
    r"채무상환",
    r"타법인\s*취득",
    r"제3자\s*배정",  # 배정 방식
    r"주주\s*배정",
    r"일반\s*공모",
    r"자기주식",
    r"전환가액",      # CB/BW 관련
    r"행사가액",
    r"만기일",
    r"이자율",
]
_KEY_RE = re.compile("|".join(_KEY_PATTERNS), re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")

load_dotenv()

_BASE = "https://opendart.fss.or.kr/api"
_CACHE_PATH = Path(__file__).parent.parent / "data" / "cache" / "dart_corp_codes.json"


@dataclass
class DisclosureItem:
    ticker: str
    corp_name: str
    title: str
    date: str           # YYYYMMDD
    rcept_no: str
    pblntf_ty: str      # B=주요사항, D=지분공시
    content: str = ""

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "content": self.content,
            "date": self.date,
            "source": "dart",
            "rcept_no": self.rcept_no,
            "type": self.pblntf_ty,
        }


class DARTFetcher:
    """오픈다트 공시 수집기"""

    def __init__(self) -> None:
        self._api_key = os.environ.get("DART_AUTH_KEY", "")
        self._corp_codes: dict[str, str] = {}   # ticker → corp_code (in-memory)
        self._document_cache: dict[str, str] = {}
        self._load_corp_code_cache()

    # ── corp_code 조회 ────────────────────────────────────────────────────────

    def _load_corp_code_cache(self) -> None:
        try:
            data = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
            self._corp_codes = data
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            self._corp_codes = {}

    def _save_corp_code_cache(self) -> None:
        try:
            _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            _CACHE_PATH.write_text(
                json.dumps(self._corp_codes, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            pass

    def _build_corp_code_map(self) -> None:
        """DART 전체 기업코드 목록 다운로드 → ticker→corp_code 맵 구축.

        corpCode.xml.zip 을 받아 파싱. 1회 다운로드 후 파일 캐시 사용.
        """
        resp = requests.get(
            f"{_BASE}/corpCode.xml",
            params={"crtfc_key": self._api_key},
            timeout=30,
        )
        resp.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            xml_bytes = zf.read("CORPCODE.xml")
        root = ET.fromstring(xml_bytes)
        for item in root.findall("list"):
            stock_code = (item.findtext("stock_code") or "").strip()
            corp_code  = (item.findtext("corp_code") or "").strip()
            if stock_code and corp_code:
                self._corp_codes[stock_code] = corp_code
        self._save_corp_code_cache()

    def get_corp_code(self, ticker: str) -> str | None:
        """종목코드 → DART corp_code 변환.

        캐시에 없으면 전체 기업코드 목록을 1회 다운로드해 구축.
        """
        if ticker in self._corp_codes:
            return self._corp_codes[ticker]
        if not self._api_key:
            return None
        try:
            self._build_corp_code_map()
            return self._corp_codes.get(ticker)
        except Exception:
            return None

    # ── 공시 목록 조회 ─────────────────────────────────────────────────────────

    def get_disclosures(
        self,
        ticker: str,
        days: int = 7,
        types: tuple[str, ...] = ("B", "D"),
    ) -> list[DisclosureItem]:
        """최근 N일 주요 공시 조회 (B=주요사항보고, D=지분공시)"""
        if not self._api_key:
            return []

        corp_code = self.get_corp_code(ticker)
        if not corp_code:
            return []

        end = datetime.now()
        start = end - timedelta(days=days)
        bgn_de = start.strftime("%Y%m%d")
        end_de = end.strftime("%Y%m%d")

        items: list[DisclosureItem] = []
        for pblntf_ty in types:
            try:
                resp = requests.get(
                    f"{_BASE}/list.json",
                    params={
                        "crtfc_key": self._api_key,
                        "corp_code": corp_code,
                        "bgn_de": bgn_de,
                        "end_de": end_de,
                        "pblntf_ty": pblntf_ty,
                        "page_count": 10,
                    },
                    timeout=5,
                )
                resp.raise_for_status()
                data = resp.json()
                if data.get("status") != "000":
                    continue
                for row in data.get("list", []):
                    items.append(DisclosureItem(
                        ticker=ticker,
                        corp_name=row.get("corp_name", ""),
                        title=row.get("report_nm", ""),
                        date=row.get("rcept_dt", ""),
                        rcept_no=row.get("rcept_no", ""),
                        pblntf_ty=pblntf_ty,
                    ))
            except Exception:
                continue

        # 최신순 정렬
        items.sort(key=lambda x: x.date, reverse=True)
        return items

    def get_latest(self, ticker: str, days: int = 7) -> DisclosureItem | None:
        """가장 최근 공시 1건 반환 (없으면 None)"""
        items = self.get_disclosures(ticker, days=days)
        return items[0] if items else None

    def get_document_text(self, rcept_no: str) -> str:
        """DART 공시 원문을 다운로드해 XML 태그 제거 후 평문 반환."""
        if not self._api_key or not rcept_no:
            return ""
        if rcept_no in self._document_cache:
            return self._document_cache[rcept_no]
        try:
            resp = requests.get(
                f"{_BASE}/document.xml",
                params={"crtfc_key": self._api_key, "rcept_no": rcept_no},
                timeout=5,
            )
            resp.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                names = zf.namelist()
                if not names:
                    return ""
                raw = zf.read(names[0])
            raw_text = raw.decode("utf-8", errors="ignore")
            plain = _TAG_RE.sub(" ", raw_text)
            self._document_cache[rcept_no] = plain
            return plain
        except Exception:
            self._document_cache[rcept_no] = ""
            return ""

    @staticmethod
    def extract_key_summary(plain_text: str, context_chars: int = 120) -> str:
        """XML 태그가 제거된 평문에서 핵심 키워드 주변 컨텍스트만 추출.

        LLM에 넘기기 전 처리:
          - 핵심 키워드(_KEY_PATTERNS) 주변 ±context_chars 추출
          - 중복 구간 제거
          - 공백·줄바꿈 정규화
        반환: 최대 ~800자 요약 문자열
        """
        if not plain_text:
            return ""

        text = " ".join(plain_text.split())  # 공백 정규화
        snippets: list[tuple[int, str]] = []

        for m in _KEY_RE.finditer(text):
            start = max(0, m.start() - context_chars)
            end = min(len(text), m.end() + context_chars)
            snippets.append((start, text[start:end]))

        if not snippets:
            # 키워드 없으면 앞부분만
            return text[:400]

        # 겹치는 구간 병합 후 정렬
        snippets.sort(key=lambda x: x[0])
        merged: list[str] = []
        last_end = -1
        for pos, snippet in snippets:
            s = max(0, pos - context_chars)
            e = s + len(snippet)
            if s > last_end + 20:      # 간격 20자 이상이면 별도 구간
                merged.append(snippet.strip())
                last_end = e
            # 겹치면 스킵 (이미 포함됨)

        summary = " … ".join(merged)
        return summary[:800]

    def enrich_content(self, item: DisclosureItem) -> DisclosureItem:
        """공시 원문을 받아 핵심 키워드 요약으로 item.content를 채운다."""
        if item.content:
            return item
        plain = self.get_document_text(item.rcept_no)
        item.content = self.extract_key_summary(plain)
        return item

    @property
    def available(self) -> bool:
        return bool(self._api_key)
