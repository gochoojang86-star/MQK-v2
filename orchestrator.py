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

from config.settings import RISK, LOG_CONFIG, EXECUTION
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
from codes.news_fetcher import NaverNewsFetcher, KISNewsFetcher
from codes.disclosure_fetcher import DARTFetcher
from codes.flow import FlowRecord
from broker.telegram_news import get_recent_news as get_telegram_news

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("mqk_v2")


class MQKOrchestrator:
    """MQK-v2 메인 오케스트레이터"""

    def __init__(self, kis_api=None, dry_run_orders: bool | None = None):
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
        self._order_manager = OrderManager(
            kis_api=kis_api,
            telegram=self._telegram,
            dry_run=EXECUTION.order_dry_run if dry_run_orders is None else dry_run_orders,
        )
        self._naver_news = NaverNewsFetcher()
        self._kis_news = KISNewsFetcher(kis_api=kis_api)
        self._dart = DARTFetcher()
        self._current_theme: str = ""       # run_scan()에서 갱신
        self._last_regime = None            # run_premarket()에서 갱신 → PM Agent에 전달
        self._last_theme = None             # run_scan()에서 갱신 → PM Agent에 전달
        self._sector_performance: dict = {} # run_scan()에서 갱신 → Regime Agent 재호출 시 사용
        self._today = datetime.now().strftime("%Y-%m-%d")
        self._log_dir = LOG_CONFIG.base_dir / self._today
        self._log_dir.mkdir(parents=True, exist_ok=True)

    # ── 08:00 장전 ──────────────────────────────────────────────────────────

    def run_premarket(self) -> dict:
        """장전 시장 분석"""
        logger.info("[08:00] 장전 시장 분석 시작")
        index = self._market_data.get_index_status()

        # 시장 전반 뉴스 수집 — KIS API는 개별종목만 지원, 시장뉴스는 Naver 사용
        market_news_items = self._naver_news.search("코스피 코스닥 시장 주식", display=10)
        market_news_summary = " | ".join(n.title for n in market_news_items[:5])

        # Regime Agent 호출 (LLM)
        market_ctx = {
            "kospi_change_pct": index.kospi_change_pct,
            "kosdaq_change_pct": index.kosdaq_change_pct,
            "market_news_summary": market_news_summary,
            "sector_performance": self._sector_performance,  # 전날 scan 결과 재사용
        }
        regime = self._regime_agent.judge(market_ctx)
        self._last_regime = regime
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
            # snapshot의 당일 외국인/기관 데이터로 FlowRecord 구성
            flow_records = [FlowRecord(
                date=self._today,
                ticker=snap.ticker,
                foreign_net=snap.foreign_net,
                institution_net=snap.institution_net,
                program_net=0.0,
                trading_value=snap.trading_value,
            )] if (snap.foreign_net or snap.institution_net) else []
            flows[snap.ticker] = self._flow.analyze(snap.ticker, flow_records)

        candidates = self._scanner.scan(snapshots, technicals, flows)
        logger.info(f"후보 {len(candidates)}종목 선발")

        # 1차 검색: 스캐너 상위 종목명으로 동적 쿼리 생성 (고정 키워드 대신)
        top_names = " ".join(c.name for c in candidates[:3]) if candidates else ""
        initial_query = f"{top_names} 급등 테마" if top_names else "테마주 주도주 상한가 급등"
        initial_news = self._naver_news.search(initial_query, display=10)

        # Theme Agent 호출 (LLM) - 30종목 이하일 때만
        theme = self._theme_agent.analyze({
            "news_headlines": [n.title for n in initial_news],
            "top_gainers": [
                {"ticker": c.ticker, "name": c.name, "change_pct": c.change_pct, "sector": c.sector}
                for c in candidates[:10]
            ],
        })

        best_theme = theme.best
        best_theme_name = best_theme.theme if best_theme else ""

        # 2차 검색: Theme Agent가 식별한 테마명으로 Naver 재검색 (추가 LLM 호출 없음)
        if best_theme_name:
            targeted_news = self._naver_news.search(f"{best_theme_name} 테마 대장주", display=10)
            news_headlines = [n.title for n in targeted_news] if targeted_news else [n.title for n in initial_news]
        else:
            news_headlines = [n.title for n in initial_news]

        # 이후 evaluate_candidate()에서 재사용
        self._current_theme = best_theme_name
        self._last_theme = theme

        # 섹터별 평균 등락률 계산 → Regime Agent 재사용 컨텍스트
        sector_perf: dict[str, list[float]] = {}
        for snap in snapshots:
            if snap.sector:
                sector_perf.setdefault(snap.sector, []).append(snap.change_pct)
        self._sector_performance = {
            s: round(sum(v) / len(v), 2) for s, v in sector_perf.items()
        }

        logger.info(f"주도 테마: {best_theme_name or '미식별'}")

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

        # 스냅샷 1회 조회 → flow / disclosure market_cap 공유
        snap = self._market_data.get_snapshot(ticker)

        bars = self._market_data.get_ohlcv(ticker)
        tech = self._technical.analyze(ticker, bars) if bars else None

        # snapshot 당일 수급 데이터로 FlowRecord 구성 (빈 리스트 방지)
        flow_records = [FlowRecord(
            date=self._today,
            ticker=ticker,
            foreign_net=snap.foreign_net,
            institution_net=snap.institution_net,
            program_net=0.0,
            trading_value=snap.trading_value,
        )] if (snap.foreign_net or snap.institution_net) else []
        flow = self._flow.analyze(ticker, flow_records)

        # 뉴스 수집: KIS 종목 뉴스 + Naver 종목명+테마 검색 + Telegram DB
        kis_items = self._kis_news.get_news(ticker=ticker, limit=5)
        naver_query = f"{name} {self._current_theme}".strip() if self._current_theme else name
        naver_items = self._naver_news.search(naver_query, display=5)
        tg_items = [
            {"title": n["title"], "content": "", "date": n["date"], "source": n["source"]}
            for n in get_telegram_news(ticker=ticker, hours=2)
        ]
        raw_news = [n.to_dict() for n in kis_items + naver_items] + tg_items
        news = self._news_agent.evaluate(ticker, raw_news)

        # 공시 수집 + Disclosure Agent 해석
        disc_result = None
        if self._dart.available:
            dart_item = self._dart.get_latest(ticker, days=7)
            if dart_item:
                disc_result = self._disclosure_agent.interpret(
                    ticker,
                    {**dart_item.to_dict(), "market_cap": snap.market_cap},
                )
                logger.info(
                    f"[공시] {name}({ticker}): {dart_item.title[:40]} → {disc_result.impact.value}"
                )

        # Portfolio Manager Agent 호출 (핵심 LLM 호출)
        # regime / theme 을 컨텍스트에 포함 — Decision Hierarchy 완성
        decision = self._pm_agent.decide(ticker, {
            "name": name,
            "current_price": current_price,
            "regime": self._last_regime,
            "theme": self._last_theme,
            "technical": tech,
            "flow": flow,
            "news_evaluations": news,
            "disclosure": disc_result,
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
        try:
            sizing = self._position_sizer.calculate(
                ticker=ticker,
                entry_price=current_price,
                atr=tech.atr,
                total_capital=portfolio_state.total_capital,
            )
        except ValueError as e:
            self._append_jsonl("risk_checks.jsonl", {
                "ticker": ticker, "blocked": True, "rule": "POSITION_SIZING", "detail": str(e)
            })
            return {"action": "BLOCKED", "ticker": ticker, "reason": str(e)}

        proposal = TradeProposal(
            ticker=ticker,
            theme=self._current_theme or "UNKNOWN",
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
        approval_request_id = None
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
            approval_request_id = approval.request_id
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
            approval_request_id=approval_request_id,
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
