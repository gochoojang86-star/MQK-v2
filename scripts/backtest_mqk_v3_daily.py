#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import sys
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest.backtest_engine import BacktestEngine, BacktestTrade
from backtest.historical_loader import HistoricalLoader
from broker.kis_api import KISApi
from codes.market_data import MarketSnapshot, OHLCVBar


BASE_DIR = Path(__file__).resolve().parent.parent
REPORT_DIR = BASE_DIR / "reports"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

TOP_FLUCTUATION = 100
TOP_TRADING_VALUE = 100
LOOKBACK_BARS = 260
INITIAL_CAPITAL = 100_000_000.0
MAX_POSITIONS = 5
MAX_NEW_POSITIONS_PER_DAY = 2
POSITION_SIZE_PCT = 0.20
MIN_TRADING_VALUE_KRW = 10_000_000_000
MIN_MARKET_CAP_KRW = 300_000_000_000
MAX_GAP_UP_PCT = 5.0


@dataclass
class Candidate:
    ticker: str
    date: str
    setup: str
    score: float
    close: float
    low: float
    change_pct: float
    trading_value: float
    avg_trading_value_20: float


@dataclass
class Position:
    ticker: str
    setup: str
    entry_date: str
    entry_price: float
    quantity: int
    stop_loss_price: float
    target_price: float
    max_holding_days: int
    bars_held: int = 0


@dataclass
class PendingEntry:
    entry_date: str
    ticker: str
    setup: str
    entry_price: float
    quantity: int
    stop_loss_price: float
    target_price: float
    max_holding_days: int


def _seed_universe(kis: KISApi) -> list[str]:
    rows = kis.get_fluctuation_rank(limit=TOP_FLUCTUATION) + kis.get_trading_value_rank(limit=TOP_TRADING_VALUE)
    tickers: list[str] = []
    seen: set[str] = set()
    for row in rows:
        ticker = str(
            row.get("ticker")
            or row.get("stck_shrn_iscd")
            or row.get("mksc_shrn_iscd")
            or ""
        ).strip()
        if len(ticker) == 6 and ticker not in seen:
            seen.add(ticker)
            tickers.append(ticker)
    return tickers


def _load_universe_meta(universe_path: Path) -> dict[str, dict[str, str]]:
    meta: dict[str, dict[str, str]] = {}
    with universe_path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            ticker = str(row.get("ticker") or "").strip()
            if len(ticker) == 6:
                meta[ticker] = row
    return meta


def _snapshot_meta(kis: KISApi, tickers: list[str]) -> dict[str, MarketSnapshot]:
    snapshots: dict[str, MarketSnapshot] = {}
    for ticker in tickers:
        try:
            row = kis.get_snapshot(ticker)
            snapshots[ticker] = MarketSnapshot(
                ticker=ticker,
                name=str(row.get("name") or row.get("hts_kor_isnm") or ticker),
                current_price=float(str(row.get("current_price") or row.get("stck_prpr") or 0).replace(",", "")),
                change_pct=float(str(row.get("change_pct") or row.get("prdy_ctrt") or 0).replace(",", "")),
                volume=int(float(str(row.get("volume") or row.get("acml_vol") or 0).replace(",", ""))),
                trading_value=float(str(row.get("trading_value") or row.get("acml_tr_pbmn") or 0).replace(",", "")),
                foreign_net=0.0,
                institution_net=0.0,
                market_cap=float(str(row.get("market_cap") or row.get("hts_avls") or 0).replace(",", "")) * (
                    100_000_000 if row.get("hts_avls") not in (None, "", 0, "0") and row.get("market_cap") in (None, "") else 1
                ),
                sector=str(row.get("sector") or row.get("bstp_kor_isnm") or ""),
            )
        except Exception:
            continue
    return snapshots


def _load_bars(loader: HistoricalLoader, tickers: list[str]) -> dict[str, list[OHLCVBar]]:
    bars_by_ticker: dict[str, list[OHLCVBar]] = {}
    for ticker in tickers:
        bars = _load_full_history(loader, ticker, LOOKBACK_BARS)
        bars = [b for b in bars if b.date and b.close > 0 and b.volume >= 0]
        if len(bars) >= 180:
            bars.sort(key=lambda b: b.date)
            bars_by_ticker[ticker] = bars[-LOOKBACK_BARS:]
    return bars_by_ticker


def _load_full_history(loader: HistoricalLoader, ticker: str, target_bars: int) -> list[OHLCVBar]:
    cached = loader.load_cache(ticker)
    if len(cached) >= target_bars:
        return cached[-target_bars:]

    kis = getattr(loader, "_kis", None)
    if kis is None:
        return cached

    rows_by_date: dict[str, OHLCVBar] = {bar.date: bar for bar in cached if bar.date}
    end_date = datetime.now()
    loops = 0

    while len(rows_by_date) < target_bars and loops < 6:
        loops += 1
        batch = _fetch_ohlcv_batch(kis, ticker, end_date=end_date, days=420)
        if not batch:
            break
        for bar in batch:
            rows_by_date[bar.date] = bar
        oldest = min(batch, key=lambda b: b.date)
        try:
            end_date = datetime.strptime(oldest.date, "%Y%m%d") - timedelta(days=1)
        except ValueError:
            break
        if len(batch) < 50:
            break

    merged = sorted(rows_by_date.values(), key=lambda b: b.date)
    if merged:
        loader.save_cache(ticker, merged)
    return merged[-target_bars:]


def _fetch_ohlcv_batch(kis: KISApi, ticker: str, end_date: datetime, days: int) -> list[OHLCVBar]:
    url = f"{kis._base_url_for(kis._data_mode)}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
    start_date = end_date - timedelta(days=days)
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": ticker,
        "FID_INPUT_DATE_1": start_date.strftime("%Y%m%d"),
        "FID_INPUT_DATE_2": end_date.strftime("%Y%m%d"),
        "FID_PERIOD_DIV_CODE": "D",
        "FID_ORG_ADJ_PRC": "0",
    }
    try:
        resp = kis._get_with_retry(
            url,
            headers=kis._headers("FHKST03010100", mode=kis._data_mode),
            params=params,
            timeout=10,
        )
        output = resp.json().get("output2", []) or []
    except Exception:
        return []

    bars: list[OHLCVBar] = []
    for row in output:
        bar = loader_coerce(row)
        if bar is not None:
            bars.append(bar)
    return bars


def loader_coerce(row: dict[str, Any]) -> OHLCVBar | None:
    try:
        def f(k: str, *alts: str) -> float:
            for key in (k, *alts):
                value = row.get(key)
                if value not in (None, ""):
                    return float(str(value).replace(",", ""))
            return 0.0

        return OHLCVBar(
            date=str(row.get("stck_bsop_date") or row.get("date") or ""),
            open=f("stck_oprc", "open"),
            high=f("stck_hgpr", "high"),
            low=f("stck_lwpr", "low"),
            close=f("stck_clpr", "close"),
            volume=int(f("acml_vol", "volume")),
            trading_value=f("acml_tr_pbmn", "trading_value"),
        )
    except Exception:
        return None


def _mean(values: list[float]) -> float:
    return statistics.mean(values) if values else 0.0


def _build_daily_candidates(
    bars_by_ticker: dict[str, list[OHLCVBar]],
    snapshots: dict[str, MarketSnapshot],
) -> tuple[dict[str, list[Candidate]], dict[str, list[str]]]:
    per_date: dict[str, list[Candidate]] = defaultdict(list)
    availability: dict[str, list[str]] = defaultdict(list)

    for ticker, bars in bars_by_ticker.items():
        snap = snapshots.get(ticker)
        if snap is None or snap.market_cap < MIN_MARKET_CAP_KRW:
            continue

        for i in range(60, len(bars)):
            bar = bars[i]
            prev = bars[i - 1]
            trailing20 = bars[i - 20:i]
            trailing5 = bars[i - 5:i]
            if len(trailing20) < 20 or len(trailing5) < 5:
                continue

            avg_tv20 = _mean([b.trading_value for b in trailing20])
            avg_tv5 = _mean([b.trading_value for b in trailing5])
            if avg_tv20 <= 0:
                continue

            change_pct = ((bar.close - prev.close) / prev.close) * 100 if prev.close > 0 else 0.0
            high20 = max(b.high for b in trailing20)
            close_to_high = high20 > 0 and bar.close >= high20 * 0.98
            tv_ratio20 = bar.trading_value / avg_tv20 if avg_tv20 > 0 else 0.0
            tv_ratio5 = bar.trading_value / avg_tv5 if avg_tv5 > 0 else 0.0

            availability[bar.date].append(ticker)

            if bar.trading_value >= MIN_TRADING_VALUE_KRW and change_pct >= 5.0 and tv_ratio20 >= 2.0 and close_to_high:
                score = change_pct * 1.4 + min(tv_ratio20, 5.0) * 8.0
                per_date[bar.date].append(
                    Candidate(
                        ticker=ticker,
                        date=bar.date,
                        setup="TREND",
                        score=score,
                        close=bar.close,
                        low=bar.low,
                        change_pct=change_pct,
                        trading_value=bar.trading_value,
                        avg_trading_value_20=avg_tv20,
                    )
                )

            if bar.trading_value >= MIN_TRADING_VALUE_KRW and change_pct <= -5.0 and tv_ratio5 >= 2.0:
                score = abs(change_pct) * 1.2 + min(tv_ratio5, 6.0) * 7.0
                per_date[bar.date].append(
                    Candidate(
                        ticker=ticker,
                        date=bar.date,
                        setup="REVERSAL",
                        score=score,
                        close=bar.close,
                        low=bar.low,
                        change_pct=change_pct,
                        trading_value=bar.trading_value,
                        avg_trading_value_20=avg_tv20,
                    )
                )

    for date_key, rows in per_date.items():
        rows.sort(key=lambda c: (c.setup == "TREND", c.score, c.trading_value), reverse=True)
    return per_date, availability


def _make_date_index(bars_by_ticker: dict[str, list[OHLCVBar]]) -> dict[str, dict[str, int]]:
    index: dict[str, dict[str, int]] = {}
    for ticker, bars in bars_by_ticker.items():
        index[ticker] = {bar.date: i for i, bar in enumerate(bars)}
    return index


def _bar_on_or_after(bars: list[OHLCVBar], start_idx: int) -> OHLCVBar | None:
    if start_idx < len(bars):
        return bars[start_idx]
    return None


def _simulate(
    bars_by_ticker: dict[str, list[OHLCVBar]],
    candidates_by_date: dict[str, list[Candidate]],
    date_index: dict[str, dict[str, int]],
) -> tuple[list[BacktestTrade], dict[str, Any]]:
    engine = BacktestEngine()
    all_dates = sorted({bar.date for bars in bars_by_ticker.values() for bar in bars if bar.date})
    open_positions: list[Position] = []
    pending_entries: dict[str, list[PendingEntry]] = defaultdict(list)
    trades: list[BacktestTrade] = []
    cash = INITIAL_CAPITAL
    equity_curve: list[dict[str, Any]] = []
    setup_stats: dict[str, dict[str, float]] = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0, "days": 0.0})

    for date_key in all_dates:
        todays_entries = pending_entries.pop(date_key, [])
        for entry in todays_entries:
            cost = entry.entry_price * entry.quantity
            if cost > cash:
                continue
            cash -= cost
            open_positions.append(
                Position(
                    ticker=entry.ticker,
                    setup=entry.setup,
                    entry_date=entry.entry_date,
                    entry_price=entry.entry_price,
                    quantity=entry.quantity,
                    stop_loss_price=entry.stop_loss_price,
                    target_price=entry.target_price,
                    max_holding_days=entry.max_holding_days,
                )
            )

        remaining_positions: list[Position] = []
        for pos in open_positions:
            bars = bars_by_ticker[pos.ticker]
            idx = date_index[pos.ticker].get(date_key)
            if idx is None:
                remaining_positions.append(pos)
                continue

            bar = bars[idx]
            pos.bars_held += 1
            exit_reason = None
            exit_price = bar.close

            if bar.low <= pos.stop_loss_price:
                exit_reason = "STOP_LOSS"
                exit_price = pos.stop_loss_price
            elif bar.high >= pos.target_price:
                exit_reason = "TARGET"
                exit_price = pos.target_price
            elif pos.bars_held >= pos.max_holding_days:
                exit_reason = "TIME_EXIT"
                exit_price = bar.close
            elif pos.setup == "TREND" and bar.close < pos.entry_price * 0.97:
                exit_reason = "PRICE_FAIL"
                exit_price = bar.close
            elif pos.setup == "REVERSAL" and bar.close < pos.entry_price * 0.98:
                exit_reason = "REVERSAL_FAIL"
                exit_price = bar.close

            if exit_reason:
                proceeds = exit_price * pos.quantity
                cash += proceeds
                trade = BacktestTrade(
                    ticker=pos.ticker,
                    entry_date=pos.entry_date,
                    exit_date=date_key,
                    entry_price=pos.entry_price,
                    exit_price=exit_price,
                    quantity=pos.quantity,
                    stop_loss_price=pos.stop_loss_price,
                    exit_reason=exit_reason,
                )
                trades.append(trade)
                setup_stats[pos.setup]["trades"] += 1
                setup_stats[pos.setup]["pnl"] += trade.pnl
                setup_stats[pos.setup]["days"] += pos.bars_held
                if trade.pnl > 0:
                    setup_stats[pos.setup]["wins"] += 1
            else:
                remaining_positions.append(pos)

        open_positions = remaining_positions

        reserved_slots = len(open_positions) + sum(len(rows) for rows in pending_entries.values())
        available_slots = max(MAX_POSITIONS - reserved_slots, 0)
        if available_slots > 0:
            opened_today = 0
            for candidate in candidates_by_date.get(date_key, []):
                if opened_today >= MAX_NEW_POSITIONS_PER_DAY or available_slots <= 0:
                    break
                if any(p.ticker == candidate.ticker for p in open_positions):
                    continue
                if any(entry.ticker == candidate.ticker for rows in pending_entries.values() for entry in rows):
                    continue

                bars = bars_by_ticker[candidate.ticker]
                idx = date_index[candidate.ticker].get(date_key)
                next_bar = _bar_on_or_after(bars, (idx or 0) + 1) if idx is not None else None
                if next_bar is None:
                    continue

                gap_pct = ((next_bar.open - candidate.close) / candidate.close) * 100 if candidate.close > 0 else 0.0
                if gap_pct > MAX_GAP_UP_PCT:
                    continue

                allocation = cash * POSITION_SIZE_PCT
                quantity = int(allocation / max(next_bar.open, 1.0))
                if quantity < 1:
                    continue

                cost = quantity * next_bar.open
                if cost > cash:
                    continue

                if candidate.setup == "TREND":
                    stop = min(candidate.low, next_bar.open * 0.94)
                    target = next_bar.open * 1.10
                    max_holding_days = 5
                else:
                    stop = min(candidate.low, next_bar.open * 0.95)
                    target = next_bar.open * 1.06
                    max_holding_days = 2

                if stop >= next_bar.open:
                    stop = next_bar.open * 0.95

                pending_entries[next_bar.date].append(
                    PendingEntry(
                        entry_date=next_bar.date,
                        ticker=candidate.ticker,
                        setup=candidate.setup,
                        entry_price=next_bar.open,
                        quantity=quantity,
                        stop_loss_price=stop,
                        target_price=target,
                        max_holding_days=max_holding_days,
                    )
                )
                opened_today += 1
                available_slots -= 1

        mark_to_market = cash
        for pos in open_positions:
            idx = date_index[pos.ticker].get(date_key)
            if idx is None:
                mark_to_market += pos.entry_price * pos.quantity
            else:
                mark_to_market += bars_by_ticker[pos.ticker][idx].close * pos.quantity

        equity_curve.append({"date": date_key, "equity": mark_to_market})

    if open_positions:
        for pos in open_positions:
            bars = bars_by_ticker[pos.ticker]
            bar = bars[-1]
            cash += bar.close * pos.quantity
            trade = BacktestTrade(
                ticker=pos.ticker,
                entry_date=pos.entry_date,
                exit_date=bar.date,
                entry_price=pos.entry_price,
                exit_price=bar.close,
                quantity=pos.quantity,
                stop_loss_price=pos.stop_loss_price,
                exit_reason="FORCED_EOD",
            )
            trades.append(trade)
            setup_stats[pos.setup]["trades"] += 1
            setup_stats[pos.setup]["pnl"] += trade.pnl
            setup_stats[pos.setup]["days"] += pos.bars_held
            if trade.pnl > 0:
                setup_stats[pos.setup]["wins"] += 1

    result = engine.run("mqk_v3_daily_approx", INITIAL_CAPITAL, trades)
    return trades, {
        "summary": result,
        "setup_stats": setup_stats,
        "ending_capital": cash,
        "equity_curve": equity_curve,
    }


def _monthly_returns(trades: list[BacktestTrade]) -> list[dict[str, Any]]:
    monthly: dict[str, float] = defaultdict(float)
    for trade in trades:
        month = _month_key(trade.exit_date)
        monthly[month] += trade.pnl
    return [{"month": k, "pnl_krw": round(v, 0)} for k, v in sorted(monthly.items())]


def _top_drawdowns(equity_curve: list[dict[str, Any]]) -> list[dict[str, Any]]:
    peak = 0.0
    rows: list[dict[str, Any]] = []
    for row in equity_curve:
        equity = float(row["equity"])
        if equity > peak:
            peak = equity
        dd_pct = ((peak - equity) / peak * 100) if peak > 0 else 0.0
        rows.append({"date": row["date"], "equity": equity, "drawdown_pct": dd_pct})
    rows.sort(key=lambda x: x["drawdown_pct"], reverse=True)
    return rows[:10]


def _parse_trade_date(value: str) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _month_key(value: str) -> str:
    dt = _parse_trade_date(value)
    if dt is None:
        return value[:7]
    return dt.strftime("%Y-%m")


def _report_payload(
    tickers: list[str],
    bars_by_ticker: dict[str, list[OHLCVBar]],
    snapshots: dict[str, MarketSnapshot],
    trades: list[BacktestTrade],
    sim: dict[str, Any],
) -> dict[str, Any]:
    result = sim["summary"]
    holding_days = []
    for trade in trades:
        entry_dt = _parse_trade_date(trade.entry_date)
        exit_dt = _parse_trade_date(trade.exit_date)
        if entry_dt and exit_dt:
            holding_days.append((exit_dt - entry_dt).days)
    avg_holding = statistics.mean(holding_days) if holding_days else 0.0

    setup_rows = []
    for setup, stats in sim["setup_stats"].items():
        trade_count = int(stats["trades"])
        if trade_count == 0:
            continue
        setup_rows.append(
            {
                "setup": setup,
                "trades": trade_count,
                "win_rate_pct": round(stats["wins"] / trade_count * 100, 2),
                "pnl_krw": round(stats["pnl"], 0),
                "avg_holding_days": round(stats["days"] / trade_count, 2),
            }
        )
    setup_rows.sort(key=lambda x: x["pnl_krw"], reverse=True)

    liquid_names = []
    for ticker in tickers[:20]:
        snap = snapshots.get(ticker)
        meta = {
            "ticker": ticker,
            "name": snap.name if snap else ticker,
            "sector": snap.sector if snap else "",
            "market_cap_krw": round(snap.market_cap, 0) if snap else 0,
        }
        liquid_names.append(meta)

    return {
        "generated_at": datetime.now().isoformat(),
        "strategy": "mqk_v3_daily_approx",
        "method": {
            "universe_source": "current KIS liquidity seed (fluctuation rank + trading value rank)",
            "llm_usage": "none; deterministic mini-first replay approximation",
            "lookback_bars": LOOKBACK_BARS,
            "initial_capital": INITIAL_CAPITAL,
            "max_positions": MAX_POSITIONS,
            "max_new_positions_per_day": MAX_NEW_POSITIONS_PER_DAY,
            "position_size_pct": POSITION_SIZE_PCT,
            "min_trading_value_krw": MIN_TRADING_VALUE_KRW,
            "min_market_cap_krw": MIN_MARKET_CAP_KRW,
            "caveat": "daily-bar approximation; no historical intraday/news/theme leadership replay",
        },
        "coverage": {
            "seed_tickers": len(tickers),
            "tickers_with_history": len(bars_by_ticker),
            "sample_universe": liquid_names,
        },
        "performance": {
            "total_trades": result.total_trades,
            "win_rate_pct": result.win_rate,
            "total_return_pct": result.total_return_pct,
            "max_drawdown_pct": result.max_drawdown_pct,
            "profit_factor": result.profit_factor,
            "avg_win_pct": result.avg_win_pct,
            "avg_loss_pct": result.avg_loss_pct,
            "sharpe_ratio": result.sharpe_ratio,
            "ending_capital_krw": round(sim["ending_capital"], 0),
            "avg_holding_days": round(avg_holding, 2),
        },
        "by_setup": setup_rows,
        "monthly_returns": _monthly_returns(trades),
        "top_drawdowns": _top_drawdowns(sim["equity_curve"]),
        "top_winners": sorted(
            [
                {
                    "ticker": t.ticker,
                    "entry_date": t.entry_date,
                    "exit_date": t.exit_date,
                    "pnl_krw": round(t.pnl, 0),
                    "pnl_pct": round(t.pnl_pct, 2),
                    "exit_reason": t.exit_reason,
                }
                for t in trades
            ],
            key=lambda x: x["pnl_krw"],
            reverse=True,
        )[:15],
        "top_losers": sorted(
            [
                {
                    "ticker": t.ticker,
                    "entry_date": t.entry_date,
                    "exit_date": t.exit_date,
                    "pnl_krw": round(t.pnl, 0),
                    "pnl_pct": round(t.pnl_pct, 2),
                    "exit_reason": t.exit_reason,
                }
                for t in trades
            ],
            key=lambda x: x["pnl_krw"],
        )[:15],
    }


def _write_markdown(payload: dict[str, Any], output_path: Path) -> None:
    perf = payload["performance"]
    setup_rows = payload["by_setup"]
    top_dd = payload["top_drawdowns"][:5]

    weaknesses = []
    if perf["profit_factor"] < 1.2:
        weaknesses.append("- 손익비가 낮아 작은 승리로 큰 손실을 상쇄하지 못합니다.")
    if perf["max_drawdown_pct"] > 15:
        weaknesses.append("- 낙폭이 커서 실전 체감 리스크가 높습니다.")
    if any(row["setup"] == "REVERSAL" and row["pnl_krw"] < 0 for row in setup_rows):
        weaknesses.append("- REVERSAL 전술이 일봉 근사 환경에서는 손실 또는 불안정성이 큽니다.")
    if perf["avg_holding_days"] > 4:
        weaknesses.append("- 보유기간이 전략 의도보다 길어 시간 청산 규율이 약할 수 있습니다.")
    if not weaknesses:
        weaknesses.append("- 일봉 근사 기준에서는 치명적 약점이 두드러지지 않았지만, 장중 리플레이 부재로 과대평가 가능성이 있습니다.")

    improvements = [
        "- REVERSAL은 장중 반등봉 확인이 핵심이므로, 다음 단계에서는 분봉 리플레이 전용 데이터셋을 붙여 별도 검증해야 합니다.",
        "- TREND 후보는 거래대금 급증 다음날 갭 과열을 더 강하게 필터링하는 것이 좋습니다.",
        "- 테마 1등 여부를 일봉 근사 대신 히스토리컬 랭킹 데이터로 보강하면 전략 충실도가 올라갑니다.",
        "- `late_intraday` 전술은 지수 급락일 샘플만 분리해 따로 측정하는 것이 바람직합니다.",
    ]

    lines = [
        "# MQK v3 One-Year Backtest Report",
        "",
        f"- 생성 시각: `{payload['generated_at']}`",
        f"- 전략: `{payload['strategy']}`",
        f"- 유니버스: 현재 KIS 유동성 seed {payload['coverage']['seed_tickers']}종목 중 히스토리 확보 {payload['coverage']['tickers_with_history']}종목",
        f"- 방식: {payload['method']['caveat']}",
        "",
        "## 핵심 성과",
        "",
        f"- 총 거래: `{perf['total_trades']}`",
        f"- 총 수익률: `{perf['total_return_pct']:+.2f}%`",
        f"- 최대 낙폭: `{perf['max_drawdown_pct']:.2f}%`",
        f"- 승률: `{perf['win_rate_pct']:.2f}%`",
        f"- 손익비: `{perf['profit_factor']:.2f}`",
        f"- 샤프: `{perf['sharpe_ratio']:.2f}`",
        f"- 평균 보유일: `{perf['avg_holding_days']:.2f}`",
        "",
        "## Setup별 성과",
        "",
        "| setup | trades | win_rate_pct | pnl_krw | avg_holding_days |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in setup_rows:
        lines.append(
            f"| {row['setup']} | {row['trades']} | {row['win_rate_pct']:.2f} | {int(row['pnl_krw']):,} | {row['avg_holding_days']:.2f} |"
        )

    lines += [
        "",
        "## 대표 낙폭 구간",
        "",
        "| date | drawdown_pct | equity |",
        "|---|---:|---:|",
    ]
    for row in top_dd:
        lines.append(f"| {row['date']} | {row['drawdown_pct']:.2f} | {int(row['equity']):,} |")

    lines += [
        "",
        "## 취약점",
        "",
        *weaknesses,
        "",
        "## 보완점",
        "",
        *improvements,
        "",
        "## 해석",
        "",
        "- 이번 결과는 `mini/LLM 없이` 일봉 규칙으로 재현한 1차 검증입니다.",
        "- 실제 v3는 장중 거래대금, 뉴스, 테마 리더십, 분봉 반등 확인이 핵심이므로 REVERSAL은 특히 보수적으로 해석해야 합니다.",
        "- 따라서 이번 리포트는 `전략의 방향성`과 `취약 구간`을 보는 용도로 유효하고, 실전 배치 판단 전에는 분봉 리플레이가 추가로 필요합니다.",
        "",
    ]
    output_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    kis = KISApi()
    loader = HistoricalLoader(kis_api=kis)
    universe_meta = _load_universe_meta(BASE_DIR / "data" / "universe.csv")
    seed = _seed_universe(kis)
    tickers = [ticker for ticker in seed if ticker in universe_meta]
    snapshots = _snapshot_meta(kis, tickers)
    bars_by_ticker = _load_bars(loader, tickers)
    candidates_by_date, _ = _build_daily_candidates(bars_by_ticker, snapshots)
    date_index = _make_date_index(bars_by_ticker)
    trades, sim = _simulate(bars_by_ticker, candidates_by_date, date_index)
    payload = _report_payload(tickers, bars_by_ticker, snapshots, trades, sim)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = REPORT_DIR / f"mqk_v3_backtest_{stamp}.json"
    md_path = REPORT_DIR / f"mqk_v3_backtest_{stamp}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown(payload, md_path)

    print(json.dumps({"json_report": str(json_path), "markdown_report": str(md_path), "trades": len(trades)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
