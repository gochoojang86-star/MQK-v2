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
from config.settings import SCANNER, REVERSAL


@dataclass
class CandidateScore:
    ticker: str
    name: str
    total_score: float
    trading_value_score: float
    new_high_score: float
    technical_score: float
    flow_score: float
    leadership_score: float
    sector: str
    change_pct: float
    trading_value: float
    market_rank: int = 0
    theme_rank: int = 0
    is_theme_leader: bool = False
    strategy_type: str = "TREND"
    reversal_score: float = 0.0
    disparity20_pct: float = 0.0
    disparity60_pct: float = 0.0
    oversold_reason: str = ""
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

        self._apply_leader_ranking(candidates)

        # 대장주 우선, 그 다음 총점 순으로 상위 N개 선발
        candidates.sort(key=lambda c: c.total_score, reverse=True)
        leaders = [c for c in candidates if c.is_theme_leader]
        non_leaders = [c for c in candidates if not c.is_theme_leader]
        result = (leaders + non_leaders)[: self._cfg.candidate_count]

        return result

    def scan_reversal(
        self,
        snapshots: list[MarketSnapshot],
        technicals: dict[str, TechnicalSignals],
        flows: dict[str, FlowSignals],
    ) -> list[CandidateScore]:
        """폭락/과매도 구간의 REVERSAL 전술 후보 스캔."""
        candidates = []
        for snap in snapshots:
            score = self._score_reversal(snap, technicals.get(snap.ticker), flows.get(snap.ticker))
            if score is not None:
                candidates.append(score)

        self._apply_leader_ranking(candidates)
        candidates.sort(key=lambda c: c.total_score, reverse=True)
        return candidates[: self._cfg.candidate_count]

    def _apply_leader_ranking(self, candidates: list[CandidateScore]) -> None:
        """섹터/테마 프록시 단위 대장주 랭킹을 점수에 반영한다."""
        candidates.sort(key=lambda c: c.total_score, reverse=True)
        for rank, candidate in enumerate(candidates, start=1):
            candidate.market_rank = rank

        by_sector: dict[str, list[CandidateScore]] = {}
        for candidate in candidates:
            sector = candidate.sector or "UNKNOWN"
            by_sector.setdefault(sector, []).append(candidate)

        for sector_candidates in by_sector.values():
            sector_candidates.sort(key=lambda c: c.total_score, reverse=True)
            for rank, candidate in enumerate(sector_candidates, start=1):
                candidate.theme_rank = rank
                candidate.is_theme_leader = rank == 1
                candidate.leadership_score = max(0.0, 20.0 - ((rank - 1) * 5.0))
                candidate.total_score = round(candidate.total_score + candidate.leadership_score, 2)
                if rank == 1:
                    candidate.passed_filters.append("섹터대장")

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
        if snap.trading_halted:
            failed.append("거래정지")
            return None

        if snap.administrative_issue:
            failed.append("관리종목")
            return None

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
            leadership_score=0.0,
            sector=snap.sector,
            change_pct=snap.change_pct,
            trading_value=snap.trading_value,
            passed_filters=passed,
            failed_filters=failed,
        )

    def _score_reversal(
        self,
        snap: MarketSnapshot,
        tech: Optional[TechnicalSignals],
        flow: Optional[FlowSignals],
    ) -> Optional[CandidateScore]:
        passed = []
        failed = []

        if not REVERSAL.enabled:
            failed.append("reversal_disabled")
            return None
        if snap.trading_halted:
            failed.append("거래정지")
            return None
        if snap.administrative_issue:
            failed.append("관리종목")
            return None
        if snap.trading_value < self._cfg.min_trading_value_krw:
            failed.append("거래대금_미달")
            return None
        if tech is None:
            failed.append("기술데이터_없음")
            return None
        if tech.rsi > REVERSAL.rsi_threshold:
            failed.append("RSI_과매도미달")
            return None

        disparity20_ok = tech.disparity20_pct <= REVERSAL.min_disparity20_pct
        disparity60_ok = tech.disparity60_pct <= REVERSAL.min_disparity60_pct
        if not (disparity20_ok or disparity60_ok):
            failed.append("이격도_미달")
            return None

        passed.append("거래대금_통과")
        passed.append("과매도")
        if disparity20_ok:
            passed.append("이격20")
        if disparity60_ok:
            passed.append("이격60")

        liquidity_score = min(snap.trading_value / 100_000_000_000, 1.0) * 25
        oversold_score = max(0.0, (REVERSAL.rsi_threshold - tech.rsi) * 1.5)
        disparity_score = max(abs(min(tech.disparity20_pct, 0.0)), abs(min(tech.disparity60_pct, 0.0))) * 2
        selloff_score = max(abs(min(snap.change_pct, 0.0)), 0.0) * 2
        flow_score = 0.0
        if flow:
            flow_score = min(flow.foreign_consecutive_buy * 2, 6)
            if flow.is_strong_inflow:
                flow_score += 4
                passed.append("반전수급")

        total = liquidity_score + oversold_score + disparity_score + selloff_score + flow_score
        oversold_reason = f"RSI {tech.rsi:.1f}, 20일 이격 {tech.disparity20_pct:.1f}%, 60일 이격 {tech.disparity60_pct:.1f}%"

        return CandidateScore(
            ticker=snap.ticker,
            name=snap.name,
            total_score=round(total, 2),
            trading_value_score=round(liquidity_score, 2),
            new_high_score=0.0,
            technical_score=round(oversold_score + disparity_score + selloff_score, 2),
            flow_score=round(flow_score, 2),
            leadership_score=0.0,
            sector=snap.sector,
            change_pct=snap.change_pct,
            trading_value=snap.trading_value,
            strategy_type="REVERSAL",
            reversal_score=round(oversold_score + disparity_score + selloff_score, 2),
            disparity20_pct=tech.disparity20_pct,
            disparity60_pct=tech.disparity60_pct,
            oversold_reason=oversold_reason,
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
                "theme_rank": c.theme_rank,
                "is_theme_leader": c.is_theme_leader,
                "strategy_type": c.strategy_type,
                "passed": c.passed_filters,
            }, ensure_ascii=False))
        output_path.write_text("\n".join(lines), encoding="utf-8")
