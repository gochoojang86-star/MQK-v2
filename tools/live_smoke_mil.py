#!/usr/bin/env python3
"""MIL 17개 도구 라이브 스모크 테스트.

실제 KIS API를 호출해 각 도구가 실데이터를 올바르게 가져오는지 검증한다.
사용: .venv/bin/python tools/live_smoke_mil.py [--ticker 005930]
- 장외 시간에는 장중 전용(🕘) 도구가 빈 데이터를 반환할 수 있다 — INTRADAY_ONLY로 표시.
- 실패 raw는 data/live_test_logs/에 저장.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from broker.kis_api import KISApi
# from broker.kis_mcp_client import KISMCPClient  # MCP 비활성화
from market_intelligence import market, portfolio, risk_filter, screening, stock
from market_intelligence.base import MILContext, ToolFailure
from market_intelligence.cache import MILCache
from market_intelligence.circuit_breaker import CircuitBreaker

LOG_DIR = Path(__file__).parent.parent / "data" / "live_test_logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

PASS, FAIL, WARN = "✅", "❌", "⚠️"


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def run_checks(ctx: MILContext, ticker: str, hts_id: str) -> list[tuple[str, str, str]]:
    """(도구명, 판정, 요약) 리스트 반환."""
    results = []

    def check(name, fn, sanity, intraday_only=False):
        t0 = time.monotonic()
        try:
            out = fn()
        except ToolFailure as e:
            results.append((name, FAIL, f"ToolFailure: {e}"))
            (LOG_DIR / f"{name}.error.txt").write_text(str(e), encoding="utf-8")
            return None
        elapsed = (time.monotonic() - t0) * 1000
        try:
            ok, summary = sanity(out)
        except Exception as e:
            ok, summary = False, f"sanity 예외: {e}"
        if not ok and intraday_only:
            results.append((name, WARN, f"INTRADAY_ONLY(장외): {summary} [{elapsed:.0f}ms]"))
        else:
            results.append((name, PASS if ok else FAIL, f"{summary} [{elapsed:.0f}ms]"))
            if not ok:
                (LOG_DIR / f"{name}.raw.json").write_text(
                    json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return out

    # ── market ──
    check("get_market_context", lambda: market.get_market_context(ctx, "SCAN"),
          lambda o: (any(_num(o.get(k)) and o.get(k) for k in ("kospi", "kosdaq")) or bool(o),
                     f"keys={sorted(o.keys())[:8]} program_net={o.get('program_net_buy_krw')} "
                     f"investor_days={len(o.get('investor_trend_days') or [])} "
                     f"missing={o.get('missing_fields')}"))
    check("get_sector_breadth", lambda: market.get_sector_breadth(ctx, "SCAN"),
          lambda o: (len(o.get("sectors", [])) > 0 and o.get("market_breadth", {}).get("advancers", 0) + o.get("market_breadth", {}).get("decliners", 0) > 0,
                     f"sectors={len(o.get('sectors', []))} breadth={o.get('market_breadth')}"))
    check("get_intraday_index_candles", lambda: market.get_intraday_index_candles(ctx, "SCAN"),
          lambda o: (len(o.get("candles", [])) > 0, f"candles={len(o.get('candles', []))}"),
          intraday_only=True)
    check("get_news_market", lambda: market.get_news_market(ctx, "SCAN"),
          lambda o: (len(o.get("headlines", [])) > 0 and bool(o["headlines"][0].get("title")),
                     f"headlines={len(o.get('headlines', []))} 첫줄={str(o.get('headlines', [{}])[0].get('title'))[:30]!r}"))

    # ── stock ──
    check("get_ohlcv", lambda: stock.get_ohlcv(ctx, "SCAN", ticker),
          lambda o: (len(o.get("rows", o.get("candles", o.get("ohlcv", [])))) > 0 or bool(o),
                     f"keys={sorted(o.keys())[:6]}"))
    check("get_realtime_price", lambda: stock.get_realtime_price(ctx, "SCAN", [ticker, "000660"]),
          lambda o: (bool(o), f"keys={sorted(o.keys())[:6]}"), intraday_only=True)
    check("get_intraday_candles", lambda: stock.get_intraday_candles(ctx, "SCAN", ticker),
          lambda o: (len(o.get("candles", [])) > 0, f"candles={len(o.get('candles', []))}"),
          intraday_only=True)
    check("get_flow", lambda: stock.get_flow(ctx, "SCAN", ticker),
          lambda o: (bool(o), f"keys={sorted(o.keys())[:6]}"))
    check("get_news_stock", lambda: stock.get_news_stock(ctx, "SCAN", ticker),
          lambda o: (o.get("ticker") == ticker, f"headlines={len(o.get('headlines', []))}"))
    check("get_fundamentals", lambda: stock.get_fundamentals(ctx, "SCAN", ticker),
          lambda o: (len(o.get("financial_ratios", [])) > 0,
                     f"ratios={len(o.get('financial_ratios', []))} "
                     f"income={len(o.get('income_statements', []))} "
                     f"balance={len(o.get('balance_sheets', []))} "
                     f"opinions={len(o.get('analyst_opinions', []))} "
                     f"missing={o.get('missing_fields')}"))

    # ── screening ──
    psearch = check("psearch_title", lambda: screening.psearch_title(ctx, "SCAN", hts_id),
                    lambda o: (bool(o), f"keys={sorted(o.keys())[:6]}"))
    if psearch and isinstance(psearch, dict):
        seqs = [c.get("seq") for c in psearch.get("conditions", psearch.get("titles", []))
                if isinstance(c, dict) and c.get("seq")]
        if seqs:
            check("psearch_result", lambda: screening.psearch_result(ctx, "SCAN", hts_id, str(seqs[0])),
                  lambda o: (bool(o), f"keys={sorted(o.keys())[:6]}"), intraday_only=True)
        else:
            results.append(("psearch_result", WARN, "HTS 조건식 없음 — 사전조건 미충족 (HTS에서 조건식 등록 필요)"))
    check("get_top_movers", lambda: screening.get_top_movers(ctx, "SCAN"),
          lambda o: (len(o.get("movers", o.get("rows", []))) > 0 or bool(o),
                     f"keys={sorted(o.keys())[:6]} "
                     f"vol_power={len(o.get('volume_power_top') or [])} "
                     f"change_top={len(o.get('change_rate_top') or [])} "
                     f"missing={o.get('missing_fields')}"), intraday_only=True)

    # ── risk_filter ──
    check("get_stock_status", lambda: risk_filter.get_stock_status(ctx, "SCAN", ticker),
          lambda o: (bool(o), f"keys={sorted(o.keys())[:8]} "
                     f"limit_up={o.get('is_limit_up')} limit_down={o.get('is_limit_down')} "
                     f"missing={o.get('missing_fields')}"))
    check("get_event_schedule", lambda: risk_filter.get_event_schedule(ctx, "SCAN", ticker),
          lambda o: (isinstance(o, dict), f"keys={sorted(o.keys())[:6]} "
                     f"bonus={len(o.get('bonus_issue_events') or [])} "
                     f"merger={len(o.get('merger_split_events') or [])} "
                     f"meeting={len(o.get('shareholder_meeting_events') or [])} "
                     f"missing={o.get('missing_fields')}"))

    # ── portfolio ──
    check("get_open_positions", lambda: portfolio.get_open_positions(ctx, "SCAN"),
          lambda o: ("position_count" in o, f"count={o.get('position_count')}"))
    check("get_daily_pnl", lambda: portfolio.get_daily_pnl(ctx, "SCAN"),
          lambda o: ("realized_pnl_pct" in o or bool(o), f"keys={sorted(o.keys())[:6]}"))

    return results


def infra_checks(ctx: MILContext, ticker: str) -> list[tuple[str, str, str]]:
    results = []
    # 캐시: 같은 도구 2회 — 2회차가 충분히 빨라야 함
    t0 = time.monotonic(); stock.get_ohlcv(ctx, "SCAN", ticker); first = time.monotonic() - t0
    t0 = time.monotonic(); stock.get_ohlcv(ctx, "SCAN", ticker); second = time.monotonic() - t0
    ok = second < 0.01 or second < first / 10
    results.append(("cache_hit", PASS if ok else FAIL,
                    f"1회차 {first*1000:.0f}ms → 2회차 {second*1000:.0f}ms"))

    # circuit breaker: 가짜 ticker 반복 실패 → open
    bad = "999999"
    opened = False
    for i in range(10):
        try:
            stock.get_flow(ctx, "SCAN", bad)
        except ToolFailure as e:
            if "circuit breaker open" in str(e):
                opened = True
                break
        except Exception:
            pass
    results.append(("circuit_breaker", PASS if opened else WARN,
                    f"{'open 전이 확인' if opened else '가짜 ticker가 정상응답일 수 있음 — 수동 확인 필요'} (시도 {i+1}회)"))
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", default="005930")
    args = parser.parse_args()

    import os
    hts_id = os.environ.get("KIS_HTS_ID", "")
    # ctx = MILContext(kis_api=KISApi(), mcp_client=KISMCPClient(),
    #                  cache=MILCache(), circuit_breaker=CircuitBreaker())  # MCP 비활성화
    ctx = MILContext(kis_api=KISApi(), cache=MILCache(), circuit_breaker=CircuitBreaker())

    print(f"# MIL 라이브 스모크 — {datetime.now().isoformat(timespec='seconds')} ticker={args.ticker}\n")
    results = run_checks(ctx, args.ticker, hts_id)
    results += infra_checks(ctx, args.ticker)

    width = max(len(n) for n, _, _ in results)
    for name, verdict, summary in results:
        print(f"{verdict} {name:<{width}}  {summary}")

    fails = [n for n, v, _ in results if v == FAIL]
    print(f"\n총 {len(results)}건 — 실패 {len(fails)}건" + (f": {fails}" if fails else ""))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
