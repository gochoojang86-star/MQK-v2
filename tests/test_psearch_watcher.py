"""조건검색 편입 감시 (유사 웹훅) 테스트"""
from codes.psearch_watcher import (
    classify_condition, load_seen, save_seen, poll_new_entries,
    partition_entries, format_alert,
)


class StubCtx:
    """screening.psearch_*가 사용하는 인터페이스만 흉내."""
    def __init__(self, titles, results):
        self._titles = titles
        self._results = results
        self.kis_api = self
        self.cache = self
        self.circuit_breaker = self

    # MILContext.cached_call 시그니처 호환 — 캐시 없이 직접 실행
    def cached_call(self, tool, phase, args, fetch_fn):
        return fetch_fn()

    def raw_get(self, tr_id, path, params, mode=None):
        if tr_id == "HHKST03900300":
            return {"rt_cd": "0", "output2": self._titles}
        seq = params.get("seq")
        return self._results.get(seq, {"rt_cd": "1", "msg1": "종목코드 오류입니다."})


def _titles():
    return [
        {"seq": "0", "condition_nm": "MQK1_주도주베이스"},
        {"seq": "1", "condition_nm": "MQK2_EP돌파"},
        {"seq": "2", "condition_nm": "MQK3_폭락낙주"},
    ]


def _result(tickers):
    return {"rt_cd": "0", "output2": [
        {"code": t, "name": f"종목{t}", "price": "10000", "chgrate": "8.0",
         "acml_vol": "1", "trade_amt": "50000000000", "cttr": "150",
         "chgrate2": "300", "high52": "12000", "low52": "5000", "stotprice": "1"}
        for t in tickers
    ]}


def test_classify_condition():
    assert classify_condition("MQK1_주도주베이스") == "base"
    assert classify_condition("MQK2_EP돌파") == "ep"
    assert classify_condition("MQK3_폭락낙주") == "reversal"
    assert classify_condition("아무이름") == "base"


def test_first_poll_seeds_base_but_alerts_ep(tmp_path):
    ctx = StubCtx(_titles(), {"0": _result(["111111"]), "1": _result(["222222"]), "2": _result(["333333"])})
    seen = load_seen(path=tmp_path / "seen.json", today="2026-06-12")

    events = poll_new_entries(ctx, "user", seen)

    kinds = {e["ticker"]: e["kind"] for e in events}
    assert kinds == {"222222": "ep"}  # EP만 첫 폴부터 이벤트
    assert "111111" in seen["seen"]["0"]  # base는 조용히 시드
    assert "333333" in seen["seen"]["2"]  # reversal도 시드


def test_subsequent_poll_diffs_new_entries(tmp_path):
    path = tmp_path / "seen.json"
    ctx1 = StubCtx(_titles(), {"0": _result(["111111"]), "1": _result([]), "2": _result([])})
    seen = load_seen(path=path, today="2026-06-12")
    poll_new_entries(ctx1, "user", seen)
    save_seen(seen, path=path)

    # 다음 폴: base에 신규 444444 편입, reversal에 555555 편입
    ctx2 = StubCtx(_titles(), {"0": _result(["111111", "444444"]), "1": _result([]), "2": _result(["555555"])})
    seen2 = load_seen(path=path, today="2026-06-12")
    events = poll_new_entries(ctx2, "user", seen2)

    assert {e["ticker"] for e in events} == {"444444", "555555"}
    merge, trigger = partition_entries(events)
    assert merge == ["444444"]  # reversal은 watchlist 병합 제외
    assert trigger is True
    alert = format_alert(events)
    assert "444444" in alert and "555555" in alert and "late 슬롯" in alert


def test_reversal_only_entries_do_not_trigger():
    events = [{"seq": "2", "condition_name": "MQK3_폭락낙주", "kind": "reversal",
               "ticker": "555555", "name": "낙주", "price": 1, "change_pct": -8.0, "trading_value": 1}]
    merge, trigger = partition_entries(events)
    assert merge == [] and trigger is False


def test_seen_resets_on_new_day(tmp_path):
    path = tmp_path / "seen.json"
    save_seen({"date": "2026-06-11", "seen": {"0": ["111111"]}, "seeded": ["0"]}, path=path)
    seen = load_seen(path=path, today="2026-06-12")
    assert seen["seen"] == {} and seen["seeded"] == []
