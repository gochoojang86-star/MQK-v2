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
import os
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
from codes.trade_journal import TradeJournal
from codes.stop_take_profit import StopTakeProfitManager, PositionStatus, ExitSignal
from codes.improvement_manager import ImprovementManager
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
        self._kis_api = kis_api
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
        self._journal = TradeJournal()
        # KIS_USE_MCP=true + MCP 서버 가동 시 OrderManager가 MCP 경로로 주문
        order_api = kis_api
        if os.environ.get("KIS_USE_MCP", "false").lower() in {"1", "true", "yes"}:
            from broker.kis_mcp_client import KISMCPClient
            mcp = KISMCPClient()
            if mcp.available:
                logger.info("[OrderManager] KIS MCP 서버 감지 → MCP 주문 경로 사용")
                order_api = mcp
            else:
                logger.info("[OrderManager] KIS MCP 서버 미실행 → KIS API 폴백")
        self._order_manager = OrderManager(
            kis_api=order_api,
            telegram=self._telegram,
            dry_run=EXECUTION.order_dry_run if dry_run_orders is None else dry_run_orders,
            journal=self._journal,
        )
        self._naver_news = NaverNewsFetcher()
        if not self._naver_news.available:
            logger.warning("NAVER_CLIENT_ID/SECRET 미설정 — Naver 뉴스 비활성화")
        self._kis_news = KISNewsFetcher(kis_api=kis_api)
        self._dart = DARTFetcher()
        if not self._dart.available:
            logger.warning("DART_AUTH_KEY 미설정 — 공시 수집 비활성화")
        self._improvement_mgr = ImprovementManager(telegram=self._telegram)
        self._current_theme: str = ""       # run_scan()에서 갱신
        self._last_regime = None            # run_premarket()에서 갱신 → PM Agent에 전달
        self._last_theme = None             # run_scan()에서 갱신 → PM Agent에 전달
        self._candidate_context: dict[str, dict] = {}  # run_scan() 후보 메타 → PM Agent에 전달
        self._sector_performance: dict = {} # run_scan()에서 갱신 → Regime Agent 재호출 시 사용
        self._atr_cache: dict[str, float] = {}  # {ticker: atr} — 하루 한 번만 갱신
        self._today = datetime.now().strftime("%Y-%m-%d")
        self._log_dir = LOG_CONFIG.base_dir / self._today
        self._log_dir.mkdir(parents=True, exist_ok=True)

    def build_portfolio_state(
        self,
        theme_by_ticker: dict[str, str] | None = None,
    ) -> PortfolioState:
        """KIS 잔고 응답을 RiskOfficer용 PortfolioState로 변환."""
        if self._kis_api is None or not hasattr(self._kis_api, "get_balance"):
            raise RuntimeError("PortfolioState 생성을 위해 get_balance() 가능한 KIS API가 필요합니다.")

        balance = self._kis_api.get_balance()
        holdings = balance.get("output1") or balance.get("holdings") or []
        summary = balance.get("output2") or balance.get("summary") or {}
        if isinstance(summary, list):
            summary = summary[0] if summary else {}

        theme_by_ticker = theme_by_ticker or {}
        total_capital = self._first_number(
            summary,
            [
                "tot_evlu_amt",
                "nass_amt",
                "tot_asst_amt",
                "total_capital",
                "dnca_tot_amt",
            ],
        )

        open_positions = []
        theme_value: dict[str, float] = {}
        position_value_total = 0.0
        for row in holdings:
            ticker = str(
                row.get("ticker")
                or row.get("pdno")
                or row.get("mksc_shrn_iscd")
                or ""
            ).strip()
            quantity = self._first_number(row, ["quantity", "hldg_qty", "ord_psbl_qty"])
            if not ticker or quantity <= 0:
                continue

            current_price = self._first_number(row, ["current_price", "prpr", "now_pric"])
            avg_price = self._first_number(row, ["avg_price", "pchs_avg_pric", "pchs_avg_prc"])
            market_value = self._first_number(row, ["market_value", "evlu_amt"])
            if market_value <= 0 and current_price > 0:
                market_value = current_price * quantity
            position_value_total += market_value

            theme = theme_by_ticker.get(ticker) or row.get("theme") or "UNKNOWN"
            theme_value[theme] = theme_value.get(theme, 0.0) + market_value
            open_positions.append({
                "ticker": ticker,
                "name": row.get("name") or row.get("prdt_name") or ticker,
                "quantity": int(quantity),
                "avg_price": avg_price,
                "current_price": current_price,
                "market_value": market_value,
                "theme": theme,
            })

        if total_capital <= 0:
            cash = self._first_number(summary, ["cash", "dnca_tot_amt", "ord_psbl_cash"])
            total_capital = cash + position_value_total
        if total_capital <= 0:
            raise RuntimeError("KIS 잔고 응답에서 총자산을 계산할 수 없습니다.")

        theme_exposure = {
            theme: round(value / total_capital * 100, 4)
            for theme, value in theme_value.items()
        }
        daily_pnl = self._first_number(
            summary,
            [
                "daily_pnl",
                "thdt_evlu_pfls_amt",
                "asst_icdc_amt",
            ],
        )

        return PortfolioState(
            total_capital=total_capital,
            daily_pnl=daily_pnl,
            open_positions=open_positions,
            theme_exposure=theme_exposure,
        )

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
            "kospi_trading_value": index.kospi_trading_value,
            "kosdaq_trading_value": index.kosdaq_trading_value,
            "kospi_advancers": index.kospi_advancers,
            "kospi_decliners": index.kospi_decliners,
            "kosdaq_advancers": index.kosdaq_advancers,
            "kosdaq_decliners": index.kosdaq_decliners,
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
            "kospi_trading_value": index.kospi_trading_value,
            "kosdaq_trading_value": index.kosdaq_trading_value,
            "kospi_advancers": index.kospi_advancers,
            "kospi_decliners": index.kospi_decliners,
            "kosdaq_advancers": index.kosdaq_advancers,
            "kosdaq_decliners": index.kosdaq_decliners,
            "status": regime.status.value,
            "regime": regime.regime.value,
            "confidence": regime.confidence,
            "risk_notes": regime.risk_notes,
        }
        self._save_json("market_status.json", market_status)

        # 장전에 보유 포지션 ATR 미리 계산 → 장중 run_position_exit_check API 호출 절감
        self.warm_atr_cache()

        return market_status

    # ── 08:30 후보 생성 ─────────────────────────────────────────────────────

    def run_scan(self, market_status: dict) -> list[dict]:
        """후보 종목 30개 선발"""
        logger.info("[08:30] 종목 스캔 시작")
        if market_status.get("status") == "RED":
            logger.warning("[SCAN BLOCK] 시장 상태 RED - 신규 후보 스캔 중단")
            self._save_jsonl("candidate_scores.jsonl", [])
            return []

        tickers = self._market_data.get_universe()
        snapshots = [self._market_data.get_snapshot(t) for t in tickers]

        technicals = {}
        flows = {}
        for snap in snapshots:
            bars = self._market_data.get_ohlcv(snap.ticker)
            if bars:
                technicals[snap.ticker] = self._technical.analyze(snap.ticker, bars)
            flow_records = self._get_flow_records(snap.ticker, snap)
            flows[snap.ticker] = self._flow.analyze(snap.ticker, flow_records)

        candidates = self._scanner.scan(snapshots, technicals, flows)
        logger.info(f"후보 {len(candidates)}종목 선발")

        # 거래대금 순위를 FlowSignals.trading_value_rank에 반영
        sorted_by_tv = sorted(snapshots, key=lambda s: s.trading_value, reverse=True)
        tv_rank = {s.ticker: i + 1 for i, s in enumerate(sorted_by_tv)}
        for ticker, flow_signals in flows.items():
            if flow_signals is not None:
                flow_signals.trading_value_rank = tv_rank.get(ticker)

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

        result = []
        candidate_context: dict[str, dict] = {}
        for rank, c in enumerate(candidates, start=1):
            flow_signals = flows.get(c.ticker)
            is_leader = bool(
                best_theme
                and (
                    c.name in best_theme.leader_candidates
                    or c.ticker in best_theme.leader_candidates
                )
            )
            is_laggard = bool(
                best_theme
                and (
                    c.name in best_theme.laggard_stocks
                    or c.ticker in best_theme.laggard_stocks
                )
            )
            item = {
                "ticker": c.ticker,
                "name": c.name,
                "score": c.total_score,
                "rank": rank,
                "theme": best_theme_name,
                "theme_match": bool(best_theme_name and c.sector == best_theme_name),
                "sector": c.sector,
                "change_pct": c.change_pct,
                "trading_value": c.trading_value,
                "trading_value_score": c.trading_value_score,
                "new_high_score": c.new_high_score,
                "technical_score": c.technical_score,
                "flow_score": c.flow_score,
                "trading_value_rank": flow_signals.trading_value_rank if flow_signals else None,
                "is_theme_leader": is_leader,
                "is_laggard": is_laggard,
                "theme_news": news_headlines[:3],
                "passed": c.passed_filters,
            }
            result.append(item)
            candidate_context[c.ticker] = item
        self._candidate_context = candidate_context
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

        flow_records = self._get_flow_records(ticker, snap)
        flow = self._flow.analyze(ticker, flow_records)

        # 뉴스 수집: KIS 종목 뉴스 + Naver 종목명+테마 검색 + Telegram DB
        kis_items = self._kis_news.get_news(ticker=ticker, limit=5)
        naver_query = f"{name} {self._current_theme}".strip() if self._current_theme else name
        naver_items = self._naver_news.search(naver_query, display=5)
        tg_items = [
            {"title": n["title"], "content": "", "date": n["date"], "source": n["source"]}
            for n in get_telegram_news(ticker=ticker, hours=2)
        ]
        reaction = self._build_reaction_context(snap, bars)
        raw_news = [
            self._with_reaction_context(n.to_dict(), reaction, self._current_theme)
            for n in kis_items + naver_items
        ] + [
            self._with_reaction_context(n, reaction, self._current_theme)
            for n in tg_items
        ]
        news = self._news_agent.evaluate(ticker, raw_news)

        # 공시 수집 + Disclosure Agent 해석
        disc_result = None
        if self._dart.available:
            dart_item = self._dart.get_latest(ticker, days=7)
            if dart_item:
                dart_item = self._dart.enrich_content(dart_item)
                disc_result = self._disclosure_agent.interpret(
                    ticker,
                    {
                        **dart_item.to_dict(),
                        "market_cap": snap.market_cap,
                        **reaction,
                    },
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
            "candidate": getattr(self, "_candidate_context", {}).get(ticker, {}),
            "reaction": reaction,
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

    def process_position_exit(
        self,
        position: PositionStatus,
        name: str,
        current_price: float,
        reason: str = "",
    ) -> dict:
        """보유 포지션 손절/익절 신호를 매도 주문으로 연결."""
        self._stp_manager.update_trailing(position, current_price)
        signal = self._stp_manager.evaluate(position, current_price)
        if signal == ExitSignal.HOLD:
            return {"action": "HOLD", "ticker": position.ticker}

        sell_quantity = position.quantity
        if signal == ExitSignal.TARGET_1:
            sell_quantity = max(1, int(position.quantity * position.config.partial_exit_pct))
            position.target1_hit = True

        order = OrderRequest(
            ticker=position.ticker,
            name=name,
            side="SELL",
            quantity=sell_quantity,
            price=current_price,
            stop_loss_price=position.stop_loss_price,
            reason=reason or signal.value,
            confidence=100,
        )
        result = self._order_manager.execute_sell(order)
        self._append_jsonl("exit_signals.jsonl", {
            "ticker": position.ticker,
            "signal": signal.value,
            "quantity": sell_quantity,
            "price": current_price,
            "success": result.success,
        })
        logger.info(
            f"[EXIT] {signal.value} {position.ticker} {sell_quantity}주: "
            f"{'성공' if result.success else '실패'}"
        )
        return {
            "action": "SELL_EXECUTED",
            "ticker": position.ticker,
            "signal": signal.value,
            "quantity": sell_quantity,
            "success": result.success,
        }

    def run_position_exit_check(self) -> list[dict]:
        """저널의 미청산 포지션을 조회해 손절/익절 조건을 점검."""
        results = []
        for row in self._journal.get_open_positions():
            ticker = row["ticker"]
            snapshot = self._market_data.get_snapshot(ticker)
            position = PositionStatus(
                ticker=ticker,
                entry_price=float(row["entry_price"]),
                stop_loss_price=float(row["stop_loss_price"]),
                quantity=int(row["quantity"]),
                atr=self._estimate_atr(ticker),
                highest_price=max(
                    float(row.get("highest_price") or row["entry_price"]),
                    snapshot.current_price,
                ),
            )
            results.append(
                self.process_position_exit(
                    position=position,
                    name=row.get("name") or ticker,
                    current_price=snapshot.current_price,
                )
            )
        return results

    # ── 장마감 복기 ─────────────────────────────────────────────────────────

    def run_close_review(self) -> None:
        """장마감 복기 및 자기개선"""
        logger.info("[장마감] 거래 복기 시작")
        today_trades = self._journal.get_closed_trades(days=1)
        if not today_trades:
            logger.info("[장마감] 오늘 청산 거래 없음")
            return

        reviews = []
        for trade in today_trades:
            review = self._review_agent.analyze(trade)
            reviews.append(review)
            logger.info(f"복기: {review.ticker} {review.result} {review.pnl_pct:+.2f}%")

        journal_summary = "\n".join(
            f"- {r.ticker}: {r.result} {r.pnl_pct:+.2f}%" for r in reviews
        )
        proposals = self._si_agent.suggest(today_trades, journal_summary)
        for p in proposals:
            pid = self._improvement_mgr.save(p)
            logger.info(f"[개선 제안] #{pid} {p.title} → 텔레그램 통보 완료")
        logger.info("※ 승인된 제안만 수동으로 config/settings.py에 반영 가능")

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

    def _get_flow_records(self, ticker: str, snapshot=None) -> list[FlowRecord]:
        kis_api = getattr(self, "_kis_api", None)
        if kis_api is not None and hasattr(kis_api, "get_investor_flow_history"):
            records = kis_api.get_investor_flow_history(ticker, days=3)
            if records:
                return [
                    FlowRecord(
                        date=str(r.get("date", "")),
                        ticker=str(r.get("ticker", ticker)),
                        foreign_net=self._number(r.get("foreign_net")),
                        institution_net=self._number(r.get("institution_net")),
                        program_net=self._number(r.get("program_net")),
                        trading_value=self._number(r.get("trading_value")),
                    )
                    for r in records
                ]

        if snapshot and (snapshot.foreign_net or snapshot.institution_net or snapshot.program_net):
            return [FlowRecord(
                date=self._today,
                ticker=ticker,
                foreign_net=snapshot.foreign_net,
                institution_net=snapshot.institution_net,
                program_net=snapshot.program_net,
                trading_value=snapshot.trading_value,
            )]
        return []

    def _estimate_atr(self, ticker: str) -> float:
        """ATR 추정. 당일 첫 호출만 API를 사용하고 이후엔 캐시 반환."""
        if ticker in self._atr_cache:
            return self._atr_cache[ticker]
        bars = self._market_data.get_ohlcv(ticker)
        atr = self._technical.calculate_atr(bars) if bars else 0.0
        self._atr_cache[ticker] = atr
        return atr

    def warm_atr_cache(self) -> None:
        """보유 포지션의 ATR을 장전에 미리 계산해 캐시에 적재."""
        journal = getattr(self, "_journal", None)
        if journal is None:
            return
        open_pos = journal.get_open_positions()
        if not open_pos:
            return
        logger.info(f"[ATR 워밍] 보유 종목 {len(open_pos)}개 ATR 사전 계산")
        for row in open_pos:
            self._estimate_atr(row["ticker"])  # 캐시 미스 → API 호출 후 저장

    def _build_reaction_context(self, snapshot, bars) -> dict:
        """뉴스/공시 이후 반응 판단에 필요한 가격·거래대금 요약."""
        recent = bars[-20:] if bars else []
        avg_trading_value = 0.0
        if recent:
            avg_trading_value = sum(b.trading_value for b in recent) / len(recent)
        trading_value_ratio = (
            snapshot.trading_value / avg_trading_value
            if avg_trading_value > 0
            else 0.0
        )
        return {
            "price_reaction_pct": snapshot.change_pct,
            "current_trading_value": snapshot.trading_value,
            "avg_trading_value_20d": avg_trading_value,
            "trading_value_ratio_20d": trading_value_ratio,
        }

    def _with_reaction_context(self, item: dict, reaction: dict, theme: str) -> dict:
        enriched = dict(item)
        enriched.update(reaction)
        if theme:
            enriched["related_theme"] = theme
        return enriched

    def _first_number(self, row: dict, keys: list[str]) -> float:
        for key in keys:
            value = row.get(key)
            if value in (None, ""):
                continue
            try:
                return float(str(value).replace(",", "").strip())
            except ValueError:
                continue
        return 0.0

    def _number(self, value) -> float:
        if value in (None, ""):
            return 0.0
        try:
            return float(str(value).replace(",", "").strip())
        except ValueError:
            return 0.0


if __name__ == "__main__":
    orchestrator = MQKOrchestrator()
    market_status = orchestrator.run_premarket()
    candidates = orchestrator.run_scan(market_status)
    logger.info(f"오늘의 후보: {[c['ticker'] for c in candidates[:5]]}")
