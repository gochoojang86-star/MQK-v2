"""6개 종목 실데이터 TradingAgent INTRADAY 분석 테스트.

실행: python tools/test_analysis_live.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from agents.trading_agent import TradingAgent, TradingPhase, build_context
from broker.kis_api import KISApi, KISConfig
from broker.kiwoom_api import KiwoomApi, KiwoomConfig
from market_intelligence.base import MILContext
from market_intelligence.cache import MILCache
from market_intelligence.circuit_breaker import CircuitBreaker
from agents.regime_agent import load_last_regime, _LAST_REGIME_PATH

TICKERS = [
    "005930",  # 삼성전자
    "000660",  # SK하이닉스
    "005380",  # 현대차
    "066570",  # LG전자
    "042660",  # 한화오션
    "047040",  # 대우건설
]

TICKER_NAMES = {
    "005930": "삼성전자",
    "000660": "SK하이닉스",
    "005380": "현대차",
    "066570": "LG전자",
    "042660": "한화오션",
    "047040": "대우건설",
}


def main():
    print("=" * 68)
    print("MQK v3 TradingAgent INTRADAY 분석")
    print(f"대상: {', '.join(TICKER_NAMES[t] for t in TICKERS)}")
    print("=" * 68)

    # API 초기화
    kis = KISApi(KISConfig())
    kw = KiwoomApi(KiwoomConfig())
    mil = MILContext(
        kis_api=kis,
        kiwoom_api=kw,
        cache=MILCache(),
        circuit_breaker=CircuitBreaker(),
    )

    # 저장된 레짐 로드
    regime = load_last_regime(path=_LAST_REGIME_PATH)
    if regime is None:
        print("⚠ last_regime.json 없음 — YELLOW/SIDEWAYS 기본값 사용")
        regime = {
            "status": "YELLOW",
            "regime": "SIDEWAYS",
            "confidence": 70,
            "risk_guidance": {
                "buy_confidence_threshold": 75,
                "risk_per_trade_pct": 0.4,
                "max_positions": 4,
                "min_trading_value_krw": 10_000_000_000,
            },
        }
    else:
        print(f"✓ 레짐: {regime.get('status')} / {regime.get('regime')} (확신도 {regime.get('confidence')}%)")
        print(f"  risk_guidance: {json.dumps(regime.get('risk_guidance', {}), ensure_ascii=False)}")

    risk_guidance = regime.get("risk_guidance", {})

    # 컨텍스트 빌드 (보유 없는 순수 분석 모드)
    context = build_context(
        phase=TradingPhase.INTRADAY,
        trading_date=__import__("datetime").datetime.now().strftime("%Y-%m-%d"),
        regime={
            "status": regime.get("status"),
            "regime": regime.get("regime"),
            "confidence": regime.get("confidence"),
        },
        drift_status="STABLE",
        risk_guidance=risk_guidance,
        portfolio_snapshot={
            "positions": [],
            "position_count": 0,
            "available_cash_krw": 10_000_000,
            "estimated_total_capital_krw": 10_000_000,
            "cash_ratio_pct": 100.0,
            "invested_ratio_pct": 0.0,
            "positions_left_is_soft": True,
        },
        daily_pnl={
            "realized_pnl_pct": 0.0,
            "realized_pnl_krw": 0.0,
            "total_eval_amt": 10_000_000,
        },
        risk_budget_remaining={
            "positions_left": risk_guidance.get("max_positions", 4),
            "monitoring_slots": 6,
            "daily_loss_remaining_pct": 2.0,
        },
        watchlist=TICKERS,
        exploration_policy={
            "allow_intraday_discovery": False,
            "max_new_tickers": 0,
            "require_strong_evidence": True,
            "discovery_priority": "watchlist_first_then_new_leaders",
        },
        context_timestamps={
            "regime": regime.get("timestamp", ""),
            "now": __import__("datetime").datetime.now().isoformat(),
        },
    )

    # 도구 호출을 추적하기 위해 execute_tool 패치
    import agents.trading_agent as agent_module
    original_execute = agent_module.TradingAgent._execute_tool
    tool_calls_log = []

    def traced_execute(self, phase, tool_name, tool_args):
        print(f"  → {tool_name}({tool_args if tool_args else ''})")
        result = original_execute(self, phase, tool_name, tool_args)
        missing = result.get("missing_fields") if isinstance(result, dict) else None
        if missing:
            print(f"    ⚠ missing={missing}")
        tool_calls_log.append(tool_name)
        return result

    agent_module.TradingAgent._execute_tool = traced_execute

    print("\n에이전트 실행 중... (ReAct 루프, 실 API 호출)\n")
    agent = TradingAgent(mil=mil)
    result = agent.run(TradingPhase.INTRADAY, context)

    # 복원
    agent_module.TradingAgent._execute_tool = original_execute
    print(f"\n[도구 호출 순서] {' → '.join(tool_calls_log)}")

    # 결과 출력
    print("\n" + "=" * 68)
    print("최종 결과")
    print("=" * 68)
    print(json.dumps(result, ensure_ascii=False, indent=2))

    # proposals 요약
    proposals = result.get("proposals", [])
    if proposals:
        print("\n▶ 매매 proposal 요약:")
        for p in proposals:
            ticker = p.get("ticker", "")
            name = TICKER_NAMES.get(ticker, ticker)
            side = p.get("side", "")
            conf = p.get("confidence", 0)
            setup = p.get("setup", "")
            stop = p.get("stop_loss_price", 0)
            reason = p.get("reason", "")[:120]
            print(f"  {'BUY 🟢' if side == 'BUY' else 'SELL 🔴' if side == 'SELL' else side} {name}({ticker})  확신도:{conf}%  setup:{setup}")
            print(f"    손절:{stop:,.0f}원")
            print(f"    근거: {reason}")
    else:
        action = result.get("action", "NO_TRADE")
        reason = result.get("reason", "")
        print(f"\n▶ 결론: {action}")
        print(f"  사유: {reason[:200]}")


if __name__ == "__main__":
    main()
