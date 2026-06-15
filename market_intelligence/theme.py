"""키움 REST 기반 테마 스캔 보강 도구."""
from __future__ import annotations

from typing import Any

from broker.kiwoom_api import KiwoomApi
from market_intelligence.base import MILContext


def get_theme_candidates(
    ctx: MILContext,
    phase: str,
    topn_themes: int = 5,
    theme_date_tp: str = "10",
    component_date_tp: str = "2",
) -> dict:
    """강한 테마 상위 그룹과 구성 종목 후보를 반환한다.

    거래대금은 키움 응답의 현재가 × 누적거래량으로 근사한다.
    """

    def fetch():
        client = ctx.kiwoom_api or KiwoomApi()
        if not client.available:
            return {"themes": [], "candidates": [], "source": "kiwoom_unavailable"}

        raw_groups = client.theme_groups(qry_tp="0", date_tp=theme_date_tp, flu_pl_amt_tp="1", stex_tp="1")
        groups = raw_groups.get("thema_grp", []) or []
        normalized_groups = [
            {
                "theme_code": str(row.get("thema_grp_cd") or ""),
                "theme_name": row.get("thema_nm"),
                "theme_change_pct": _to_float(row.get("flu_rt")),
                "theme_period_return_pct": _to_float(row.get("dt_prft_rt")),
                "stock_count": _to_int(row.get("stk_num")),
                "rising_count": _to_int(row.get("rising_stk_num")),
                "falling_count": _to_int(row.get("fall_stk_num")),
                "main_stock": row.get("main_stk"),
            }
            for row in groups
            if row.get("thema_grp_cd")
        ]
        normalized_groups.sort(
            key=lambda x: (x["theme_period_return_pct"], x["theme_change_pct"], x["rising_count"]),
            reverse=True,
        )

        picked_themes = normalized_groups[:topn_themes]
        candidates: list[dict[str, Any]] = []
        for group in picked_themes:
            comp = client.theme_components(group["theme_code"], date_tp=component_date_tp, stex_tp="1")
            for row in comp.get("thema_comp_stk", []) or []:
                ticker = str(row.get("stk_cd") or "").strip()
                if len(ticker) != 6 or not ticker.isdigit():
                    continue
                current_price = _to_float(row.get("cur_prc"))
                volume = _to_float(row.get("acc_trde_qty"))
                candidates.append(
                    {
                        "ticker": ticker,
                        "name": row.get("stk_nm"),
                        "price": current_price,
                        "change_pct": _to_float(row.get("flu_rt")),
                        "volume": volume,
                        "trading_value": current_price * volume,
                        "theme_code": group["theme_code"],
                        "theme_name": group["theme_name"],
                        "theme_change_pct": group["theme_change_pct"],
                        "theme_period_return_pct": group["theme_period_return_pct"],
                        "source": "kiwoom_theme",
                    }
                )

        candidates.sort(
            key=lambda x: (x["trading_value"], x["theme_period_return_pct"], x["change_pct"]),
            reverse=True,
        )
        return {
            "themes": picked_themes,
            "candidates": candidates,
            "source": "kiwoom_theme",
        }

    return ctx.cached_call(
        "get_theme_candidates",
        phase,
        {
            "topn_themes": topn_themes,
            "theme_date_tp": theme_date_tp,
            "component_date_tp": component_date_tp,
        },
        fetch,
    )


def _to_float(value) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def _to_int(value) -> int:
    return int(_to_float(value))
