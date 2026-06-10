"""포트폴리오 도구 2개: get_open_positions, get_daily_pnl"""
from __future__ import annotations

from market_intelligence.base import MILContext


def get_open_positions(ctx: MILContext, phase: str) -> dict:
    """보유 종목, 수량, 평균단가, 평가손익."""

    def fetch():
        raw = ctx.kis_api.get_balance()
        positions = []
        for row in raw.get("output1", []):
            qty = _to_float(row.get("hldg_qty"))
            if qty <= 0:
                continue
            positions.append({
                "ticker": row.get("pdno"),
                "name": row.get("prdt_name"),
                "quantity": int(qty),
                "avg_price": _to_float(row.get("pchs_avg_pric")),
                "current_price": _to_float(row.get("prpr")),
                "eval_pnl": _to_float(row.get("evlu_pfls_amt")),
                "eval_pnl_pct": _to_float(row.get("evlu_pfls_rt")),
            })
        return {"positions": positions, "position_count": len(positions)}

    return ctx.cached_call("get_open_positions", phase, {}, fetch)


def get_daily_pnl(ctx: MILContext, phase: str) -> dict:
    """당일 실현손익 (금액 + 총평가금액 대비 %)."""

    def fetch():
        raw = ctx.kis_api.get_balance()
        summary_rows = raw.get("output2", [])
        summary = summary_rows[0] if summary_rows else {}
        total_eval = _to_float(summary.get("tot_evlu_amt"))
        realized_pnl = _to_float(summary.get("rlzt_pfls"))
        realized_pct = round(realized_pnl / total_eval * 100, 2) if total_eval else 0.0
        return {
            "realized_pnl_krw": realized_pnl,
            "realized_pnl_pct": realized_pct,
            "total_eval_amt": total_eval,
        }

    return ctx.cached_call("get_daily_pnl", phase, {}, fetch)


def _to_float(value) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(str(value).replace(",", ""))
    except (ValueError, TypeError):
        return 0.0
