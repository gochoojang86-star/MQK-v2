"""실데이터 연동 테스트 — 신규 도구 3종 + kw_psearch.

실행: python tools/test_new_tools_live.py
장 중/외 무관하게 실행 가능. 실패는 ✗, 성공은 ✓, 데이터 없음은 ○ 로 표시.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from broker.kiwoom_api import KiwoomApi, KiwoomConfig
from broker.kis_api import KISApi, KISConfig
from market_intelligence.base import MILContext
from market_intelligence import screening


def _ctx() -> MILContext:
    kis = KISApi(KISConfig())
    kw = KiwoomApi(KiwoomConfig())
    return MILContext(kis_api=kis, kiwoom_api=kw)


def _print_result(name: str, result: dict) -> None:
    missing = result.get("missing_fields", [])
    if missing:
        cred_miss = all("credentials not configured" in str(m) for m in missing)
        if cred_miss:
            print(f"  ○ {name}: 키움 API 미설정 (missing={missing})")
        else:
            print(f"  ✗ {name}: 오류 — {missing}")
        return
    data_keys = [k for k in result if k not in ("note", "missing_fields")]
    counts = {}
    for k in data_keys:
        v = result[k]
        counts[k] = len(v) if isinstance(v, list) else ("None" if v is None else str(v)[:50])
    print(f"  ✓ {name}: {counts}")
    if result.get("note"):
        print(f"    note: {result['note']}")


# ── [1] 업종별투자자순매수 ────────────────────────────────────────────────
def test_sector_investor_flow(ctx: MILContext) -> None:
    print("\n[1] get_sector_investor_flow (ka10051 업종별투자자순매수)")
    result = screening.get_sector_investor_flow(ctx, "SCAN")
    _print_result("sector_investor_flow", result)
    sectors = result.get("sectors") or []
    if sectors:
        print("  ▶ 외인+기관 합계 상위 5 섹터:")
        for s in sectors[:5]:
            both_pos = "★" if s["institution_net"] > 0 and s["foreign_net"] > 0 else " "
            print(f"    {both_pos} {s['sector_name']:22s}  기관:{s['institution_net']:+12,.0f}  외인:{s['foreign_net']:+12,.0f}  등락:{s['change_pct']:+.2f}%")


# ── [2] 호가잔량급증 ─────────────────────────────────────────────────────
def test_bid_queue_surge(ctx: MILContext) -> None:
    print("\n[2] get_bid_queue_surge (ka10021 호가잔량급증)")
    result = screening.get_bid_queue_surge(ctx, "SCAN")
    _print_result("bid_queue_surge", result)
    stocks = result.get("stocks") or []
    if stocks:
        print("  ▶ 급증률 상위 5 종목:")
        for s in stocks[:5]:
            print(f"    {s['ticker']} {s['name']:16s}  급증률:{s['surge_rate_pct']:+8.1f}%  총매수:{s['total_bid_qty']:,.0f}")


# ── [3] 키움 조건검색 목록 ────────────────────────────────────────────────
def test_kw_psearch_title(ctx: MILContext) -> None:
    print("\n[3] kw_psearch_title (ka10171 조건검색 목록)")
    result = screening.kw_psearch_title(ctx, "SCAN")
    _print_result("kw_psearch_title", result)
    conditions = result.get("conditions") or []
    if conditions:
        print(f"  ▶ 저장된 조건식 {len(conditions)}개:")
        for c in conditions[:5]:
            print(f"    seq={c['seq']}  {c['name']}")
    elif result.get("note"):
        print(f"  ▶ {result['note']}")


# ── [4] 키움 조건검색 결과 ────────────────────────────────────────────────
def test_kw_psearch_result(ctx: MILContext, seq: str) -> None:
    print(f"\n[4] kw_psearch_result (ka10172 seq={seq})")
    result = screening.kw_psearch_result(ctx, "SCAN", seq=seq)
    _print_result("kw_psearch_result", result)
    candidates = result.get("candidates") or []
    if candidates:
        print(f"  ▶ 결과 {len(candidates)}종목 (상위 5):")
        for c in candidates[:5]:
            print(f"    {c['ticker']} {c['name']:16s}  등락:{c['change_pct']:+.2f}%  거래량:{c['volume']:,.0f}")
    elif result.get("note"):
        print(f"  ▶ {result['note']}")


# ── Raw API 직접 확인 ────────────────────────────────────────────────────
def test_kiwoom_raw(kw: KiwoomApi) -> None:
    print("\n[0] Kiwoom API 직접 연결 확인")
    if not kw.available:
        print("  ✗ KIWOOM_APP_KEY / KIWOOM_SECRET_KEY 미설정")
        return

    # sector_investor_flow
    try:
        raw = kw.sector_investor_flow()
        rows = len(raw.get("inds_netprps") or [])
        print(f"  ✓ sector_investor_flow: return_code={raw.get('return_code')}, 업종수={rows}")
    except Exception as e:
        print(f"  ✗ sector_investor_flow: {e}")

    # bid_queue_surge (코스피)
    try:
        raw = kw.bid_queue_surge(mrkt_tp="001")
        rows = len(raw.get("bid_req_sdnin") or [])
        print(f"  ✓ bid_queue_surge(코스피): return_code={raw.get('return_code')}, 종목수={rows}")
    except Exception as e:
        print(f"  ✗ bid_queue_surge: {e}")

    # bid_queue_surge (코스닥)
    try:
        raw = kw.bid_queue_surge(mrkt_tp="002")
        rows = len(raw.get("bid_req_sdnin") or [])
        print(f"  ✓ bid_queue_surge(코스닥): return_code={raw.get('return_code')}, 종목수={rows}")
    except Exception as e:
        print(f"  ✗ bid_queue_surge(코스닥): {e}")

    # search_list (WebSocket)
    try:
        raw = kw.search_list()
        data = raw.get("data") or []
        code = raw.get("return_code", -1)
        if code == 0:
            print(f"  ✓ search_list(WS): return_code={code}, 조건식={len(data)}개")
            return data  # seq 목록 반환
        else:
            print(f"  ✗ search_list(WS): return_code={code}, msg={raw.get('return_msg', '')[:80]}")
    except Exception as e:
        print(f"  ✗ search_list(WS): {e}")
    return []


if __name__ == "__main__":
    print("=" * 64)
    print("MQK v3 신규 도구 실데이터 테스트")
    print("=" * 64)

    kw = KiwoomApi()
    conditions = test_kiwoom_raw(kw) or []

    ctx = _ctx()
    test_sector_investor_flow(ctx)
    test_bid_queue_surge(ctx)
    test_kw_psearch_title(ctx)

    if conditions:
        first_seq = str(conditions[0].get("seq", "")).strip()
        if first_seq:
            test_kw_psearch_result(ctx, first_seq)
    else:
        print("\n[4] kw_psearch_result: 영웅문4에 조건식 없어서 스킵")

    print("\n" + "=" * 64)
    print("완료")
