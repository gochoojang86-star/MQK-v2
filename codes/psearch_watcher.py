"""조건검색식 편입 감시 (유사 웹훅).

KIS OpenAPI에는 조건검색 실시간 채널이 없으므로(REST 2종뿐), psearch_result를
짧은 주기로 폴링해 직전 결과와 diff하여 "신규 편입 이벤트"를 만든다.

- ep (MQK2/EP/돌파): 첫 폴부터 이벤트 — 아침 편입 자체가 시그널
- base (MQK1/주도주 등): 첫 폴은 조용히 시드(개장 시점 대량 알림 방지), 이후 diff 이벤트
- reversal (MQK3/낙주/폭락): 알림만 — 진입은 late_intraday 전용이라 watchlist 병합 금지
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path

from market_intelligence import screening
from market_intelligence.base import MILContext, ToolFailure
from market_intelligence.cache import MILCache

logger = logging.getLogger("mqk_v3_psearch_watcher")

_SEEN_PATH = Path(__file__).parent.parent / "data" / "psearch_seen.json"


def classify_condition(name: str) -> str:
    """조건식 이름으로 종류 분류 (scan 프롬프트의 플레이북과 동일 규칙)."""
    n = str(name or "")
    if any(k in n for k in ("낙주", "폭락", "MQK3")):
        return "reversal"
    if any(k in n for k in ("EP", "돌파", "MQK2")):
        return "ep"
    return "base"


def load_seen(path: Path = _SEEN_PATH, today: str | None = None) -> dict:
    today = today or datetime.now().strftime("%Y-%m-%d")
    default = {"date": today, "seen": {}, "seeded": []}
    if not path.exists():
        return default
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"[psearch_watcher] seen 상태 손상 — 초기화: {e}")
        return default
    if state.get("date") != today:
        return default
    state.setdefault("seen", {})
    state.setdefault("seeded", [])
    return state


def save_seen(state: dict, path: Path = _SEEN_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def poll_new_entries(ctx: MILContext, hts_id: str, seen_state: dict) -> list[dict]:
    """모든 조건식을 1회 폴링해 신규 편입 이벤트 목록을 반환한다.

    seen_state는 in-place로 갱신된다 (호출부가 처리 성공 후 save_seen).
    폴링 결과가 캐시에 갇히지 않도록 호출 전 ctx.cache를 새로 끼우는 것은 호출부 책임.
    """
    try:
        titles = screening.psearch_title(ctx, "INTRADAY", hts_id).get("conditions", [])
    except ToolFailure as e:
        logger.warning(f"[psearch_watcher] 조건식 목록 조회 실패: {e}")
        return []

    events: list[dict] = []
    for cond in titles:
        seq, name = str(cond.get("seq", "")), str(cond.get("name", ""))
        if not seq:
            continue
        kind = classify_condition(name)
        try:
            result = screening.psearch_result(ctx, "INTRADAY", hts_id, seq)
        except ToolFailure as e:
            logger.warning(f"[psearch_watcher] {name}(seq={seq}) 조회 실패: {e}")
            continue

        tickers = {c.get("ticker"): c for c in result.get("candidates", []) if c.get("ticker")}
        seen: set = set(seen_state["seen"].get(seq, []))

        first_poll = seq not in seen_state["seeded"]
        if first_poll:
            seen_state["seeded"].append(seq)
            if kind != "ep":
                # base/reversal은 개장 시점 기존 구성종목을 조용히 베이스라인으로
                seen_state["seen"][seq] = sorted(set(tickers) | seen)
                continue

        new_tickers = [t for t in tickers if t not in seen]
        for t in new_tickers:
            c = tickers[t]
            events.append({
                "seq": seq, "condition_name": name, "kind": kind,
                "ticker": t, "name": c.get("name", t),
                "price": c.get("price"), "change_pct": c.get("change_pct"),
                "trading_value": c.get("trading_value"),
            })
        seen_state["seen"][seq] = sorted(set(tickers) | seen)
    return events


def partition_entries(events: list[dict]) -> tuple[list[str], bool]:
    """이벤트를 (watchlist 병합 대상 티커, LLM 트리거 필요 여부)로 분해.

    reversal은 병합/트리거 제외 — 진입은 late_intraday 전용 (알림은 전체 발송).
    """
    merge = [e["ticker"] for e in events if e["kind"] in ("ep", "base")]
    trigger = bool(merge)
    return merge, trigger


def format_alert(events: list[dict]) -> str:
    lines = ["🔔 *조건검색 신규 편입*"]
    for e in events:
        chg = e.get("change_pct")
        chg_s = f" {chg:+.1f}%" if isinstance(chg, (int, float)) else ""
        tail = " (낙주 — late 슬롯에서 평가)" if e["kind"] == "reversal" else ""
        lines.append(f"- [{e['condition_name']}] {e['name']}({e['ticker']}){chg_s}{tail}")
    return "\n".join(lines)


def fresh_cache(ctx: MILContext) -> None:
    """폴링 결과가 직전 캐시에 갇히지 않도록 캐시 교체."""
    ctx.cache = MILCache()
