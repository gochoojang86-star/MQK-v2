"""
Scanner Code - 5000종목 → 30종목 압축
LLM 미사용. 순수 필터링 로직.

비용 제어의 핵심: LLM은 이 필터 통과 후에만 호출된다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

from codes.market_data import MarketSnapshot
from codes.technical import TechnicalSignals
from codes.flow import FlowSignals
from config.settings import SCANNER


@dataclass
class CandidateScore:
    ticker: str
    name: str
    total_score: float
    trading_value_score: float
    new_high_score: float
    technical_score: float
    flow_score: float
    sector: str
    change_pct: float
    trading_value: float
    passed_filters: list[str] = field(default_factory=list)
    failed_filters: list[str] = field(default_factory=list)


class Scanner:
    """
    5000종목 → 30종목 필터링 엔진

    단계:
    1. 거래대금 필터 (최소 50억)
    2. 등락률 필터 (일정 기준 이상)
    3. 기술적 조건 필터
    4. 스코어링 후 상위 30종목 선발
    """

    def __init__(self, config=None):
        self._cfg = config or SCANNER

    def scan(
        self,
        snapshots: list[MarketSnapshot],
        technicals: dict[str, TechnicalSignals],
        flows: dict[str, FlowSignals],
    ) -> list[CandidateScore]:
        """전체 종목에서 후보 30종목 선발"""
        candidates = []
        for snap in snapshots:
            score = self._score(snap, technicals.get(snap.ticker), flows.get(snap.ticker))
            if score is not None:
                candidates.append(score)

        # 스코어 내림차순 정렬 후 상위 N개
        candidates.sort(key=lambda c: c.total_score, reverse=True)
        result = candidates[: self._cfg.candidate_count]

        return result

    def _score(
        self,
        snap: MarketSnapshot,
        tech: Optional[TechnicalSignals],
        flow: Optional[FlowSignals],
    ) -> Optional[CandidateScore]:
        """개별 종목 스코어 계산. 필수 조건 미달 시 None 반환."""
        passed = []
        failed = []

        # ── 필수 필터 ────────────────────────────────────────────────────────
        if snap.trading_value < self._cfg.min_trading_value_krw:
            failed.append("거래대금_미달")
            return None  # 거래대금 미달은 즉시 탈락

        passed.append("거래대금_통과")

        # ── 스코어 계산 ──────────────────────────────────────────────────────
        trading_value_score = min(snap.trading_value / 100_000_000_000, 1.0) * 30  # 최대 30점

        new_high_score = 0.0
        technical_score = 0.0
        if tech:
            new_high_score = 20.0 if tech.new_high_52w else 0.0
            technical_score = (
                (10 if tech.is_vcp else 0)
                + (10 if tech.is_box_breakout else 0)
                + (5 if tech.is_pullback else 0)
                + (5 if tech.above_ma60 else 0)
            )
            if tech.new_high_52w:
                passed.append("신고가")
            if tech.is_vcp:
                passed.append("VCP")
            if tech.is_box_breakout:
                passed.append("박스돌파")

        flow_score = 0.0
        if flow:
            flow_score = (
                (15 if flow.is_strong_inflow else 0)
                + min(flow.foreign_consecutive_buy * 2, 10)
            )
            if flow.is_strong_inflow:
                passed.append("강한수급")

        total = trading_value_score + new_high_score + technical_score + flow_score

        return CandidateScore(
            ticker=snap.ticker,
            name=snap.name,
            total_score=round(total, 2),
            trading_value_score=round(trading_value_score, 2),
            new_high_score=new_high_score,
            technical_score=round(technical_score, 2),
            flow_score=round(flow_score, 2),
            sector="",
            change_pct=snap.change_pct,
            trading_value=snap.trading_value,
            passed_filters=passed,
            failed_filters=failed,
        )

    def save_candidates(self, candidates: list[CandidateScore], output_path: Path) -> None:
        """candidate_scores.jsonl 저장"""
        lines = []
        for c in candidates:
            lines.append(json.dumps({
                "ticker": c.ticker,
                "name": c.name,
                "total_score": c.total_score,
                "trading_value": c.trading_value,
                "passed": c.passed_filters,
            }, ensure_ascii=False))
        output_path.write_text("\n".join(lines), encoding="utf-8")
