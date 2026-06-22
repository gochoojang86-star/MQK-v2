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
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

from agents.drift_detector import RegimeDriftDetector
from agents.regime_agent import RegimeAgent
from agents.review_agent import ReviewAgent
from agents.self_improvement_agent import SelfImprovementAgent
from agents.regime_agent import load_last_regime, save_last_regime, _LAST_REGIME_PATH
from agents.trading_agent import TradingAgent, TradingPhase, build_context
from broker.kis_api import KISApi
from broker.kiwoom_api import KiwoomApi
# from broker.kis_mcp_client import KISMCPClient  # MCP 비활성화
from broker.telegram import ApprovalRequest, TelegramApproval
from codes.improvement_manager import ImprovementManager
from codes.market_data import MarketData
from codes.news_fetcher import NaverNewsFetcher
from codes.order_manager import OrderManager
from codes.order_manager import OrderRequest
from codes.position_sizer import PositionSizer
from codes.risk_officer import PortfolioState, RiskOfficer, RiskViolation, TradeProposal
from codes.stop_take_profit import StopTakeProfitManager
from codes.technical import TechnicalAnalysis
from codes.trade_journal import TradeJournal
from config.settings import EXECUTION, LOG_CONFIG, RISK, ModelTier
from llm.client import LLMClient
from market_intelligence import market as mil_market
from market_intelligence import portfolio as mil_portfolio
from market_intelligence import risk_filter as mil_risk_filter
from market_intelligence import screening as mil_screening
from market_intelligence import theme as mil_theme
from market_intelligence.base import MILContext, ToolFailure
from market_intelligence.cache import MILCache
from market_intelligence.circuit_breaker import CircuitBreaker

logger = logging.getLogger("mqk_v3")

_DATA_DIR = Path(__file__).parent / "data"
_DRIFT_STATE_PATH = _DATA_DIR / "drift_state.json"
_WATCHLIST_PATH = _DATA_DIR / "watchlist.json"
_NEXT_DAY_PREMARKET_CONTEXT_PATH = _DATA_DIR / "next_day_premarket_context.json"
_TOOL_GAP_LOG_RETENTION_DAYS = 30
_MIN_MONITORING_WATCHLIST = 6
_MAX_WATCHLIST_SIZE = 10


def _default_drift_state(date: str) -> dict:
    return {"date": date, "last_trigger_time": {}, "today_caution_count": 0, "daily_lite_llm_calls": 0}


def _resolve_regime_evaluation_mode(now: datetime | None = None) -> str:
    now = now or datetime.now()
    if now.hour < 10:
        return "OPENING"
    if now.hour < 12:
        return "MIDDAY"
    return "AFTERNOON"


def _to_float(value) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(str(value).replace(",", ""))
    except (ValueError, TypeError):
        return 0.0


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(content, encoding="utf-8")
    os.replace(tmp_path, path)


def _normalize_watchlist(watchlist: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in watchlist:
        ticker = str(raw).strip()
        if not re.fullmatch(r"\d{6}", ticker):
            continue
        if ticker in seen:
            continue
        seen.add(ticker)
        normalized.append(ticker)
    return normalized


def _watchlist_ticker(entry: object) -> str:
    if isinstance(entry, dict):
        return str(entry.get("ticker") or "").strip()
    return str(entry).strip()


def _normalize_watchlist_entries(watchlist: list[object]) -> list[dict]:
    normalized: list[dict] = []
    seen: set[str] = set()
    for raw in watchlist:
        ticker = _watchlist_ticker(raw)
        if not re.fullmatch(r"\d{6}", ticker):
            continue
        if ticker in seen:
            continue
        seen.add(ticker)
        if isinstance(raw, dict):
            entry = {
                "ticker": ticker,
                "setup": str(raw.get("setup") or "TREND"),
                "confidence": int(raw.get("confidence") or 0),
                "reason": str(raw.get("reason") or ""),
            }
            d_day = raw.get("d_day")
            if d_day not in (None, ""):
                entry["d_day"] = str(d_day)
        else:
            entry = {
                "ticker": ticker,
                "setup": "TREND",
                "confidence": 0,
                "reason": "",
            }
        normalized.append(entry)
    return normalized


def _scan_watchlist_limit(context: dict) -> int:
    remaining = context.get("risk_budget_remaining", {}) or {}
    monitoring_slots = remaining.get("monitoring_slots")
    if monitoring_slots not in (None, ""):
        try:
            return min(_MAX_WATCHLIST_SIZE, max(int(monitoring_slots), 0))
        except (TypeError, ValueError):
            pass

    positions_left = remaining.get("positions_left", 0)
    try:
        positions_left_int = int(positions_left)
    except (TypeError, ValueError):
        positions_left_int = 0
    return min(_MAX_WATCHLIST_SIZE, max(positions_left_int, _MIN_MONITORING_WATCHLIST))


def _first_number_from_row(row: dict, keys: list[str]) -> float:
    for key in keys:
        value = row.get(key)
        if value in (None, ""):
            continue
        try:
            return float(str(value).replace(",", "").strip())
        except ValueError:
            continue
    return 0.0


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
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"[orchestrator_v3] watchlist.json 손상 — 빈 목록 반환: {e}")
        return []
    return [entry["ticker"] for entry in _normalize_watchlist_entries(data.get("watchlist", []))]


def load_watchlist_entries(path: Path = _WATCHLIST_PATH) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"[orchestrator_v3] watchlist.json 손상 — 빈 목록 반환: {e}")
        return []
    return _normalize_watchlist_entries(data.get("watchlist", []))


def save_watchlist(watchlist: list[object], path: Path = _WATCHLIST_PATH) -> None:
    normalized = _normalize_watchlist_entries(watchlist)
    _atomic_write_text(
        path,
        json.dumps(
            {
                "watchlist": normalized,
                "tickers": [entry["ticker"] for entry in normalized],
                "updated_at": datetime.now().isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        ),
    )


def load_next_day_premarket_context(path: Path = _NEXT_DAY_PREMARKET_CONTEXT_PATH) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"[orchestrator_v3] next_day_premarket_context.json 손상 — 빈 컨텍스트 반환: {e}")
        return {}
    return data if isinstance(data, dict) else {}


def save_next_day_premarket_context(payload: dict, path: Path = _NEXT_DAY_PREMARKET_CONTEXT_PATH) -> None:
    safe_payload = payload if isinstance(payload, dict) else {}
    _atomic_write_text(path, json.dumps(safe_payload, ensure_ascii=False, indent=2))


class MQKOrchestratorV3:
    """v3 독립 오케스트레이터."""

    def __init__(self, kis_api=None, mil: MILContext | None = None, dry_run_orders: bool | None = None):
        self._kis_api = kis_api or KISApi()
        self._market_data = MarketData(data_source=self._kis_api)
        self._risk_officer = RiskOfficer()
        self._position_sizer = PositionSizer()
        self._stp_manager = StopTakeProfitManager()
        self._technical = TechnicalAnalysis()
        self._regime_agent = RegimeAgent()
        self._review_agent = ReviewAgent()
        self._si_agent = SelfImprovementAgent()
        self._buy_review_llm = LLMClient()
        self._telegram = TelegramApproval()
        self._journal = TradeJournal()

        # KIS MCP 주문 경로 — MCP 서버 인증 불안정으로 비활성화 (KISApi 직접 사용)
        # if os.environ.get("KIS_USE_MCP", "false").lower() in {"1", "true", "yes"}:
        #     mcp = KISMCPClient()
        #     if mcp.available:
        #         logger.info("[V3 OrderManager] KIS MCP 서버 감지 → MCP 주문 경로 사용")
        #         order_api = mcp
        #     else:
        #         logger.info("[V3 OrderManager] KIS MCP 서버 미실행 → KIS API 폴백")
        order_api = self._kis_api
        self._order_manager = OrderManager(
            kis_api=order_api,
            telegram=self._telegram,
            dry_run=EXECUTION.order_dry_run if dry_run_orders is None else dry_run_orders,
            journal=self._journal,
        )
        self._naver_news = NaverNewsFetcher()
        if not self._naver_news.available:
            logger.warning("NAVER_CLIENT_ID/SECRET 미설정 — Naver 뉴스 비활성화")
        self._improvement_mgr = ImprovementManager(telegram=self._telegram)
        self._current_theme = ""
        self._last_regime = None
        self._last_theme = None
        self._candidate_context: dict[str, dict] = {}
        self._sector_performance: dict = {}
        self._atr_cache: dict[str, float] = {}
        self._today = datetime.now().strftime("%Y-%m-%d")
        self._log_dir = LOG_CONFIG.base_dir / self._today
        self._log_dir.mkdir(parents=True, exist_ok=True)
        try:
            processed = self._improvement_mgr.process_telegram_actions()
            if processed:
                logger.info("[V3 ImprovementManager] 텔레그램 인라인 액션 %d건 반영", processed)
        except Exception as exc:
            logger.warning("[V3 ImprovementManager] 텔레그램 인라인 액션 처리 실패: %s", exc)
        self._mil = mil or MILContext(
            kis_api=self._kis_api,
            kiwoom_api=KiwoomApi(),
            # mcp_client=KISMCPClient(),  # MCP 비활성화
            cache=MILCache(),
            circuit_breaker=CircuitBreaker(),
        )
        self._drift_detector = RegimeDriftDetector()
        self._trading_agent = TradingAgent(mil=self._mil)
        self._last_portfolio_snapshot: tuple[dict, dict] | None = None

    # ── 08:50 PREMARKET_EARLY (장전거래 전일 비교) ───────────────────────────
    def run_premarket_early_v3(self) -> dict:
        """08:50 장전거래 루틴. 전일 종가 기준 포지션 리스크 점검.

        레짐 판단(RegimeAgent)도 실행하되, 아직 장이 열리지 않았으므로 전일 데이터
        기반임을 컨텍스트에 명시한다. 오늘의 확정 레짐은 09:03 run_premarket_v3()가 담당.
        """
        return self.run_premarket_v3(session_type="PREMARKET_EARLY")

    # ── 09:03 PREMARKET (장중 첫번째 레짐 평가) ──────────────────────────────
    def run_premarket_v3(self, session_type: str = "PREMARKET_REGIME") -> dict:
        market_status = self.run_premarket()  # v2 RegimeAgent.judge() 재사용
        regime = self._last_regime
        save_last_regime(regime, path=_LAST_REGIME_PATH)
        save_drift_state(_default_drift_state(self._today), path=_DRIFT_STATE_PATH)
        self._mil.circuit_breaker.reset()

        regime_dict = _regime_to_dict(regime)
        context = self._build_context(TradingPhase.PREMARKET, regime_dict, "STABLE", watchlist=[])
        context["next_day_prior"] = load_next_day_premarket_context(path=_NEXT_DAY_PREMARKET_CONTEXT_PATH)
        context["session_type"] = session_type
        review = self._trading_agent.run(TradingPhase.PREMARKET, context)
        self._record_tool_request(review, TradingPhase.PREMARKET, regime_dict)
        self._alert_on_tool_failures(review, TradingPhase.PREMARKET)
        filename = "premarket_early_review.json" if session_type == "PREMARKET_EARLY" else "premarket_review.json"
        self._save_json(filename, review)
        return market_status

    # ── 09:10 / 11:00 / 14:00 SCAN ────────────────────────────────────────────
    def run_scan_v3(self) -> dict:
        regime = load_last_regime(path=_LAST_REGIME_PATH) or {}
        drift_state = load_drift_state(path=_DRIFT_STATE_PATH, today=self._today)
        context = self._build_context(TradingPhase.SCAN, regime, _drift_status(drift_state), watchlist=[])
        context["next_day_prior"] = load_next_day_premarket_context(path=_NEXT_DAY_PREMARKET_CONTEXT_PATH)
        result = self._trading_agent.run(TradingPhase.SCAN, context)
        self._record_tool_request(result, TradingPhase.SCAN, regime)
        self._alert_on_tool_failures(result, TradingPhase.SCAN)
        if not result.get("watchlist"):
            result = self._backfill_scan_result(result, context)
        save_watchlist(self._watchlist_entries_from_scan_result(result), path=_WATCHLIST_PATH)
        self._save_json("scan_v3.json", result)
        return result

    # ── */5 INTRADAY (드리프트 체크 + 매수/청산 판단) ──────────────────────────
    def run_intraday_v3(self) -> dict:
        regime = load_last_regime(path=_LAST_REGIME_PATH)
        if regime is None:
            logger.warning("[INTRADAY] last_regime.json 없음 — premarket을 먼저 실행하세요.")
            return {"action": "NO_TRADE", "reason": "no_regime"}

        # 당일 레짐 가드: 레짐 판단(09:03) 이전 틱이거나 premarket이 실패한 날,
        # 전일 레짐으로 매매하지 않는다.
        regime_date = str(regime.get("timestamp", ""))[:10]
        if regime_date != self._today:
            logger.warning(
                f"[INTRADAY] 레짐이 당일 것이 아님 (regime={regime_date}, today={self._today}) — 매매 스킵"
            )
            return {"action": "NO_TRADE", "reason": "stale_regime"}

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

        watchlist = load_watchlist_entries(path=_WATCHLIST_PATH)

        # 비용 절감 게이트: 평가할 후보도, 청산할 보유 포지션도 없고 시장도 STABLE이면
        # LLM을 호출하지 않는다. 보유 여부는 v3 포지션의 진실의 원천인 TradeJournal 기준
        # (실계좌 잔고 기준이면 모의 매매 중 보유를 놓친다). 조회 실패 시 보수적으로 진행.
        journal = getattr(self, "_journal", None)
        if drift_judgment == "STABLE" and not watchlist and journal is not None:
            try:
                has_positions = bool(journal.get_open_positions())
            except Exception as e:
                logger.warning(f"[INTRADAY] 저널 조회 실패 — 스킵 게이트 미적용, LLM 진행: {e}")
                has_positions = True
            if not has_positions:
                logger.info("[INTRADAY] watchlist 0 + 보유 0 + STABLE — LLM 미호출 스킵")
                return {"action": "NO_TRADE", "reason": "idle_skip"}

        context = self._build_context(
            TradingPhase.INTRADAY, regime, drift_judgment,
            watchlist=watchlist, risk_guidance_override=risk_guidance,
        )
        result = self._trading_agent.run(TradingPhase.INTRADAY, context)
        result = self._sanitize_intraday_result(result)
        self._record_tool_request(result, TradingPhase.INTRADAY, regime)
        self._alert_on_tool_failures(result, TradingPhase.INTRADAY)
        self._merge_watchlist_additions(result)
        self._handle_proposals(result.get("proposals", []))
        self._save_json(f"intraday_v3_{datetime.now().strftime('%H%M%S')}.json", result)
        return result

    # ── 15:12/15:17 LATE_INTRADAY (폭락일 전용 과매도 낙주 종가 진입) ──────────
    _CRASH_GATE_CHANGE_PCT = -3.0  # 코스피/코스닥 당일 등락률이 이 이하면 폭락일

    def run_late_intraday_v3(self) -> dict:
        """지수 폭락일에만 LLM을 호출하는 장 후반 REVERSAL 진입 phase.

        게이트는 코드가 강제한다: 코스피/코스닥 당일 -3% 이하 또는 레짐 RED가
        아니면 LLM을 호출하지 않고 즉시 스킵한다 (비용 0, 진입 0).
        """
        regime = load_last_regime(path=_LAST_REGIME_PATH)
        if regime is None or str(regime.get("timestamp", ""))[:10] != self._today:
            logger.warning("[LATE_INTRADAY] 당일 레짐 없음 — 스킵")
            return {"action": "NO_TRADE", "reason": "stale_regime"}

        try:
            market_ctx = mil_market.get_market_context(self._mil, TradingPhase.LATE_INTRADAY.value)
            kospi_chg = _to_float(market_ctx.get("kospi_change_pct"))
            kosdaq_chg = _to_float(market_ctx.get("kosdaq_change_pct"))
        except ToolFailure as e:
            # 게이트 판단 불가 → 보수적으로 진입하지 않는다.
            logger.warning(f"[LATE_INTRADAY] 시장 데이터 조회 실패 — 게이트 판단 불가, 스킵: {e}")
            return {"action": "NO_TRADE", "reason": "gate_data_unavailable"}

        is_crash = (
            kospi_chg <= self._CRASH_GATE_CHANGE_PCT
            or kosdaq_chg <= self._CRASH_GATE_CHANGE_PCT
            or regime.get("status") == "RED"
        )
        if not is_crash:
            logger.info(
                f"[LATE_INTRADAY] 폭락 게이트 미충족 (KOSPI {kospi_chg:+.2f}%, KOSDAQ {kosdaq_chg:+.2f}%, "
                f"status={regime.get('status')}) — LLM 미호출 스킵"
            )
            return {"action": "NO_TRADE", "reason": "no_crash_gate"}

        logger.warning(
            f"[LATE_INTRADAY] 폭락 게이트 통과 (KOSPI {kospi_chg:+.2f}%, KOSDAQ {kosdaq_chg:+.2f}%, "
            f"status={regime.get('status')}) — 낙주 진입 판단 시작"
        )
        drift_state = load_drift_state(path=_DRIFT_STATE_PATH, today=self._today)
        watchlist = load_watchlist(path=_WATCHLIST_PATH)
        context = self._build_context(
            TradingPhase.LATE_INTRADAY, regime, _drift_status(drift_state), watchlist=watchlist,
        )
        result = self._trading_agent.run(TradingPhase.LATE_INTRADAY, context)
        self._record_tool_request(result, TradingPhase.LATE_INTRADAY, regime)
        self._alert_on_tool_failures(result, TradingPhase.LATE_INTRADAY)
        self._merge_watchlist_additions(result)
        self._handle_proposals(result.get("proposals", []))
        self._save_json(f"late_intraday_v3_{datetime.now().strftime('%H%M%S')}.json", result)
        return result

    # ── 15:30 CLOSE ────────────────────────────────────────────────────────────
    def run_close_v3(self) -> dict:
        """15:18 — 정규장 내 청산 판단. 일반 주문으로 즉시(또는 동시호가) 체결된다.

        모의투자가 장후 시간외(06) 주문을 지원하지 않아 정규장 내로 당겼다.
        거래 복기는 마감 확정 데이터로 market_close(17:00)가 수행한다.
        실전 전환 후 늦은 청산이 필요하면 after_hours=True 경로(06)를 쓸 수 있다.
        """
        regime = load_last_regime(path=_LAST_REGIME_PATH) or {}
        drift_state = load_drift_state(path=_DRIFT_STATE_PATH, today=self._today)
        context = self._build_context(TradingPhase.CLOSE, regime, _drift_status(drift_state), watchlist=[])
        result = self._trading_agent.run(TradingPhase.CLOSE, context)
        self._record_tool_request(result, TradingPhase.CLOSE, regime)
        self._alert_on_tool_failures(result, TradingPhase.CLOSE)
        self._handle_sell_proposals(result.get("sell_proposals", []))
        self._save_json("close_v3.json", result)
        return result

    # ── 17:00 MARKET_CLOSE ───────────────────────────────────────────────────
    def run_market_close_v3(self) -> dict:
        """장마감 분석. 팩트(snapshot)는 코드가 결정론적으로 수집해 컨텍스트에 주입하고
        파일로 저장한다 — LLM의 도구 호출 재량에 맡기면 수집을 건너뛸 수 있다 (D1 확인).
        LLM은 해석(close_market_read)과 다음날 prior 생성만 담당한다."""
        regime = load_last_regime(path=_LAST_REGIME_PATH) or {}
        snapshot = self._collect_market_close_snapshot()
        self._save_json("market_close_snapshot.json", snapshot)

        context = self._build_context(TradingPhase.MARKET_CLOSE, regime, "STABLE", watchlist=[])
        context["market_close_data"] = snapshot
        result = self._trading_agent.run(TradingPhase.MARKET_CLOSE, context)
        self._record_tool_request(result, TradingPhase.MARKET_CLOSE, regime)
        self._alert_on_tool_failures(result, TradingPhase.MARKET_CLOSE)
        self.run_close_review()  # v2 거래 복기 — 마감 확정 데이터 기준
        self._save_json("close_market_read.json", result.get("close_market_read", {}))
        next_day_context = result.get("next_day_premarket_context", {})
        self._save_json("next_day_premarket_context.json", next_day_context)
        save_next_day_premarket_context(next_day_context, path=_NEXT_DAY_PREMARKET_CONTEXT_PATH)
        self._save_json("tool_gap_summary.json", self._summarize_tool_gaps())
        return result

    def _record_tool_request(self, result: dict, phase: TradingPhase, regime: dict) -> None:
        if result.get("action") != "TOOL_REQUEST":
            return
        tool_request = result.get("tool_request") or {}
        record = {
            "timestamp": datetime.now().isoformat(),
            "agent": "TradingAgent",
            "phase": phase.value,
            "regime": regime.get("status", ""),
            "missing_capability": tool_request.get("missing_capability", ""),
            "priority": tool_request.get("priority", "medium"),
            "why_needed": tool_request.get("why_needed", ""),
            "affected_tickers": tool_request.get("affected_tickers", []),
            "suggested_data_source": tool_request.get("suggested_data_source", []),
            "fallback_action": tool_request.get("fallback_action", "NO_TRADE"),
            "status": "open",
        }
        log_path = self._log_dir / "tool_gap_log.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _summarize_tool_gaps(self, path: Path | None = None) -> dict:
        path = path or (self._log_dir / "tool_gap_log.jsonl")
        if not path.exists():
            return {
                "date": self._today,
                "top_missing_capabilities": [],
                "high_priority_count": 0,
                "recommendation": "no_tool_gaps_detected",
            }

        cutoff = (
            datetime.strptime(self._today, "%Y-%m-%d") - timedelta(days=_TOOL_GAP_LOG_RETENTION_DAYS)
        ).strftime("%Y-%m-%d")

        records: list[dict] = []
        retained_rows: list[dict] = []
        total_lines = 0
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                total_lines += 1
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                row_date = str(row.get("timestamp", ""))[:10]
                if row_date >= cutoff:
                    retained_rows.append(row)
                if row_date == self._today:
                    records.append(row)

        if len(retained_rows) < total_lines:
            with path.open("w", encoding="utf-8") as f:
                for row in retained_rows:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")

        if not records:
            return {
                "date": self._today,
                "top_missing_capabilities": [],
                "high_priority_count": 0,
                "recommendation": "no_tool_gaps_detected",
            }

        grouped: dict[str, dict] = {}
        high_priority_count = 0
        for row in records:
            name = str(row.get("missing_capability") or "unspecified_capability")
            item = grouped.setdefault(name, {
                "name": name,
                "count": 0,
                "phases": set(),
                "priorities": set(),
                "tickers": [],
            })
            item["count"] += 1
            item["phases"].add(str(row.get("phase") or ""))
            priority = str(row.get("priority") or "medium")
            item["priorities"].add(priority)
            if priority == "high":
                high_priority_count += 1
            for ticker in row.get("affected_tickers", []):
                if ticker not in item["tickers"]:
                    item["tickers"].append(ticker)

        top = sorted(grouped.values(), key=lambda x: x["count"], reverse=True)[:5]
        top_missing = [
            {
                "name": item["name"],
                "count": item["count"],
                "phases": sorted(p for p in item["phases"] if p),
                "priority": "high" if "high" in item["priorities"] else ("medium" if "medium" in item["priorities"] else "low"),
                "example_tickers": item["tickers"][:5],
            }
            for item in top
        ]
        recommendation = (
            f"prioritize_{top_missing[0]['name']}"
            if top_missing else "no_tool_gaps_detected"
        )
        return {
            "date": self._today,
            "top_missing_capabilities": top_missing,
            "high_priority_count": high_priority_count,
            "recommendation": recommendation,
        }

    def _collect_market_close_snapshot(self) -> dict:
        """마감 팩트 스냅샷 (코드 수집, 섹션별 실패 격리 + missing_fields 기록)."""
        snapshot: dict = {"date": self._today}
        missing: list[str] = []
        try:
            ctx = mil_market.get_market_context(self._mil, TradingPhase.MARKET_CLOSE.value)
            for k in ("kospi", "kospi_change_pct", "kosdaq", "kosdaq_change_pct",
                      "foreign_net_buy_krw", "institution_net_buy_krw",
                      "program_net_buy_krw", "investor_trend_days"):
                snapshot[k] = ctx.get(k)
        except (ToolFailure, Exception) as e:
            logger.warning(f"[MARKET_CLOSE] 시장 컨텍스트 수집 실패: {e}")
            missing.append("market_context")
        try:
            breadth_out = mil_market.get_sector_breadth(self._mil, TradingPhase.MARKET_CLOSE.value)
            snapshot["market_breadth"] = breadth_out.get("market_breadth", {})
            sectors = sorted(breadth_out.get("sectors", []),
                             key=lambda s: s.get("change_pct", 0.0), reverse=True)
            snapshot["top_sectors"] = sectors[:5]
            snapshot["bottom_sectors"] = sectors[-5:]
        except (ToolFailure, Exception) as e:
            logger.warning(f"[MARKET_CLOSE] 업종 브레드스 수집 실패: {e}")
            missing.append("sector_breadth")
        try:
            news = mil_market.get_news_market(self._mil, TradingPhase.MARKET_CLOSE.value)
            snapshot["headlines"] = news.get("headlines", [])[:15]
        except (ToolFailure, Exception) as e:
            logger.warning(f"[MARKET_CLOSE] 뉴스 수집 실패: {e}")
            missing.append("news")
        snapshot["data_quality"] = {"missing_fields": missing}
        return snapshot

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

        # 잔고 조회는 일시적 KIS 500/타임아웃으로 실패할 수 있다 (D1 라이브 테스트 및
        # 2026-06-15 발견). 최대 3회 재시도 후에도 실패하면, 직전에 성공한 스냅샷을
        # (stale 표시 후) 재사용한다. 당일 첫 조회부터 실패하면 스냅샷이 없으므로
        # 그때만 보수적으로 강등한다: positions_left=0, 손실예산 0.
        positions = daily_pnl = None
        last_exc: Exception | None = None
        for attempt in range(1, 4):
            try:
                positions = mil_portfolio.get_open_positions(self._mil, phase.value)
                daily_pnl = mil_portfolio.get_daily_pnl(self._mil, phase.value)
                break
            except ToolFailure as e:
                last_exc = e
                if attempt < 3:
                    breaker = getattr(self._mil, "circuit_breaker", None)
                    if breaker is not None:
                        breaker.reset("get_open_positions")
                        breaker.reset("get_daily_pnl")
                    time.sleep(1.0)

        portfolio_unavailable = False
        last_snapshot = getattr(self, "_last_portfolio_snapshot", None)
        if positions is not None:
            self._last_portfolio_snapshot = (positions, daily_pnl)
        elif last_snapshot is not None:
            logger.warning(
                f"[V3 CONTEXT] 포트폴리오 조회 3회 실패 — 직전 스냅샷으로 대체(stale): {last_exc}"
            )
            cached_positions, cached_daily_pnl = last_snapshot
            positions = {**cached_positions, "data_unavailable": True, "stale": True}
            daily_pnl = {**cached_daily_pnl, "data_unavailable": True, "stale": True}
        else:
            logger.warning(f"[V3 CONTEXT] 포트폴리오 조회 3회 실패 — 보수적 강등(매수 예산 0): {last_exc}")
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
        monitoring_slots = min(_MAX_WATCHLIST_SIZE, max(positions_left, _MIN_MONITORING_WATCHLIST))

        balance_summary: dict = {}
        balance_cash_metrics: dict = {}
        kis_api = getattr(self, "_kis_api", None)
        if kis_api is not None and hasattr(kis_api, "get_balance"):
            try:
                balance = kis_api.get_balance()
                summary_rows = balance.get("output2") or balance.get("summary") or []
                if isinstance(summary_rows, list):
                    balance_summary = summary_rows[0] if summary_rows else {}
                elif isinstance(summary_rows, dict):
                    balance_summary = summary_rows
            except Exception as e:
                logger.warning(f"[V3 CONTEXT] 잔고 요약 조회 실패 — 현금 비중 힌트 생략: {e}")

        available_cash = _first_number_from_row(balance_summary, ["ord_psbl_cash", "dnca_tot_amt", "cash"])
        estimated_position_value = sum(
            _to_float(row.get("current_price")) * _to_float(row.get("quantity"))
            for row in positions.get("positions", [])
        )
        estimated_total_capital = _first_number_from_row(
            balance_summary,
            ["tot_evlu_amt", "nass_amt", "tot_asst_amt", "total_capital"],
        )
        if estimated_total_capital <= 0:
            estimated_total_capital = _to_float(daily_pnl.get("total_eval_amt"))
        if estimated_total_capital <= 0:
            estimated_total_capital = available_cash + estimated_position_value

        cash_ratio_pct = round(available_cash / estimated_total_capital * 100, 2) if estimated_total_capital > 0 else 0.0
        invested_ratio_pct = round(estimated_position_value / estimated_total_capital * 100, 2) if estimated_total_capital > 0 else 0.0
        balance_cash_metrics = {
            "available_cash_krw": available_cash,
            "estimated_position_value_krw": estimated_position_value,
            "estimated_total_capital_krw": estimated_total_capital,
            "cash_ratio_pct": cash_ratio_pct,
            "invested_ratio_pct": invested_ratio_pct,
            "position_count_hint": positions.get("position_count", 0),
            "max_positions_guidance": max_positions,
            "positions_left_is_soft": True,
        }
        positions = {**positions, **balance_cash_metrics}

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
                "monitoring_slots": monitoring_slots,
                "daily_loss_remaining_pct": daily_loss_remaining,
            },
            watchlist=watchlist,
            exploration_policy={
                "allow_intraday_discovery": phase == TradingPhase.INTRADAY,
                "max_new_tickers": 2 if phase == TradingPhase.INTRADAY else 0,
                "require_strong_evidence": True,
                "discovery_priority": "watchlist_first_then_new_leaders",
            },
            context_timestamps={
                "regime": regime.get("timestamp", ""),
                "now": datetime.now().isoformat(),
            },
        )

    def _merge_watchlist_additions(self, result: dict) -> None:
        additions = [
            str(ticker).strip()
            for ticker in (result.get("watchlist_additions") or [])
            if re.fullmatch(r"\d{6}", str(ticker).strip())
        ]
        if not additions:
            return
        current = load_watchlist_entries(path=_WATCHLIST_PATH)
        current_tickers = [entry["ticker"] for entry in current]
        merged = current + [
            {"ticker": ticker, "setup": "WATCH_ONLY", "confidence": 0, "reason": "intraday_watchlist_addition"}
            for ticker in additions
            if ticker not in current_tickers
        ]
        if merged != current:
            save_watchlist(merged, path=_WATCHLIST_PATH)
            logger.info(f"[WATCHLIST MERGE] intraday additions={additions} → {[entry['ticker'] for entry in merged]}")

    def _sanitize_intraday_result(self, result: dict) -> dict:
        if not isinstance(result, dict):
            return {"action": "NO_TRADE", "reason": "invalid_intraday_result", "proposals": []}

        action = str(result.get("action") or "NO_TRADE").upper()
        raw_proposals = result.get("proposals") or []
        if not isinstance(raw_proposals, list):
            raw_proposals = []

        valid_proposals: list[dict] = []
        for proposal in raw_proposals:
            if not isinstance(proposal, dict):
                continue
            side = str(proposal.get("side") or "").upper()
            if side not in {"BUY", "SELL"}:
                continue
            valid_proposals.append(proposal)

        if action in {"HOLD", "NO_TRADE"} and valid_proposals:
            logger.warning(
                f"[V3 INTRADAY] action={action}인데 실행 proposal {len(valid_proposals)}건 포함 — 안전상 제거"
            )
            result = {**result, "proposals": []}
            return result

        if action == "SELL":
            valid_proposals = [p for p in valid_proposals if str(p.get("side") or "").upper() == "SELL"]
        elif action == "BUY":
            valid_proposals = [p for p in valid_proposals if str(p.get("side") or "").upper() in {"BUY", "SELL"}]

        if valid_proposals != raw_proposals:
            result = {**result, "proposals": valid_proposals}
        return result

    def _watchlist_entries_from_scan_result(self, result: dict) -> list[dict]:
        watchlist = [str(ticker).strip() for ticker in (result.get("watchlist") or [])]
        candidate_map = {
            str(item.get("ticker") or "").strip(): item
            for item in (result.get("candidates") or [])
            if isinstance(item, dict)
        }
        entries: list[dict] = []
        for ticker in watchlist:
            if not re.fullmatch(r"\d{6}", ticker):
                continue
            candidate = candidate_map.get(ticker, {})
            entry = {
                "ticker": ticker,
                "setup": str(candidate.get("setup") or "TREND"),
                "confidence": int(candidate.get("confidence") or 0),
                "reason": str(candidate.get("reason") or ""),
            }
            d_day = candidate.get("d_day")
            if d_day not in (None, ""):
                entry["d_day"] = str(d_day)
            entries.append(entry)
        return entries

    def _backfill_scan_result(self, result: dict, context: dict) -> dict:
        """SCAN 결과가 비면 거래대금/상태 기반 deterministic watchlist를 만든다."""
        min_trading_value = float(context.get("risk_guidance", {}).get("min_trading_value_krw", 0) or 0)
        limit = _scan_watchlist_limit(context)
        if limit <= 0:
            return result

        candidates_by_ticker: dict[str, dict] = {}
        overheated_bias_warning = False

        try:
            theme_result = mil_theme.get_theme_candidates(self._mil, TradingPhase.SCAN.value)
        except ToolFailure as e:
            logger.warning(f"[SCAN BACKFILL] get_theme_candidates 실패: {e}")
            theme_result = {"candidates": []}
        for row in theme_result.get("candidates", []):
            ticker = str(row.get("ticker") or "").strip()
            if not re.fullmatch(r"\d{6}", ticker):
                continue
            candidates_by_ticker[ticker] = {
                "ticker": ticker,
                "name": row.get("name"),
                "trading_value": _to_float(row.get("trading_value")),
                "change_pct": _to_float(row.get("change_pct")),
                "source": "kiwoom_theme",
                "theme_name": row.get("theme_name"),
            }

        try:
            movers = mil_screening.get_top_movers(self._mil, TradingPhase.SCAN.value)
        except ToolFailure as e:
            logger.warning(f"[SCAN BACKFILL] get_top_movers 실패: {e}")
            movers = {"change_rate_top": [], "movers": []}
        overheated_bias_warning = bool(movers.get("overheated_bias_warning"))

        rows = movers.get("change_rate_top") or movers.get("movers") or []
        for row in rows:
            ticker = str(row.get("ticker") or "").strip()
            if not re.fullmatch(r"\d{6}", ticker):
                continue
            trading_value = _to_float(row.get("trading_value_krw", row.get("trading_value", 0)))
            existing = candidates_by_ticker.get(ticker)
            if existing:
                existing["trading_value"] = max(existing["trading_value"], trading_value)
                existing["change_pct"] = max(existing["change_pct"], _to_float(row.get("change_pct")))
                existing["source"] = f"{existing['source']}+top_movers"
            else:
                candidates_by_ticker[ticker] = {
                    "ticker": ticker,
                    "name": row.get("name"),
                    "trading_value": trading_value,
                    "change_pct": _to_float(row.get("change_pct")),
                    "source": "top_movers",
                    "theme_name": None,
                }

        candidates = []
        for ticker, row in candidates_by_ticker.items():
            if row["trading_value"] < min_trading_value:
                continue
            try:
                status = mil_risk_filter.get_stock_status(self._mil, TradingPhase.SCAN.value, ticker)
            except ToolFailure:
                continue
            if status.get("trading_halted") or status.get("administrative_issue") or status.get("is_limit_up"):
                continue
            candidates.append(row)

        candidates.sort(key=lambda x: (x["trading_value"], x["change_pct"]), reverse=True)
        watchlist = [row["ticker"] for row in candidates[:limit]]
        if not watchlist:
            return result

        logger.info(f"[SCAN BACKFILL] deterministic watchlist={watchlist}")
        return {
            "next_action": "final",
            "action": "WATCHLIST_UPDATE",
            "watchlist": watchlist,
            "candidates": [
                {
                    "ticker": row["ticker"],
                    "confidence": 65,
                    "reason": f"orchestrator_scan_backfill:{row['source']}",
                    "setup": "RELATIVE_STRENGTH" if "theme" not in row["source"] else "TREND",
                }
                for row in candidates[:limit]
            ],
            "overheated_bias_warning": overheated_bias_warning,
            "reason": f"{result.get('reason', '')} | orchestrator_scan_backfill".strip(" |"),
        }

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

    def _alert_on_tool_failures(self, result: dict, phase: TradingPhase) -> None:
        failures = result.get("tool_failures")
        if not failures:
            return
        lines = [f"⚠️ [{phase.value}] 도구 {len(failures)}개 연속 실패 — NO_TRADE로 강제 전환"]
        for f in failures:
            lines.append(f"- {f.get('tool')}: {f.get('message')}")
        try:
            self._telegram.notify("\n".join(lines))
        except Exception as e:
            logger.warning(f"[도구 실패 알림] 텔레그램 발송 실패: {e}")

    # ── proposal → Safety Layer ─────────────────────────────────────────────

    def _handle_proposals(self, proposals: list[dict]) -> list[dict]:
        results = []
        for p in proposals:
            try:
                if not isinstance(p, dict):
                    raise TypeError(f"proposal이 dict가 아님: {type(p).__name__}")
                if p.get("side") == "BUY":
                    # confidence=0 또는 WATCH_ONLY는 LLM이 "관망"을 표현한 것 — 실행 금지
                    if int(p.get("confidence") or 0) == 0 or p.get("setup") == "WATCH_ONLY":
                        logger.info(f"[V3 PROPOSAL] WATCH_ONLY/confidence=0 무시: {p.get('ticker')}")
                        results.append({"action": "SKIP", "reason": "watch_only", "proposal": _safe_summary(p)})
                        continue
                    results.append(self._process_v3_buy_proposal(p))
                elif p.get("side") == "SELL":
                    results.append(self._process_v3_sell_proposal(p, require_approval=False))
                else:
                    results.append({"action": "SKIP", "reason": "unknown_side", "proposal": _safe_summary(p)})
            except (KeyError, TypeError, ValueError, AttributeError) as e:
                logger.warning(f"[V3 PROPOSAL] 잘못된 proposal 무시: {e} | proposal={_safe_summary(p)}")
                results.append({"action": "SKIP", "reason": "malformed_proposal", "proposal": _safe_summary(p)})
            except Exception as e:
                logger.error(f"[V3 PROPOSAL] 주문 실패: {e} | proposal={_safe_summary(p)}")
                results.append({"action": "ORDER_FAILED", "reason": str(e), "proposal": _safe_summary(p)})
        return results

    def _handle_sell_proposals(self, proposals: list[dict], after_hours: bool = False) -> list[dict]:
        results = []
        for p in proposals:
            try:
                if not isinstance(p, dict):
                    raise TypeError(f"proposal이 dict가 아님: {type(p).__name__}")
                results.append(self._process_v3_sell_proposal(p, after_hours=after_hours))
            except (KeyError, TypeError, ValueError, AttributeError) as e:
                logger.warning(f"[V3 SELL PROPOSAL] 잘못된 proposal 무시: {e} | proposal={_safe_summary(p)}")
                results.append({"action": "SKIP", "reason": "malformed_proposal", "proposal": _safe_summary(p)})
            except Exception as e:
                logger.error(f"[V3 SELL PROPOSAL] 주문 실패: {e} | proposal={_safe_summary(p)}")
                results.append({"action": "SELL_FAILED", "reason": str(e), "proposal": _safe_summary(p)})
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
        # 텔레그램 승인/주문 로그에 종목명이 보이도록 스냅샷에서 해석 (없으면 코드로 폴백)
        stock_name = getattr(snapshot, "name", "") or ticker
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

        buy_review = self._review_v3_buy_proposal(
            proposal=proposal,
            snapshot=snapshot,
            sizing=sizing,
            portfolio_state=portfolio_state,
        )
        if not buy_review.get("approve", True):
            logger.info(f"[V3 BUY REVIEW] {ticker}: 최종 재판단 거부 - {buy_review.get('reason', '')}")
            return {
                "action": "REJECTED",
                "ticker": ticker,
                "reason": buy_review.get("reason", "buy_review_rejected"),
                "buy_review": buy_review,
            }

        approval_request_id = None
        if RISK.require_telegram_approval:
            approval_req = ApprovalRequest(
                ticker=ticker, name=stock_name, decision="BUY",
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
            ticker=ticker, name=stock_name, side="BUY",
            quantity=sizing.quantity,
            price=entry_price,
            stop_loss_price=sizing.stop_loss_price,
            reason=proposal.get("reason", ""),
            confidence=proposal.get("confidence", 0),
            approval_request_id=approval_request_id,
            strategy_type=proposal.get("setup", "TREND"),
        )
        result = self._order_manager.execute_buy(order)
        if result.success:
            pnl_sign = "+" if sizing.risk_pct >= 0 else ""
            try:
                self._telegram.notify(
                    f"✅ *매수 체결* {stock_name} ({ticker})\n"
                    f"체결가: {result.executed_price:,.0f}원 × {result.quantity}주\n"
                    f"손절: {sizing.stop_loss_price:,.0f}원 | 리스크: {pnl_sign}{sizing.risk_pct:.3f}%\n"
                    f"확신도: {proposal.get('confidence', 0)}% | {proposal.get('reason', '')[:200]}"
                )
            except Exception as e:
                logger.warning(f"[BUY 체결 알림] 텔레그램 발송 실패: {e}")
        return {"action": "BUY_EXECUTED", "ticker": ticker, "success": result.success}

    def _review_v3_buy_proposal(
        self,
        proposal: dict,
        snapshot,
        sizing,
        portfolio_state: PortfolioState,
    ) -> dict:
        llm = getattr(self, "_buy_review_llm", None)
        if llm is None:
            return {"approve": True, "reason": "buy_review_unavailable"}

        stock_name = getattr(snapshot, "name", "") or proposal.get("ticker", "")
        current_price = float(getattr(snapshot, "current_price", 0.0) or 0.0)
        change_pct = float(getattr(snapshot, "change_pct", 0.0) or 0.0)
        trading_value = self._first_number(
            getattr(snapshot, "__dict__", {}),
            ["trading_value_krw", "trading_value", "acml_tr_pbmn", "volume_value"],
        )
        market_cap = self._first_number(
            getattr(snapshot, "__dict__", {}),
            ["market_cap", "market_cap_krw", "hts_avls"],
        )
        portfolio_payload = {
            "total_capital_krw": round(float(getattr(portfolio_state, "total_capital", 0.0) or 0.0), 0),
            "daily_pnl_krw": round(float(getattr(portfolio_state, "daily_pnl", 0.0) or 0.0), 0),
            "open_positions_count": len(getattr(portfolio_state, "open_positions", []) or []),
            "theme_exposure": getattr(portfolio_state, "theme_exposure", {}) or {},
        }
        review_payload = {
            "ticker": proposal.get("ticker"),
            "name": stock_name,
            "setup": proposal.get("setup", "TREND"),
            "confidence": proposal.get("confidence", 0),
            "reason": proposal.get("reason", ""),
            "current_price": current_price,
            "change_pct": change_pct,
            "trading_value_krw": trading_value,
            "market_cap_krw": market_cap,
            "stop_loss_price": float(sizing.stop_loss_price),
            "quantity": int(sizing.quantity),
            "risk_pct": float(sizing.risk_pct),
        }
        user_msg = (
            "MQK soul 전략 기준 최종 매수 재판단이다.\n"
            "후발주, 약한 2등주, 과도한 추격은 거부하고 진짜 본류 대장 또는 고품질 리더 진입만 승인하라.\n"
            "리뷰 실패 시 시스템은 fail-open으로 진행하므로, 판단 가능할 때만 명확히 거부하라.\n\n"
            f"proposal={json.dumps(review_payload, ensure_ascii=False)}\n"
            f"portfolio={json.dumps(portfolio_payload, ensure_ascii=False)}\n\n"
            '반드시 단일 JSON으로 답하라: {"approve": true|false, "reason": "..."}'
        )
        try:
            result = llm.call(
                system=(
                    "You are the final MQK buy reviewer. "
                    "Approve only high-quality leader entries consistent with the soul strategy. "
                    "Reject laggards, weak followers, and overextended chase entries. "
                    "Return valid JSON only."
                ),
                user=user_msg,
                tier=ModelTier.REASONING,
                expect_json=True,
            )
        except Exception as exc:
            logger.warning(f"[V3 BUY REVIEW] {proposal.get('ticker')}: 리뷰 호출 실패 — fail-open: {exc}")
            return {"approve": True, "reason": "buy_review_call_failed"}

        approve = bool(result.get("approve", True))
        reason = str(result.get("reason") or ("approved" if approve else "buy_review_rejected"))
        return {"approve": approve, "reason": reason}

    def _process_v3_sell_proposal(
        self, proposal: dict, after_hours: bool = False, require_approval: bool = False
    ) -> dict:
        ticker = proposal["ticker"]
        open_pos = self._journal.get_open_positions()
        match = next((p for p in open_pos if p["ticker"] == ticker), None)
        if match is None:
            return {"action": "SKIP", "ticker": ticker, "reason": "보유하지 않은 종목"}

        snapshot = self._market_data.get_snapshot(ticker)
        stock_name = match.get("name", ticker)
        current_price = snapshot.current_price
        avg_price = float(match.get("avg_price") or match.get("entry_price") or 0)
        pnl_pct = ((current_price - avg_price) / avg_price * 100) if avg_price > 0 else 0.0

        pnl_amount = (current_price - avg_price) * int(match["quantity"]) if avg_price > 0 else 0.0

        approval_request_id = None
        if require_approval and RISK.require_telegram_approval:
            approval_req = ApprovalRequest(
                ticker=ticker, name=stock_name, decision="SELL",
                entry_price=current_price,
                stop_loss_price=float(match.get("stop_loss_price", 0)),
                quantity=int(match["quantity"]),
                risk_pct=round(pnl_pct, 2),
                confidence=proposal.get("confidence", 100),
                reason=proposal.get("reason", ""),
                counter_argument="",
                pnl_amount=round(pnl_amount, 0),
            )
            approval = self._telegram.request_approval(approval_req)
            approval_request_id = approval.request_id
            if not approval.approved:
                return {"action": "REJECTED", "ticker": ticker, "reason": "텔레그램 거부"}

        order = OrderRequest(
            ticker=ticker, name=stock_name, side="SELL",
            quantity=int(match["quantity"]),
            price=current_price,
            stop_loss_price=float(match.get("stop_loss_price", 0)),
            reason=proposal.get("reason", ""),
            confidence=proposal.get("confidence", 100),
            approval_request_id=approval_request_id,
            after_hours=after_hours,
        )
        result = self._order_manager.execute_sell(order)
        if result.success:
            pnl_sign = "+" if pnl_pct >= 0 else ""
            pnl_color = "🟢" if pnl_pct >= 0 else "🔴"
            try:
                self._telegram.notify(
                    f"{pnl_color} *매도 체결* {stock_name} ({ticker})\n"
                    f"체결가: {result.executed_price:,.0f}원 × {result.quantity}주\n"
                    f"손익: {pnl_sign}{pnl_pct:.2f}% ({pnl_sign}{pnl_amount:,.0f}원)\n"
                    f"사유: {proposal.get('reason', '')[:200]}"
                )
            except Exception as e:
                logger.warning(f"[SELL 체결 알림] 텔레그램 발송 실패: {e}")
        return {"action": "SELL_EXECUTED", "ticker": ticker, "success": result.success}

    # ── v3 독립 기반 유틸/장전/복기 ──────────────────────────────────────────

    def build_portfolio_state(
        self,
        theme_by_ticker: dict[str, str] | None = None,
    ) -> PortfolioState:
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
            ["tot_evlu_amt", "nass_amt", "tot_asst_amt", "total_capital", "dnca_tot_amt"],
        )

        open_positions = []
        theme_value: dict[str, float] = {}
        position_value_total = 0.0
        for row in holdings:
            ticker = str(row.get("ticker") or row.get("pdno") or row.get("mksc_shrn_iscd") or "").strip()
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
        daily_pnl = self._first_number(summary, ["daily_pnl", "thdt_evlu_pfls_amt", "asst_icdc_amt"])

        return PortfolioState(
            total_capital=total_capital,
            daily_pnl=daily_pnl,
            open_positions=open_positions,
            theme_exposure=theme_exposure,
        )

    def run_premarket(self) -> dict:
        now = datetime.now()
        evaluation_mode = _resolve_regime_evaluation_mode(now)
        logger.info("[V3 %s] 레짐 평가 시작 (%s)", now.strftime("%H:%M"), evaluation_mode)
        index = self._market_data.get_index_status()
        market_news_items = self._naver_news.search("코스피 코스닥 시장 주식", display=10)
        market_news_summary = " | ".join(n.title for n in market_news_items[:5])

        try:
            breadth_data = mil_market.get_sector_breadth(self._mil, TradingPhase.PREMARKET.value)
            sectors = breadth_data.get("sectors", [])
            top_rising = sorted(sectors, key=lambda x: x["change_pct"], reverse=True)[:5]
            top_falling = sorted(sectors, key=lambda x: x["change_pct"])[:3]
            top_volume = sorted(sectors, key=lambda x: x["trading_value_share_pct"], reverse=True)[:3]
            self._sector_performance = {
                "top_rising": [{"name": s["sector_name"], "change_pct": s["change_pct"]} for s in top_rising],
                "top_falling": [{"name": s["sector_name"], "change_pct": s["change_pct"]} for s in top_falling],
                "heaviest_volume": [{"name": s["sector_name"], "volume_share_pct": s["trading_value_share_pct"]} for s in top_volume],
            }
            logger.info(f"[V3 PREMARKET] 섹터 데이터 로드 완료: {len(sectors)}개 업종")
        except Exception as e:
            logger.warning(f"[V3 PREMARKET] 섹터 데이터 조회 실패 — 레짐 판단에서 제외: {e}")
            self._sector_performance = {}

        market_ctx = {
            "kospi_change_pct": index.kospi_change_pct,
            "kosdaq_change_pct": index.kosdaq_change_pct,
            "kospi_trading_value": index.kospi_trading_value,
            "kosdaq_trading_value": index.kosdaq_trading_value,
            "kospi_advancers": index.kospi_advancers,
            "kospi_decliners": index.kospi_decliners,
            "kosdaq_advancers": index.kosdaq_advancers,
            "kosdaq_decliners": index.kosdaq_decliners,
            "prev_kospi_change_pct": index.prev_kospi_change_pct,
            "prev_kosdaq_change_pct": index.prev_kosdaq_change_pct,
            "prev_kospi_trading_value": index.prev_kospi_trading_value,
            "prev_kosdaq_trading_value": index.prev_kosdaq_trading_value,
            "market_news_summary": market_news_summary,
            "sector_performance": self._sector_performance,
        }
        regime = self._regime_agent.judge(
            market_ctx,
            evaluation_mode=evaluation_mode,
            evaluation_time=now.strftime("%H:%M"),
        )
        self._last_regime = regime
        logger.info(f"[V3] Regime: {regime.regime.value} (확신도 {regime.confidence}%)")

        market_status = {
            "date": self._today,
            "evaluation_mode": evaluation_mode,
            "evaluation_time": now.strftime("%H:%M"),
            "kospi": index.kospi,
            "kosdaq": index.kosdaq,
            "kospi_trading_value": index.kospi_trading_value,
            "kosdaq_trading_value": index.kosdaq_trading_value,
            "kospi_advancers": index.kospi_advancers,
            "kospi_decliners": index.kospi_decliners,
            "kosdaq_advancers": index.kosdaq_advancers,
            "kosdaq_decliners": index.kosdaq_decliners,
            "prev_kospi_change_pct": index.prev_kospi_change_pct,
            "prev_kosdaq_change_pct": index.prev_kosdaq_change_pct,
            "prev_kospi_trading_value": index.prev_kospi_trading_value,
            "prev_kosdaq_trading_value": index.prev_kosdaq_trading_value,
            "status": regime.status.value,
            "regime": regime.regime.value,
            "confidence": regime.confidence,
            "reason": regime.reason,
            "risk_notes": regime.risk_notes,
            "opportunity_mode": regime.opportunity_mode.value,
            "scanner_mode": regime.scanner_mode.value,
        }
        self._save_json("market_status.json", market_status)
        self._notify_market_regime(market_status)
        self.warm_atr_cache()
        return market_status

    def _notify_market_regime(self, market_status: dict) -> None:
        status = market_status.get("status", "UNKNOWN")
        regime = market_status.get("regime", "UNKNOWN")
        confidence = market_status.get("confidence", 0)
        risk_notes = market_status.get("risk_notes") or []
        risk_text = "\n".join(f"- {note}" for note in risk_notes) if risk_notes else "- 특이 리스크 없음"
        reason = str(market_status.get("reason") or "근거 없음")
        if len(reason) > 700:
            reason = reason[:700].rstrip() + "..."

        lines = [
            f"📊 *MQK v3 시장레짐 평가 완료* ({market_status.get('date', self._today)})",
            "",
            f"상태: *{status}*",
            f"레짐: *{regime}*",
            f"확신도: *{confidence}%*",
            f"기회 모드: {market_status.get('opportunity_mode', 'NORMAL')}",
            f"스캐너 모드: {market_status.get('scanner_mode', 'TREND')}",
            "",
            "근거:",
            reason,
            "",
            "리스크 노트:",
            risk_text,
            "",
            f"전일 KOSPI/KOSDAQ: {market_status.get('prev_kospi_change_pct', 0):+.2f}% / {market_status.get('prev_kosdaq_change_pct', 0):+.2f}%",
        ]
        try:
            self._telegram.notify("\n".join(lines))
        except Exception as e:
            logger.warning(f"[V3 시장레짐 알림] 텔레그램 발송 실패: {e}")

    def run_close_review(self) -> None:
        logger.info("[V3 장마감] 거래 복기 시작")
        today_trades = self._journal.get_closed_trades(days=1)
        if not today_trades:
            logger.info("[V3 장마감] 오늘 청산 거래 없음")
            return

        reviews = []
        for trade in today_trades:
            review = self._review_agent.analyze(trade)
            reviews.append(review)
            logger.info(f"복기: {review.ticker} {review.result} {review.pnl_pct:+.2f}%")

        journal_summary = "\n".join(f"- {r.ticker}: {r.result} {r.pnl_pct:+.2f}%" for r in reviews)
        proposals = self._si_agent.suggest(today_trades, journal_summary)
        for p in proposals:
            pid = self._improvement_mgr.save(p)
            logger.info(f"[V3 개선 제안] #{pid} {p.title} → 텔레그램 통보 완료")

    def _save_json(self, filename: str, data: dict) -> None:
        path = self._log_dir / filename
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _append_jsonl(self, filename: str, record: dict) -> None:
        path = self._log_dir / filename
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _estimate_atr(self, ticker: str) -> float:
        if ticker in self._atr_cache:
            return self._atr_cache[ticker]
        bars = self._market_data.get_ohlcv(ticker)
        atr = self._technical.calculate_atr(bars) if bars else 0.0
        self._atr_cache[ticker] = atr
        return atr

    def warm_atr_cache(self) -> None:
        open_pos = self._journal.get_open_positions()
        if not open_pos:
            return
        logger.info(f"[V3 ATR 워밍] 보유 종목 {len(open_pos)}개 ATR 사전 계산")
        for row in open_pos:
            self._estimate_atr(row["ticker"])

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
