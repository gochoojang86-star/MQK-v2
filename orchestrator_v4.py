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
from codes.risk_officer import RiskOfficer
from codes.trade_journal import TradeJournal
from config.settings import EXECUTION, RISK
from market_intelligence import market as mil_market
from market_intelligence import portfolio as mil_portfolio
from market_intelligence import screening as mil_screening
from market_intelligence.base import MILContext, ToolFailure
from market_intelligence.cache import MILCache
from market_intelligence.circuit_breaker import CircuitBreaker

logger = logging.getLogger("mqk_v4")

_DATA_DIR = Path(__file__).parent / "data"
_WATCHLIST_PATH_V4 = _DATA_DIR / "watchlist_v4.json"
_LAST_REGIME_PATH_V4 = _DATA_DIR / "last_regime_v4.json"
_NEXT_DAY_PRIOR_PATH_V4 = _DATA_DIR / "next_day_prior_v4.json"

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
        self._order_mgr = OrderManager(
            kis_api=kis_api,
            journal=self._journal,
            dry_run=EXECUTION.order_dry_run,
        )
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

        # 저널 포지션 병합: entry_date·setup 포함 (KIS API에는 없음).
        # close/intraday의 "3일차 청산" 등 시간 기반 판단에 필요.
        try:
            journal_positions = self._journal.get_open_positions()
        except Exception:
            journal_positions = []
        
        if journal_positions and isinstance(positions, dict) and "positions" in positions:
            journal_map = {j["ticker"]: j for j in journal_positions if isinstance(j, dict) and "ticker" in j}
            # in-place 변경 방지: MILCache가 캐시한 dict를 오염시키지 않도록 얕은 복사
            positions = {
                **positions,
                "positions": [dict(p) for p in positions["positions"]],
            }
            for p_item in positions["positions"]:
                if isinstance(p_item, dict) and "ticker" in p_item:
                    tk = p_item["ticker"]
                    if tk in journal_map:
                        p_item["entry_date"] = journal_map[tk].get("entry_date")
                        p_item["setup"] = journal_map[tk].get("strategy_type") or journal_map[tk].get("setup")
                        p_item["entry_price"] = journal_map[tk].get("entry_price")

        # 전날 market_close가 남긴 prior 로드 (다음날 전략 연속성).
        next_day_prior = self._load_next_day_prior()

        risk_guidance = regime.get("risk_guidance", {})
        ctx = build_context(
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
        ctx["next_day_prior"] = next_day_prior  # 빈 dict여도 항상 주입 (scan.md step 0)
        return ctx

    def _load_next_day_prior(self) -> dict:
        if not _NEXT_DAY_PRIOR_PATH_V4.exists():
            return {}
        try:
            import json as _json
            with open(_NEXT_DAY_PRIOR_PATH_V4, encoding="utf-8") as f:
                payload = _json.load(f)
            if not isinstance(payload, dict):
                return {}
            prior_date = str(payload.get("prior_date", ""))
            if prior_date != self._today:
                logger.info(
                    f"[v4 PRIOR] stale next_day_prior 무시: prior_date={prior_date!r}, today={self._today}"
                )
                return {}
            return payload
        except Exception:
            return {}

    def _save_next_day_prior(self, payload: dict) -> None:
        if not payload:
            return
        import json as _json
        _NEXT_DAY_PRIOR_PATH_V4.parent.mkdir(parents=True, exist_ok=True)
        to_save = dict(payload)
        to_save["prior_date"] = self._today
        with open(_NEXT_DAY_PRIOR_PATH_V4, "w", encoding="utf-8") as f:
            _json.dump(to_save, f, ensure_ascii=False, indent=2)

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

    def _resolve_evaluation_mode(self) -> str:
        hour = datetime.now().hour
        if hour < 10:
            return "OPENING"
        elif hour < 12:
            return "MIDDAY"
        else:
            return "AFTERNOON"

    # ── 08:45 장전 상한가 세력 검증 ────────────────────────────────────────
    def run_premarket_sejuk_v4(self) -> dict:
        regime = load_last_regime(path=_LAST_REGIME_PATH_V4) or {}

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
        """레짐 판단은 TradingAgentV4를 거치지 않고 RegimeAgent를 직접 호출한다.
        v3와 동일한 구조. prompts/agents/trading_agent_v4/premarket.md 는 존재하지 않는다."""
        from agents.regime_agent import RegimeAgent
        agent = RegimeAgent()

        # MIL을 통해 시장 컨텍스트 수집
        try:
            market_ctx = mil_market.get_market_context(
                self._mil, TradingPhaseV4.PREMARKET.value
            )
        except ToolFailure:
            market_ctx = {}

        judgment = agent.judge(market_ctx, evaluation_mode=self._resolve_evaluation_mode())
        save_last_regime(judgment, path=_LAST_REGIME_PATH_V4)

        from dataclasses import asdict
        regime_dict = asdict(judgment)
        regime_dict["status"] = judgment.status.value
        regime_dict["regime"] = judgment.regime.value
        regime_dict["opportunity_mode"] = judgment.opportunity_mode.value
        regime_dict["scanner_mode"] = judgment.scanner_mode.value
        regime_dict["timestamp"] = datetime.now().isoformat()
        logger.info(f"[v4 PREMARKET] {regime_dict.get('regime')} ({regime_dict.get('status')})")
        return regime_dict

    # ── 09:17/11:17/13:17/15:00 스캔 ──────────────────────────────────────
    def run_scan_v4(self) -> dict:
        regime = load_last_regime(path=_LAST_REGIME_PATH_V4) or {}
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
        logger.info(f"[v4 SCAN reason] {result.get('reason', '')[:300]}")

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
        regime = load_last_regime(path=_LAST_REGIME_PATH_V4)
        if regime is None or str(regime.get("timestamp", ""))[:10] != self._today:
            logger.warning("[v4 INTRADAY] 당일 레짐 없음 — 스킵")
            return {"action": "NO_TRADE", "reason": "stale_regime"}

        watchlist = self._load_watchlist_v4()
        context = self._build_context_v4(TradingPhaseV4.INTRADAY, regime, watchlist=watchlist)
        result = self._run_agent(TradingPhaseV4.INTRADAY, context)

        # action=HOLD/NO_TRADE인데 BUY proposal이 있으면 제거 (v3 _sanitize_intraday_result 동등)
        action = str(result.get("action") or "NO_TRADE").upper()
        proposals = result.get("proposals") or []
        if action in {"HOLD", "NO_TRADE"}:
            buy_count = sum(1 for p in proposals if str(p.get("side", "")).upper() == "BUY")
            if buy_count:
                logger.warning(
                    f"[v4 INTRADAY] action={action}인데 BUY proposal {buy_count}건 — 안전상 제거"
                )
                proposals = [p for p in proposals if str(p.get("side", "")).upper() != "BUY"]

        self._handle_proposals_v4(proposals)
        logger.info(
            f"[v4 INTRADAY] action={action} reason={result.get('reason', '')[:80]}"
        )
        return result

    def _get_current_price(self, ticker: str) -> float:
        try:
            snapshot = self._kis_api.get_snapshot(ticker)
            if not isinstance(snapshot, dict):
                return 0.0
            for key in ["stck_prpr", "prpr", "now_pric", "current_price"]:
                val = snapshot.get(key)
                if val is not None:
                    return abs(float(str(val).replace(",", "")))
        except Exception as e:
            logger.warning(f"[v4 SIZER] {ticker} 현재가 조회 실패: {e}")
        return 0.0

    def _get_total_capital(self) -> float:
        try:
            balance = self._kis_api.get_balance()
            if not isinstance(balance, dict):
                return 0.0
            summary = balance.get("output2") or balance.get("summary") or {}
            if isinstance(summary, list):
                summary = summary[0] if summary else {}
            
            for key in ["tot_evlu_amt", "nass_amt", "tot_asst_amt", "total_capital", "dnca_tot_amt"]:
                val = summary.get(key)
                if val is not None:
                    try:
                        return float(str(val).replace(",", ""))
                    except ValueError:
                        continue
        except Exception as e:
            logger.warning(f"[v4 SIZER] 총 자본금 조회 실패: {e}")
        return 0.0

    def _handle_proposals_v4(self, proposals: list[dict]) -> None:
        """v4 proposal 처리. BUY/SELL을 분리해 OrderManager로 전달."""
        buy_proposals = [p for p in proposals if str(p.get("side", "")).upper() == "BUY"]
        sell_proposals = [p for p in proposals if str(p.get("side", "")).upper() == "SELL"]

        for p in buy_proposals:
            try:
                ticker = p["ticker"]
                stop_loss_price = float(p.get("stop_loss_price") or 0)

                # stop_loss 없는 BUY는 프롬프트 Forbidden 위반 — 코드 게이트로 차단
                if stop_loss_price <= 0:
                    logger.warning(
                        f"[v4 BUY] {ticker} stop_loss_price 없음 — 주문 스킵 (Forbidden 위반)"
                    )
                    continue

                entry_price = self._get_current_price(ticker)
                quantity = 1  # 기본값

                if entry_price > 0 and stop_loss_price < entry_price:
                    total_capital = self._get_total_capital()
                    if total_capital > 0:
                        try:
                            sizing = self._sizer.calculate_from_fixed_stop(
                                ticker=ticker,
                                entry_price=entry_price,
                                stop_loss_price=stop_loss_price,
                                total_capital=total_capital,
                            )
                            quantity = sizing.quantity
                            logger.info(
                                f"[v4 SIZER] {ticker} 사이징: 자본={total_capital:,.0f} "
                                f"진입={entry_price:,.0f} 손절={stop_loss_price:,.0f} 수량={quantity}"
                            )
                        except Exception as e:
                            logger.warning(f"[v4 SIZER] {ticker} 계산 실패(기본 1주): {e}")
                else:
                    logger.warning(
                        f"[v4 SIZER] {ticker} 현재가 조회 실패 — 기본 1주: "
                        f"진입={entry_price}, 손절={stop_loss_price}"
                    )

                req = OrderRequest(
                    ticker=ticker,
                    name=ticker,
                    side="BUY",
                    quantity=quantity,
                    price=0,
                    stop_loss_price=stop_loss_price,
                    reason=p.get("reason", ""),
                    confidence=int(p.get("confidence") or 70),
                    strategy_type=p.get("setup", "VOLUME_SURGE_LEADER"),
                )
                result = self._order_mgr.execute_buy(req)
                if result.success:
                    logger.info(
                        f"[v4 BUY 체결] {ticker} {quantity}주 "
                        f"체결가={result.executed_price:,.0f} 주문번호={result.order_no}"
                    )
                    self._telegram.notify(
                        f"📈 [v4 매수체결] {ticker} {quantity}주 @ {result.executed_price:,.0f}\n"
                        f"셋업={p.get('setup')} 손절={stop_loss_price:,.0f}\n"
                        f"{p.get('reason', '')[:100]}"
                    )
                else:
                    logger.warning(
                        f"[v4 BUY 실패] {ticker}: {result.error_msg}"
                    )
            except Exception as e:
                logger.warning(f"[v4 BUY] {p.get('ticker')} 실패: {e}")

        # get_open_positions를 루프 밖에서 1회 조회 (반복 DB 호출 + 타이밍 이슈 방지)
        open_pos_map: dict = {}
        try:
            open_pos_map = {pos["ticker"]: pos for pos in self._journal.get_open_positions()}
        except Exception as e:
            logger.warning(f"[v4 SELL] 저널 조회 실패: {e}")

        for p in sell_proposals:
            try:
                ticker = p["ticker"]
                match = open_pos_map.get(ticker)
                qty = int(float(match["quantity"])) if match else 0
                if qty <= 0:
                    logger.info(f"[v4 SELL] {ticker} 보유하지 않음 — 스킵")
                    continue
                req = OrderRequest(
                    ticker=ticker,
                    name=match.get("name", ticker) if match else ticker,
                    side="SELL",
                    quantity=qty,
                    price=0,
                    stop_loss_price=0.0,
                    reason=p.get("reason", ""),
                    confidence=int(p.get("confidence") or 100),
                )
                result = self._order_mgr.execute_sell(req)
                if result.success:
                    logger.info(
                        f"[v4 SELL 체결] {ticker} {qty}주 "
                        f"체결가={result.executed_price:,.0f} sell_type={p.get('sell_type')}"
                    )
                    self._telegram.notify(
                        f"📉 [v4 매도체결] {ticker} {qty}주 @ {result.executed_price:,.0f}\n"
                        f"사유={p.get('sell_type')} {p.get('reason', '')[:80]}"
                    )
                else:
                    logger.warning(f"[v4 SELL 실패] {ticker}: {result.error_msg}")
            except Exception as e:
                logger.warning(f"[v4 SELL] {p.get('ticker')} 실패: {e}")

    # ── 15:18 마감 청산 ────────────────────────────────────────────────────
    def run_close_v4(self) -> dict:
        regime = load_last_regime(path=_LAST_REGIME_PATH_V4) or {}
        context = self._build_context_v4(TradingPhaseV4.CLOSE, regime, watchlist=[])
        result = self._run_agent(TradingPhaseV4.CLOSE, context)

        try:
            close_pos_map = {pos["ticker"]: pos for pos in self._journal.get_open_positions()}
        except Exception as e:
            logger.warning(f"[v4 CLOSE] 저널 조회 실패: {e}")
            close_pos_map = {}

        for p in result.get("sell_proposals", []):
            try:
                ticker = p["ticker"]
                match = close_pos_map.get(ticker)
                qty = int(float(match["quantity"])) if match else 0
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
                result = self._order_mgr.execute_sell(req)
                if result.success:
                    logger.info(f"[v4 CLOSE SELL 체결] {ticker} {qty}주 @ {result.executed_price:,.0f}")
                    self._telegram.notify(
                        f"📉 [v4 마감청산] {ticker} {qty}주 @ {result.executed_price:,.0f}\n"
                        f"{p.get('reason', '')[:80]}"
                    )
                else:
                    logger.warning(f"[v4 CLOSE SELL 실패] {ticker}: {result.error_msg}")
            except Exception as e:
                logger.warning(f"[v4 CLOSE SELL] {p.get('ticker')}: {e}")

        logger.info(f"[v4 CLOSE] sell_proposals={len(result.get('sell_proposals', []))}")
        return result

    # ── 17:00 복기 ─────────────────────────────────────────────────────────
    def run_market_close_v4(self) -> dict:
        regime = load_last_regime(path=_LAST_REGIME_PATH_V4) or {}
        context = self._build_context_v4(TradingPhaseV4.MARKET_CLOSE, regime, watchlist=[])

        # market_close_data를 코드가 결정론적으로 수집해 주입 (LLM이 스킵하는 것을 방지).
        # v3와 동일한 패턴 — 프롬프트는 이 데이터를 전제로 해석/prior를 생성한다.
        try:
            from market_intelligence import market as mil_market
            from market_intelligence import portfolio as mil_portfolio
            snapshot = {
                "market": mil_market.get_market_context(self._mil, TradingPhaseV4.MARKET_CLOSE.value),
                "positions": mil_portfolio.get_open_positions(self._mil, TradingPhaseV4.MARKET_CLOSE.value),
                "daily_pnl": mil_portfolio.get_daily_pnl(self._mil, TradingPhaseV4.MARKET_CLOSE.value),
            }
        except Exception as e:
            logger.warning(f"[v4 MARKET_CLOSE] 스냅샷 수집 실패 — 빈 데이터로 진행: {e}")
            snapshot = {}
        context["market_close_data"] = snapshot

        result = self._run_agent(TradingPhaseV4.MARKET_CLOSE, context)

        # 다음 날 전략 연속성을 위해 prior 저장.
        next_day_prior = result.get("next_day_premarket_context", {})
        if next_day_prior:
            self._save_next_day_prior(next_day_prior)
            logger.info("[v4 MARKET_CLOSE] next_day_prior 저장 완료")

        # 장마감 알림
        pnl = snapshot.get("daily_pnl", {}) if isinstance(snapshot, dict) else {}
        pnl_pct = pnl.get("realized_pnl_pct", 0.0) if isinstance(pnl, dict) else 0.0
        pnl_krw = pnl.get("realized_pnl_krw", 0.0) if isinstance(pnl, dict) else 0.0
        self._telegram.notify(
            f"🔔 장마감입니다 ({self._today})\n"
            f"실현손익: {pnl_pct:+.2f}% / {pnl_krw:+,.0f}원\n"
            f"복기 완료"
        )

        logger.info("[v4 MARKET_CLOSE] 복기 완료")
        return result
