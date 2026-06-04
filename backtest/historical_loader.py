"""
Historical Loader - OHLCV 히스토리컬 데이터 수집·캐시
KIS API에서 받아 JSON으로 파일 캐시. LLM 미사용.
"""
from __future__ import annotations

import json
from pathlib import Path
from codes.market_data import OHLCVBar

_DEFAULT_CACHE = Path(__file__).parent.parent / "data" / "cache" / "ohlcv"


class HistoricalLoader:
    def __init__(self, cache_dir: Path = _DEFAULT_CACHE, kis_api=None):
        self._cache = Path(cache_dir)
        self._cache.mkdir(parents=True, exist_ok=True)
        self._kis = kis_api

    def load(self, ticker: str, period: int = 250) -> list[OHLCVBar]:
        """캐시 우선, 없으면 KIS API 조회."""
        cached = self.load_cache(ticker)
        if len(cached) >= period:
            return cached[-period:]
        if self._kis is None:
            return cached
        raw = self._kis.get_ohlcv(ticker, period)
        bars = [self._coerce(r) for r in raw if r]
        bars = [b for b in bars if b is not None]
        if bars:
            self.save_cache(ticker, bars)
        return bars

    def load_cache(self, ticker: str) -> list[OHLCVBar]:
        path = self._cache / f"{ticker}.json"
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        return [OHLCVBar(**row) for row in data]

    def save_cache(self, ticker: str, bars: list[OHLCVBar]) -> None:
        path = self._cache / f"{ticker}.json"
        path.write_text(
            json.dumps([b.__dict__ for b in bars], ensure_ascii=False),
            encoding="utf-8",
        )

    @staticmethod
    def _coerce(row: dict) -> OHLCVBar | None:
        try:
            def f(k, *alts):
                for key in (k, *alts):
                    v = row.get(key)
                    if v not in (None, ""):
                        return float(str(v).replace(",", ""))
                return 0.0
            return OHLCVBar(
                date=str(row.get("stck_bsop_date") or row.get("date") or ""),
                open=f("stck_oprc", "open"),
                high=f("stck_hgpr", "high"),
                low=f("stck_lwpr", "low"),
                close=f("stck_clpr", "close"),
                volume=int(f("acml_vol", "volume")),
                trading_value=f("acml_tr_pbmn", "trading_value"),
            )
        except Exception:
            return None
