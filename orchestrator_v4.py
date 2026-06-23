"""MQK v4 오케스트레이터 — 국장 세력주 스나이퍼.

v3 코드베이스(Safety Layer, MIL, broker)를 재사용하되
Phase/프롬프트/철학을 전면 교체한다.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path

from agents.regime_agent import load_last_regime, save_last_regime
from agents.trading_agent_v4 import TradingAgentV4, TradingPhaseV4, PHASE_TOOLS_V4
from agents.trading_agent import build_context
from broker.kis_api import KISApi
from broker.kiwoom_api import KiwoomApi
from broker.telegram import TelegramApproval
from codes.order_manager import OrderManager, OrderRequest
from codes.position_sizer import PositionSizer
from codes.risk_officer import PortfolioState, RiskOfficer, TradeProposal
from codes.trade_journal import TradeJournal
from config.settings import RISK
from market_intelligence import market as mil_market
from market_intelligence import portfolio as mil_portfolio
from market_intelligence import screening as mil_screening
from market_intelligence.base import MILContext, ToolFailure
from market_intelligence.cache import MILCache
from market_intelligence.circuit_breaker import CircuitBreaker

logger = logging.getLogger("mqk_v4")

_DATA_DIR = Path(__file__).parent / "data"
_WATCHLIST_PATH_V4 = _DATA_DIR / "watchlist_v4.json"
_LAST_REGIME_PATH = _DATA_DIR / "last_regime.json"  # v3와 레짐 공유

MAX_POSITIONS_V4 = 3  # v3(4개)보다 적게 — 세력주 집중 투자


class MQKOrchestratorV4:
    """v4 오케스트레이터. v3 Safety Layer 전부 재사용."""

    def __init__(self, kis_api: KISApi, kiwoom_api: KiwoomApi | None = None):
        cache = MILCache()
        breaker = CircuitBreaker()
        self._mil = MILContext(
            kis_api=kis_api,
            kiwoom_api=kiwoom_api,
            cache=cache,
            circuit_breaker=breaker,
        )
        self._kis_api = kis_api
        self._agent = TradingAgentV4()
        self._today = datetime.now().strftime("%Y-%m-%d")

        # v3 Safety Layer 재사용
        self._journal = TradeJournal()
        self._risk = RiskOfficer()
        self._sizer = PositionSizer()
        self._order_mgr = OrderManager(kis_api=kis_api)
        self._telegram = TelegramApproval()

    def _run_agent(self, phase: TradingPhaseV4, context: dict) -> dict:
        return self._agent.run(phase, context)

    def _build_context_v4(
        self,
        phase: TradingPhaseV4,
        regime: dict,
        watchlist: list[dict],
    ) -> dict:
        """v4용 컨텍스트 생성. v3 build_context 재사용."""
        try:
            positions = mil_portfolio.get_open_positions(self._mil, phase.value)
            daily_pnl = mil_portfolio.get_daily_pnl(self._mil, phase.value)
        except ToolFailure:
            positions = {"positions": [], "position_count": 0}
            daily_pnl = {"realized_pnl_pct": 0.0, "realized_pnl_krw": 0.0}

        position_count = positions.get("position_count", 0)
        positions_left = max(MAX_POSITIONS_V4 - position_count, 0)

        risk_guidance = regime.get("risk_guidance", {})
        return build_context(
            phase=phase,  # type: ignore[arg-type]
            trading_date=self._today,
            regime={
                "status": regime.get("status"),
                "regime": regime.get("regime"),
                "confidence": regime.get("confidence"),
            },
            drift_status="STABLE",
            risk_guidance=risk_guidance,
            portfolio_snapshot=positions,
            daily_pnl=daily_pnl,
            risk_budget_remaining={
                "positions_left": positions_left,
                "monitoring_slots": min(6, max(positions_left, 4)),
                "daily_loss_remaining_pct": RISK.max_daily_loss_pct,
            },
            watchlist=watchlist,
            allowed_tools=list(PHASE_TOOLS_V4[phase]),
        )

    def _save_watchlist_v4(self, candidates: list[dict]) -> None:
        _WATCHLIST_PATH_V4.parent.mkdir(parents=True, exist_ok=True)
        with open(_WATCHLIST_PATH_V4, "w", encoding="utf-8") as f:
            json.dump(candidates, f, ensure_ascii=False, indent=2)

    def _load_watchlist_v4(self) -> list[dict]:
        if not _WATCHLIST_PATH_V4.exists():
            return []
        try:
            with open(_WATCHLIST_PATH_V4, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

    # ── 08:45 장전 상한가 세력 검증 ────────────────────────────────────────
    def run_premarket_sejuk_v4(self) -> dict:
        regime = load_last_regime(path=_LAST_REGIME_PATH) or {}

        try:
            limit_up = mil_screening.get_limit_up_stocks(
                self._mil, TradingPhaseV4.PREMARKET_SEJUK.value
            )
        except ToolFailure:
            limit_up = {"stocks": []}

        try:
            premarket = mil_screening.get_premarket_movers(
                self._mil, TradingPhaseV4.PREMARKET_SEJUK.value
            )
        except ToolFailure:
            premarket = {"movers": []}

        context = self._build_context_v4(
            TradingPhaseV4.PREMARKET_SEJUK, regime, watchlist=[]
        )
        context["limit_up_stocks"] = limit_up.get("stocks", [])
        context["premarket_movers"] = premarket.get("movers", [])

        result = self._run_agent(TradingPhaseV4.PREMARKET_SEJUK, context)

        candidates = result.get("candidates", [])
        if candidates:
            self._save_watchlist_v4(candidates)
            logger.info(
                f"[v4 PREMARKET_SEJUK] 진입 후보 {len(candidates)}개 → watchlist_v4.json"
            )
        else:
            logger.info("[v4 PREMARKET_SEJUK] 통과 후보 없음")

        return result

    # ── 09:03 레짐 판단 ────────────────────────────────────────────────────
    def run_premarket_v4(self) -> dict:
        """v3 RegimeAgent.judge()를 재사용해 레짐을 판단한다."""
        from agents.regime_agent import RegimeAgent
        agent = RegimeAgent()

        # MIL을 통해 시장 컨텍스트 수집
        try:
            market_ctx = mil_market.get_market_context(
                self._mil, TradingPhaseV4.PREMARKET.value
            )
        except ToolFailure:
            market_ctx = {}

        judgment = agent.judge(market_ctx, evaluation_mode="OPENING")
        save_last_regime(judgment, path=_LAST_REGIME_PATH)

        from dataclasses import asdict
        regime_dict = asdict(judgment)
        regime_dict["status"] = judgment.status.value
        regime_dict["regime"] = judgment.regime.value
        regime_dict["opportunity_mode"] = judgment.opportunity_mode.value
        regime_dict["scanner_mode"] = judgment.scanner_mode.value
        logger.info(f"[v4 PREMARKET] {regime_dict.get('regime')} ({regime_dict.get('status')})")
        return regime_dict

    # ── 09:17/11:17/13:17/15:00 스캔 ──────────────────────────────────────
    def run_scan_v4(self) -> dict:
        regime = load_last_regime(path=_LAST_REGIME_PATH) or {}
        watchlist = self._load_watchlist_v4()

        try:
            volume_surge = mil_screening.get_volume_surge(
                self._mil, TradingPhaseV4.SCAN.value
            )
        except ToolFailure:
            volume_surge = {}

        context = self._build_context_v4(TradingPhaseV4.SCAN, regime, watchlist=watchlist)
        context["volume_surge_candidates"] = volume_surge.get("surge_top", [])

        result = self._run_agent(TradingPhaseV4.SCAN, context)

        new_candidates = result.get("candidates", [])
        if new_candidates:
            existing = {e["ticker"]: e for e in watchlist if isinstance(e, dict) and "ticker" in e}
            for c in new_candidates:
                if isinstance(c, dict) and "ticker" in c:
                    existing[c["ticker"]] = c
            self._save_watchlist_v4(list(existing.values()))
            logger.info(
                f"[v4 SCAN] watchlist={[c['ticker'] for c in existing.values()]}"
            )
        return result

    # ── 09:20~14:50 장중 ───────────────────────────────────────────────────
    def run_intraday_v4(self) -> dict:
        regime = load_last_regime(path=_LAST_REGIME_PATH)
        if regime is None or str(regime.get("timestamp", ""))[:10] != self._today:
            logger.warning("[v4 INTRADAY] 당일 레짐 없음 — 스킵")
            return {"action": "NO_TRADE", "reason": "stale_regime"}

        watchlist = self._load_watchlist_v4()
        context = self._build_context_v4(TradingPhaseV4.INTRADAY, regime, watchlist=watchlist)
        result = self._run_agent(TradingPhaseV4.INTRADAY, context)

        self._handle_proposals_v4(result.get("proposals", []))
        logger.info(
            f"[v4 INTRADAY] action={result.get('action')} "
            f"reason={result.get('reason', '')[:80]}"
        )
        return result

    def _handle_proposals_v4(self, proposals: list[dict]) -> None:
        """v4 proposal 처리. BUY/SELL을 분리해 OrderManager로 전달."""
        buy_proposals = [p for p in proposals if str(p.get("side", "")).upper() == "BUY"]
        sell_proposals = [p for p in proposals if str(p.get("side", "")).upper() == "SELL"]

        for p in buy_proposals:
            try:
                ticker = p["ticker"]
                req = OrderRequest(
                    ticker=ticker,
                    name=ticker,
                    side="BUY",
                    quantity=1,  # 실전 전환 시 PositionSizer 연동 필요
                    price=0,     # 시장가
                    stop_loss_price=float(p.get("stop_loss_price") or 0),
                    reason=p.get("reason", ""),
                    confidence=int(p.get("confidence") or 70),
                    strategy_type=p.get("setup", "VOLUME_SURGE_LEADER"),
                )
                self._order_mgr.execute_buy(req)
            except Exception as e:
                logger.warning(f"[v4 BUY] {p.get('ticker')} 실패: {e}")

        for p in sell_proposals:
            try:
                ticker = p["ticker"]
                open_pos = self._journal.get_open_positions()
                match = next((pos for pos in open_pos if pos["ticker"] == ticker), None)
                qty = int(match["quantity"]) if match else 0
                if qty <= 0:
                    logger.info(f"[v4 SELL] {ticker} 보유하지 않음 — 스킵")
                    continue
                req = OrderRequest(
                    ticker=ticker,
                    name=match.get("name", ticker) if match else ticker,
                    side="SELL",
                    quantity=qty,
                    price=0,  # 시장가
                    stop_loss_price=0.0,
                    reason=p.get("reason", ""),
                    confidence=int(p.get("confidence") or 100),
                )
                self._order_mgr.execute_sell(req)
                logger.info(f"[v4 SELL] {ticker} sell_type={p.get('sell_type')}")
            except Exception as e:
                logger.warning(f"[v4 SELL] {p.get('ticker')} 실패: {e}")

    # ── 15:18 마감 청산 ────────────────────────────────────────────────────
    def run_close_v4(self) -> dict:
        regime = load_last_regime(path=_LAST_REGIME_PATH) or {}
        context = self._build_context_v4(TradingPhaseV4.CLOSE, regime, watchlist=[])
        result = self._run_agent(TradingPhaseV4.CLOSE, context)

        for p in result.get("sell_proposals", []):
            try:
                ticker = p["ticker"]
                open_pos = self._journal.get_open_positions()
                match = next((pos for pos in open_pos if pos["ticker"] == ticker), None)
                qty = int(match["quantity"]) if match else 0
                if qty <= 0:
                    logger.info(f"[v4 CLOSE SELL] {ticker} 보유하지 않음 — 스킵")
                    continue
                req = OrderRequest(
                    ticker=ticker,
                    name=match.get("name", ticker) if match else ticker,
                    side="SELL",
                    quantity=qty,
                    price=0,
                    stop_loss_price=0.0,
                    reason=p.get("reason", "close_v4"),
                    confidence=100,
                )
                self._order_mgr.execute_sell(req)
            except Exception as e:
                logger.warning(f"[v4 CLOSE SELL] {p.get('ticker')}: {e}")

        logger.info(f"[v4 CLOSE] sell_proposals={len(result.get('sell_proposals', []))}")
        return result

    # ── 17:00 복기 ─────────────────────────────────────────────────────────
    def run_market_close_v4(self) -> dict:
        regime = load_last_regime(path=_LAST_REGIME_PATH) or {}
        context = self._build_context_v4(TradingPhaseV4.MARKET_CLOSE, regime, watchlist=[])
        result = self._run_agent(TradingPhaseV4.MARKET_CLOSE, context)
        logger.info("[v4 MARKET_CLOSE] 복기 완료")
        return result
