"""
MQK-v2 운영 오케스트레이터

운영 플로우:
  08:00  장전  → Market Data → Regime Agent → market_status.json
  08:30  후보  → Scanner → Technical → Flow → Theme Agent → candidates.json
  장중         → News Agent + Disclosure Agent + Portfolio Manager Agent
  매수 발생    → Risk Officer → Position Sizer → Telegram → Order Manager
  장마감       → Review Agent → Self Improvement Agent → journal.md
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from config.settings import RISK, LOG_CONFIG
from codes.market_data import MarketData
from codes.scanner import Scanner
from codes.technical import TechnicalAnalysis
from codes.flow import FlowAnalysis
from codes.risk_officer import RiskOfficer, RiskViolation, PortfolioState, TradeProposal
from codes.position_sizer import PositionSizer
from codes.order_manager import OrderManager, OrderRequest
from codes.stop_take_profit import StopTakeProfitManager
from agents.regime_agent import RegimeAgent
from agents.theme_agent import ThemeAgent
from agents.news_agent import NewsAgent
from agents.disclosure_agent import DisclosureAgent
from agents.portfolio_manager import PortfolioManagerAgent, Decision
from agents.review_agent import ReviewAgent
from agents.self_improvement_agent import SelfImprovementAgent
from broker.telegram import TelegramApproval, ApprovalRequest

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("mqk_v2")


class MQKOrchestrator:
    """MQK-v2 메인 오케스트레이터"""

    def __init__(self, kis_api=None):
        self._market_data = MarketData(data_source=kis_api)
        self._scanner = Scanner()
        self._technical = TechnicalAnalysis()
        self._flow = FlowAnalysis()
        self._risk_officer = RiskOfficer()
        self._position_sizer = PositionSizer()
        self._stp_manager = StopTakeProfitManager()
        self._regime_agent = RegimeAgent()
        self._theme_agent = ThemeAgent()
        self._news_agent = NewsAgent()
        self._disclosure_agent = DisclosureAgent()
        self._pm_agent = PortfolioManagerAgent()
        self._review_agent = ReviewAgent()
        self._si_agent = SelfImprovementAgent()
        self._telegram = TelegramApproval()
        self._order_manager = OrderManager(kis_api=kis_api, telegram=self._telegram)
        self._today = datetime.now().strftime("%Y-%m-%d")
        self._log_dir = LOG_CONFIG.base_dir / self._today
        self._log_dir.mkdir(parents=True, exist_ok=True)

    # ── 08:00 장전 ──────────────────────────────────────────────────────────

    def run_premarket(self) -> dict:
        """장전 시장 분석"""
        logger.info("[08:00] 장전 시장 분석 시작")
        index = self._market_data.get_index_status()

        # Regime Agent 호출 (LLM)
        market_ctx = {
            "kospi_change_pct": index.kospi_change_pct,
            "kosdaq_change_pct": index.kosdaq_change_pct,
            "market_news_summary": "",
        }
        regime = self._regime_agent.judge(market_ctx)
        logger.info(f"Regime: {regime.regime.value} (확신도 {regime.confidence}%)")

        market_status = {
            "date": self._today,
            "kospi": index.kospi,
            "kosdaq": index.kosdaq,
            "status": regime.status.value,
            "regime": regime.regime.value,
            "confidence": regime.confidence,
            "risk_notes": regime.risk_notes,
        }
        self._save_json("market_status.json", market_status)
        return market_status

    # ── 08:30 후보 생성 ─────────────────────────────────────────────────────

    def run_scan(self, market_status: dict) -> list[dict]:
        """후보 종목 30개 선발"""
        logger.info("[08:30] 종목 스캔 시작")
        tickers = self._market_data.get_universe()
        snapshots = [self._market_data.get_snapshot(t) for t in tickers[:100]]  # 실제는 전체

        technicals = {}
        flows = {}
        for snap in snapshots:
            bars = self._market_data.get_ohlcv(snap.ticker)
            if bars:
                technicals[snap.ticker] = self._technical.analyze(snap.ticker, bars)
            flows[snap.ticker] = self._flow.analyze(snap.ticker, [])

        candidates = self._scanner.scan(snapshots, technicals, flows)
        logger.info(f"후보 {len(candidates)}종목 선발")

        # Theme Agent 호출 (LLM) - 30종목 이하일 때만
        theme = self._theme_agent.analyze({
            "news_headlines": [],
            "top_gainers": [
                {"ticker": c.ticker, "name": c.name, "change_pct": c.change_pct, "sector": c.sector}
                for c in candidates[:10]
            ],
        })

        best_theme = theme.best
        best_theme_name = best_theme.theme if best_theme else ""

        result = [
            {
                "ticker": c.ticker,
                "name": c.name,
                "score": c.total_score,
                "theme": best_theme_name,
                "theme_match": bool(best_theme_name and c.sector == best_theme_name),
                "passed": c.passed_filters,
            }
            for c in candidates
        ]
        self._save_jsonl("candidate_scores.jsonl", result)
        return result

    # ── 장중 의사결정 ───────────────────────────────────────────────────────

    def evaluate_candidate(
        self,
        ticker: str,
        name: str,
        current_price: float,
        portfolio_state: PortfolioState,
    ) -> dict:
        """후보 종목 최종 판단"""
        logger.info(f"[장중] {name}({ticker}) 평가 중")

        bars = self._market_data.get_ohlcv(ticker)
        tech = self._technical.analyze(ticker, bars) if bars else None
        flow = self._flow.analyze(ticker, [])
        news = self._news_agent.evaluate(ticker, [])

        # Portfolio Manager Agent 호출 (핵심 LLM 호출)
        decision = self._pm_agent.decide(ticker, {
            "name": name,
            "current_price": current_price,
            "technical": tech,
            "flow": flow,
            "news_evaluations": news,
            "is_in_portfolio": any(
                p["ticker"] == ticker for p in portfolio_state.open_positions
            ),
        })

        log_entry = {
            "ticker": ticker,
            "decision": decision.decision.value,
            "confidence": decision.confidence,
            "reason": decision.reason[:100],
        }
        self._append_jsonl("llm_calls.jsonl", log_entry)

        if decision.decision == Decision.BUY and decision.confidence >= 70:
            return self._process_buy_signal(
                ticker, name, current_price, decision, tech, portfolio_state
            )
        return {"action": "SKIP", "ticker": ticker, "reason": decision.reason}

    def _process_buy_signal(
        self, ticker, name, current_price, decision, tech, portfolio_state
    ) -> dict:
        """매수 신호 처리: Risk → Size → Telegram → Order"""
        if tech is None:
            return {"action": "SKIP", "ticker": ticker, "reason": "기술적 데이터 없음"}

        # Position Sizer
        sizing = self._position_sizer.calculate(
            ticker=ticker,
            entry_price=current_price,
            atr=tech.atr,
            total_capital=portfolio_state.total_capital,
        )

        proposal = TradeProposal(
            ticker=ticker,
            theme="",
            entry_price=current_price,
            stop_loss_price=sizing.stop_loss_price,
            quantity=sizing.quantity,
        )

        # Risk Officer 검증
        try:
            self._risk_officer.check(proposal, portfolio_state)
        except RiskViolation as e:
            logger.warning(f"[RISK BLOCK] {ticker}: {e}")
            self._append_jsonl("risk_checks.jsonl", {
                "ticker": ticker, "blocked": True, "rule": e.rule, "detail": e.detail
            })
            return {"action": "BLOCKED", "ticker": ticker, "reason": str(e)}

        self._append_jsonl("risk_checks.jsonl", {
            "ticker": ticker, "blocked": False,
            "risk_pct": sizing.risk_pct, "quantity": sizing.quantity
        })

        # Telegram 승인
        if RISK.require_telegram_approval:
            approval_req = ApprovalRequest(
                ticker=ticker, name=name, decision="BUY",
                entry_price=current_price,
                stop_loss_price=sizing.stop_loss_price,
                quantity=sizing.quantity,
                risk_pct=sizing.risk_pct,
                confidence=decision.confidence,
                reason=decision.reason,
                counter_argument=decision.counter_argument,
            )
            approval = self._telegram.request_approval(approval_req)
            self._append_jsonl("telegram_approvals.jsonl", {
                "ticker": ticker,
                "approved": approval.approved,
                "request_id": approval.request_id,
            })
            if not approval.approved:
                return {"action": "REJECTED", "ticker": ticker, "reason": "텔레그램 거부"}

        # 주문 실행
        order = OrderRequest(
            ticker=ticker, name=name, side="BUY",
            quantity=sizing.quantity,
            price=current_price,
            stop_loss_price=sizing.stop_loss_price,
            reason=decision.reason,
            confidence=decision.confidence,
            approved_by="telegram",
        )
        result = self._order_manager.execute_buy(order)
        logger.info(f"[ORDER] BUY {ticker} {sizing.quantity}주: {'성공' if result.success else '실패'}")
        return {"action": "BUY_EXECUTED", "ticker": ticker, "success": result.success}

    # ── 장마감 복기 ─────────────────────────────────────────────────────────

    def run_close_review(self, today_trades: list[dict]) -> None:
        """장마감 복기 및 자기개선"""
        logger.info("[장마감] 거래 복기 시작")
        reviews = []
        for trade in today_trades:
            review = self._review_agent.analyze(trade)
            reviews.append(review)
            logger.info(f"복기: {review.ticker} {review.result} {review.pnl_pct:+.2f}%")

        if reviews:
            journal_summary = "\n".join(
                f"- {r.ticker}: {r.markdown[:200]}" for r in reviews
            )
            suggestions = self._si_agent.suggest(today_trades, journal_summary)
            for s in suggestions:
                logger.info(f"[개선 제안] {s.title}: {s.expected_effect}")
            logger.info("※ 개선안은 백테스트 검증 + 사용자 승인 후에만 실전 반영 가능")

    # ── 유틸 ────────────────────────────────────────────────────────────────

    def _save_json(self, filename: str, data: dict) -> None:
        path = self._log_dir / filename
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _save_jsonl(self, filename: str, records: list) -> None:
        path = self._log_dir / filename
        lines = [json.dumps(r, ensure_ascii=False) for r in records]
        path.write_text("\n".join(lines), encoding="utf-8")

    def _append_jsonl(self, filename: str, record: dict) -> None:
        path = self._log_dir / filename
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    orchestrator = MQKOrchestrator()
    market_status = orchestrator.run_premarket()
    candidates = orchestrator.run_scan(market_status)
    logger.info(f"오늘의 후보: {[c['ticker'] for c in candidates[:5]]}")
