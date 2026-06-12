"""MQK v3 오케스트레이터 - 단일 TradingAgent + MIL + v2 Safety Layer.

v2의 RED hard block을 제거한다. RegimeAgent가 매일 아침 risk_guidance/drift_triggers를
선언하면, RegimeDriftDetector가 장중 5분마다 무료로 감시한다(Tier2). 드리프트가 발동하면
Lite LLM(Tier3)을 호출해 risk_guidance를 조정하거나 레짐을 전환한다. TradingAgent는
Phase별로 MIL 16개 도구를 사용해 proposal을 생성하고, v2 Safety Layer
(RiskOfficer/PositionSizer/Telegram/OrderManager)가 이를 코드로 강제한다.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path

from agents.drift_detector import RegimeDriftDetector
from agents.regime_agent import load_last_regime, save_last_regime, _LAST_REGIME_PATH
from agents.trading_agent import TradingAgent, TradingPhase, build_context
from broker.kis_mcp_client import KISMCPClient
from broker.telegram import ApprovalRequest
from codes.order_manager import OrderRequest
from codes.risk_officer import RiskViolation, TradeProposal
from config.settings import RISK
from market_intelligence import market as mil_market
from market_intelligence import portfolio as mil_portfolio
from market_intelligence.base import MILContext, ToolFailure
from market_intelligence.cache import MILCache
from market_intelligence.circuit_breaker import CircuitBreaker
from orchestrator import MQKOrchestrator

logger = logging.getLogger("mqk_v3")

_DATA_DIR = Path(__file__).parent / "data"
_DRIFT_STATE_PATH = _DATA_DIR / "drift_state.json"
_WATCHLIST_PATH = _DATA_DIR / "watchlist.json"


def _default_drift_state(date: str) -> dict:
    return {"date": date, "last_trigger_time": {}, "today_caution_count": 0, "daily_lite_llm_calls": 0}


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(content, encoding="utf-8")
    os.replace(tmp_path, path)


def load_drift_state(path: Path = _DRIFT_STATE_PATH, today: str | None = None) -> dict:
    today = today or datetime.now().strftime("%Y-%m-%d")
    if not path.exists():
        return _default_drift_state(today)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"[orchestrator_v3] drift_state.json 손상 — 기본값 반환: {e}")
        return _default_drift_state(today)
    if state.get("date") != today:
        return _default_drift_state(today)
    return state


def save_drift_state(state: dict, path: Path = _DRIFT_STATE_PATH) -> None:
    _atomic_write_text(path, json.dumps(state, ensure_ascii=False, indent=2))


def load_watchlist(path: Path = _WATCHLIST_PATH) -> list[str]:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("watchlist", [])
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"[orchestrator_v3] watchlist.json 손상 — 빈 목록 반환: {e}")
        return []


def save_watchlist(watchlist: list[str], path: Path = _WATCHLIST_PATH) -> None:
    _atomic_write_text(
        path,
        json.dumps({"watchlist": watchlist, "updated_at": datetime.now().isoformat()}, ensure_ascii=False, indent=2),
    )


class MQKOrchestratorV3(MQKOrchestrator):
    """v2 Safety Layer를 재사용하는 v3 아젠틱 오케스트레이터."""

    def __init__(self, kis_api=None, mil: MILContext | None = None, dry_run_orders: bool | None = None):
        super().__init__(kis_api=kis_api, dry_run_orders=dry_run_orders)
        self._mil = mil or MILContext(
            kis_api=kis_api,
            mcp_client=KISMCPClient(),
            cache=MILCache(),
            circuit_breaker=CircuitBreaker(),
        )
        self._drift_detector = RegimeDriftDetector()
        self._trading_agent = TradingAgent(mil=self._mil)

    # ── 08:45 PREMARKET ──────────────────────────────────────────────────────
    def run_premarket_v3(self) -> dict:
        market_status = self.run_premarket()  # v2 RegimeAgent.judge() 재사용
        regime = self._last_regime
        save_last_regime(regime, path=_LAST_REGIME_PATH)
        save_drift_state(_default_drift_state(self._today), path=_DRIFT_STATE_PATH)
        self._mil.circuit_breaker.reset()

        regime_dict = _regime_to_dict(regime)
        context = self._build_context(TradingPhase.PREMARKET, regime_dict, "STABLE", watchlist=[])
        review = self._trading_agent.run(TradingPhase.PREMARKET, context)
        self._save_json("premarket_review.json", review)
        return market_status

    # ── 09:10 / 11:00 / 14:00 SCAN ────────────────────────────────────────────
    def run_scan_v3(self) -> dict:
        regime = load_last_regime(path=_LAST_REGIME_PATH) or {}
        drift_state = load_drift_state(path=_DRIFT_STATE_PATH, today=self._today)
        context = self._build_context(TradingPhase.SCAN, regime, _drift_status(drift_state), watchlist=[])
        result = self._trading_agent.run(TradingPhase.SCAN, context)
        save_watchlist(result.get("watchlist", []), path=_WATCHLIST_PATH)
        self._save_json("scan_v3.json", result)
        return result

    # ── */5 09:20~15:00 INTRADAY (드리프트 체크 + 매수/청산 판단) ──────────────
    def run_intraday_v3(self) -> dict:
        regime = load_last_regime(path=_LAST_REGIME_PATH)
        if regime is None:
            logger.warning("[INTRADAY] last_regime.json 없음 — premarket을 먼저 실행하세요.")
            return {"action": "NO_TRADE", "reason": "no_regime"}

        drift_state = load_drift_state(path=_DRIFT_STATE_PATH, today=self._today)
        snapshot = self._collect_drift_snapshot()

        risk_guidance = dict(regime.get("risk_guidance", {}))
        if snapshot is None:
            logger.warning("[INTRADAY] 드리프트 스냅샷 수집 실패 — 드리프트 체크 스킵, 기존 risk_guidance 유지")
            drift_judgment = "STABLE"
        else:
            drift_result = self._drift_detector.check(
                market_snapshot=snapshot,
                drift_triggers=regime.get("drift_triggers", []),
                cooldown_minutes=regime.get("cooldown_minutes", 60),
                max_daily_triggers=regime.get("max_daily_triggers", 3),
                drift_state=drift_state,
                current_status=regime.get("status", "YELLOW"),
                current_regime=regime,
            )
            save_drift_state(drift_result["drift_state"], path=_DRIFT_STATE_PATH)

            drift_judgment = drift_result["drift_judgment"]
            if drift_judgment in {"CAUTION", "REGIME_SHIFT"}:
                risk_guidance.update(drift_result.get("risk_guidance_delta", {}))
                self._notify_drift(drift_result)

            if drift_judgment == "REGIME_SHIFT":
                regime["status"] = drift_result["new_status"]
                regime["risk_guidance"] = risk_guidance
                save_last_regime_dict(regime, path=_LAST_REGIME_PATH)
                self.run_scan_v3()

        watchlist = load_watchlist(path=_WATCHLIST_PATH)
        context = self._build_context(
            TradingPhase.INTRADAY, regime, drift_judgment,
            watchlist=watchlist, risk_guidance_override=risk_guidance,
        )
        result = self._trading_agent.run(TradingPhase.INTRADAY, context)
        self._handle_proposals(result.get("proposals", []))
        self._save_json(f"intraday_v3_{datetime.now().strftime('%H%M%S')}.json", result)
        return result

    # ── 15:30 CLOSE ────────────────────────────────────────────────────────────
    def run_close_v3(self) -> dict:
        regime = load_last_regime(path=_LAST_REGIME_PATH) or {}
        drift_state = load_drift_state(path=_DRIFT_STATE_PATH, today=self._today)
        context = self._build_context(TradingPhase.CLOSE, regime, _drift_status(drift_state), watchlist=[])
        result = self._trading_agent.run(TradingPhase.CLOSE, context)
        self._handle_sell_proposals(result.get("sell_proposals", []))
        self._save_json("close_v3.json", result)
        self.run_close_review()  # v2 거래 복기 재사용
        return result

    # ── 17:00 MARKET_CLOSE ───────────────────────────────────────────────────
    def run_market_close_v3(self) -> dict:
        regime = load_last_regime(path=_LAST_REGIME_PATH) or {}
        context = self._build_context(TradingPhase.MARKET_CLOSE, regime, "STABLE", watchlist=[])
        result = self._trading_agent.run(TradingPhase.MARKET_CLOSE, context)
        self._save_json("market_close_snapshot.json", result.get("market_close_snapshot", {}))
        self._save_json("close_market_read.json", result.get("close_market_read", {}))
        self._save_json("next_day_premarket_context.json", result.get("next_day_premarket_context", {}))
        return result

    # ── 컨텍스트/스냅샷 빌더 ───────────────────────────────────────────────────

    def _build_context(
        self,
        phase: TradingPhase,
        regime: dict,
        drift_status: str,
        watchlist: list[str],
        risk_guidance_override: dict | None = None,
    ) -> dict:
        risk_guidance = risk_guidance_override or regime.get("risk_guidance", {})

        # 잔고 조회는 일시적 KIS 500 등으로 실패할 수 있다 (D1 라이브 테스트에서
        # 발생). phase 전체가 죽는 대신 보수적으로 강등한다: 포트폴리오 미상 →
        # positions_left=0, 손실예산 0 — LLM이 신규 매수를 제안하지 않게 된다.
        try:
            positions = mil_portfolio.get_open_positions(self._mil, phase.value)
            daily_pnl = mil_portfolio.get_daily_pnl(self._mil, phase.value)
            portfolio_unavailable = False
        except ToolFailure as e:
            logger.warning(f"[V3 CONTEXT] 포트폴리오 조회 실패 — 보수적 강등(매수 예산 0): {e}")
            positions = {"positions": [], "position_count": 0, "data_unavailable": True}
            daily_pnl = {"realized_pnl_pct": 0.0, "realized_pnl_krw": 0.0,
                         "total_eval_amt": 0.0, "data_unavailable": True}
            portfolio_unavailable = True

        max_positions = risk_guidance.get("max_positions", RISK.max_positions)
        positions_left = max(max_positions - positions.get("position_count", 0), 0)
        realized_loss_pct = abs(min(daily_pnl.get("realized_pnl_pct", 0.0), 0.0))
        daily_loss_remaining = max(RISK.max_daily_loss_pct - realized_loss_pct, 0.0)
        if portfolio_unavailable:
            positions_left = 0
            daily_loss_remaining = 0.0

        return build_context(
            phase=phase,
            trading_date=self._today,
            regime={
                "status": regime.get("status"),
                "regime": regime.get("regime"),
                "confidence": regime.get("confidence"),
            },
            drift_status=drift_status,
            risk_guidance=risk_guidance,
            portfolio_snapshot=positions,
            daily_pnl=daily_pnl,
            risk_budget_remaining={
                "positions_left": positions_left,
                "daily_loss_remaining_pct": daily_loss_remaining,
            },
            watchlist=watchlist,
            context_timestamps={
                "regime": regime.get("timestamp", ""),
                "now": datetime.now().isoformat(),
            },
        )

    def _collect_drift_snapshot(self) -> dict | None:
        """드리프트 스냅샷을 수집한다. 실패/이상 데이터 시 None을 반환한다 (호출부에서 STABLE로 강등).

        ToolFailure(circuit-breaker open, KIS API 오류 등)와 예기치 못한 예외를
        여기서 흡수하여 5분 intraday tick이 죽지 않도록 한다.
        """
        try:
            market_ctx = mil_market.get_market_context(self._mil, "INTRADAY")
            candles = mil_market.get_intraday_index_candles(self._mil, "INTRADAY").get("candles", [])
            breadth = mil_market.get_sector_breadth(self._mil, "INTRADAY").get("market_breadth", {})

            kospi_current = market_ctx.get("kospi", 0.0)
            kospi_open = candles[0]["open"] if candles else kospi_current
            lows = [c["low"] for c in candles if c.get("low")]
            kospi_low = min(lows) if lows else kospi_current

            for value in (kospi_current, kospi_open, kospi_low):
                if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
                    logger.warning(f"[V3 DRIFT] 드리프트 스냅샷의 kospi 값이 비정상: {value!r} — 드리프트 체크 스킵")
                    return None

            return {
                "kospi_current": kospi_current,
                "kospi_open": kospi_open,
                "kospi_low": kospi_low,
                "foreign_net_buy_bln": market_ctx.get("foreign_net_buy_krw", 0.0) / 1e8,
                "advance_count": breadth.get("advancers", 0),
                "decline_count": breadth.get("decliners", 0),
            }
        except ToolFailure as e:
            logger.warning(f"[V3 DRIFT] 드리프트 스냅샷 수집 실패(ToolFailure) — 드리프트 체크 스킵: {e}")
            return None
        except Exception as e:
            logger.warning(f"[V3 DRIFT] 드리프트 스냅샷 수집 중 예기치 못한 오류 — 드리프트 체크 스킵: {e}")
            return None

    def _notify_drift(self, drift_result: dict) -> None:
        lines = [
            f"⚠️ *드리프트 감지: {drift_result['drift_judgment']}*",
            f"사유: {drift_result.get('reason', '')}",
        ]
        if drift_result.get("new_status"):
            lines.append(f"새 상태: {drift_result['new_status']}")
        delta = drift_result.get("risk_guidance_delta", {})
        if delta:
            lines.append(f"risk_guidance 조정: {json.dumps(delta, ensure_ascii=False)}")
        try:
            self._telegram.notify("\n".join(lines))
        except Exception as e:
            logger.warning(f"[드리프트 알림] 텔레그램 발송 실패: {e}")

    # ── proposal → Safety Layer ─────────────────────────────────────────────

    def _handle_proposals(self, proposals: list[dict]) -> list[dict]:
        results = []
        for p in proposals:
            try:
                if not isinstance(p, dict):
                    raise TypeError(f"proposal이 dict가 아님: {type(p).__name__}")
                if p.get("side") == "BUY":
                    results.append(self._process_v3_buy_proposal(p))
                elif p.get("side") == "SELL":
                    results.append(self._process_v3_sell_proposal(p))
                else:
                    results.append({"action": "SKIP", "reason": "unknown_side", "proposal": _safe_summary(p)})
            except (KeyError, TypeError, ValueError, AttributeError) as e:
                logger.warning(f"[V3 PROPOSAL] 잘못된 proposal 무시: {e} | proposal={_safe_summary(p)}")
                results.append({"action": "SKIP", "reason": "malformed_proposal", "proposal": _safe_summary(p)})
        return results

    def _handle_sell_proposals(self, proposals: list[dict]) -> list[dict]:
        results = []
        for p in proposals:
            try:
                if not isinstance(p, dict):
                    raise TypeError(f"proposal이 dict가 아님: {type(p).__name__}")
                results.append(self._process_v3_sell_proposal(p))
            except (KeyError, TypeError, ValueError, AttributeError) as e:
                logger.warning(f"[V3 SELL PROPOSAL] 잘못된 proposal 무시: {e} | proposal={_safe_summary(p)}")
                results.append({"action": "SKIP", "reason": "malformed_proposal", "proposal": _safe_summary(p)})
        return results

    def _kis_buyable_cash_safe(self, ticker: str, price: float) -> dict | None:
        """매수가능현금 조회 (실패/미구성 시 None - 가드 스킵, fail-open)."""
        kis_api = getattr(self, "_kis_api", None)
        if kis_api is None or not hasattr(kis_api, "get_buyable_cash"):
            return None
        try:
            return kis_api.get_buyable_cash(ticker=ticker, price=price)
        except Exception as exc:
            logger.warning(f"[V3 CASH GUARD] {ticker}: 매수가능금액 조회 실패 - {exc}")
            return None

    def _process_v3_buy_proposal(self, proposal: dict) -> dict:
        ticker = str(proposal["ticker"])
        stop_loss_price = float(proposal["stop_loss_price"])
        snapshot = self._market_data.get_snapshot(ticker)
        entry_price = snapshot.current_price
        atr = self._estimate_atr(ticker)
        portfolio_state = self.build_portfolio_state()

        sizing = self._position_sizer.calculate_flexible_stop(
            ticker=ticker,
            entry_price=entry_price,
            atr=atr,
            total_capital=getattr(portfolio_state, "total_capital", 0),
            support_stop_price=stop_loss_price,
        )

        buyable = self._kis_buyable_cash_safe(ticker, entry_price)
        if buyable is not None:
            order_value = entry_price * sizing.quantity
            if order_value > buyable["buyable_cash_krw"]:
                logger.warning(
                    f"[V3 CASH GUARD] {ticker}: 주문금액({order_value:,.0f})이 "
                    f"매수가능금액({buyable['buyable_cash_krw']:,.0f})을 초과 — 주문 차단"
                )
                return {"action": "BLOCKED", "ticker": ticker, "reason": "insufficient_cash"}
        else:
            logger.warning(f"[V3 CASH GUARD] {ticker}: 매수가능금액 확인 불가 — 가드 스킵 후 진행")

        trade_proposal = TradeProposal(
            ticker=ticker,
            theme="V3",
            entry_price=entry_price,
            stop_loss_price=sizing.stop_loss_price,
            quantity=sizing.quantity,
        )

        try:
            self._risk_officer.check(trade_proposal, portfolio_state)
        except RiskViolation as e:
            logger.warning(f"[V3 RISK BLOCK] {ticker}: {e}")
            return {"action": "BLOCKED", "ticker": ticker, "reason": str(e)}

        approval_request_id = None
        if RISK.require_telegram_approval:
            approval_req = ApprovalRequest(
                ticker=ticker, name=ticker, decision="BUY",
                entry_price=entry_price,
                stop_loss_price=sizing.stop_loss_price,
                quantity=sizing.quantity,
                risk_pct=sizing.risk_pct,
                confidence=proposal.get("confidence", 0),
                reason=proposal.get("reason", ""),
                counter_argument="",
            )
            approval = self._telegram.request_approval(approval_req)
            approval_request_id = approval.request_id
            if not approval.approved:
                return {"action": "REJECTED", "ticker": ticker, "reason": "텔레그램 거부"}

        order = OrderRequest(
            ticker=ticker, name=ticker, side="BUY",
            quantity=sizing.quantity,
            price=entry_price,
            stop_loss_price=sizing.stop_loss_price,
            reason=proposal.get("reason", ""),
            confidence=proposal.get("confidence", 0),
            approval_request_id=approval_request_id,
            strategy_type=proposal.get("setup", "TREND"),
        )
        result = self._order_manager.execute_buy(order)
        return {"action": "BUY_EXECUTED", "ticker": ticker, "success": result.success}

    def _process_v3_sell_proposal(self, proposal: dict) -> dict:
        ticker = proposal["ticker"]
        open_pos = self._journal.get_open_positions()
        match = next((p for p in open_pos if p["ticker"] == ticker), None)
        if match is None:
            return {"action": "SKIP", "ticker": ticker, "reason": "보유하지 않은 종목"}

        snapshot = self._market_data.get_snapshot(ticker)
        order = OrderRequest(
            ticker=ticker, name=match.get("name", ticker), side="SELL",
            quantity=int(match["quantity"]),
            price=snapshot.current_price,
            stop_loss_price=float(match["stop_loss_price"]),
            reason=proposal.get("reason", ""),
            confidence=100,
        )
        result = self._order_manager.execute_sell(order)
        return {"action": "SELL_EXECUTED", "ticker": ticker, "success": result.success}


def _regime_to_dict(regime) -> dict:
    return {
        "status": regime.status.value,
        "regime": regime.regime.value,
        "confidence": regime.confidence,
        "risk_guidance": regime.risk_guidance,
        "drift_triggers": regime.drift_triggers,
        "cooldown_minutes": regime.cooldown_minutes,
        "max_daily_triggers": regime.max_daily_triggers,
    }


def save_last_regime_dict(regime: dict, path: Path = _LAST_REGIME_PATH) -> None:
    """REGIME_SHIFT 후 갱신된 레짐 dict를 last_regime.json에 다시 저장한다."""
    payload = dict(regime)
    payload["timestamp"] = datetime.now().isoformat()
    _atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))


def _drift_status(drift_state: dict) -> str:
    if drift_state.get("today_caution_count", 0) > 0:
        return "CAUTION"
    return "STABLE"


def _safe_summary(p) -> str:
    """malformed proposal을 로그/결과에 안전하게 담기 위한 repr 요약."""
    try:
        return repr(p)[:500]
    except Exception:
        return "<unrepresentable proposal>"
