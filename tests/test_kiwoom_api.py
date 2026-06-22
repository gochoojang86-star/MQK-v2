import pytest
import requests

from broker.kiwoom_api import KiwoomApi, KiwoomConfig, KiwoomRateLimitError


class DummyResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json_data = json_data or {}
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error", response=self)

    def json(self):
        return self._json_data


def make_api(tmp_path):
    return KiwoomApi(
        config=KiwoomConfig(
            appkey="app",
            secretkey="secret",
            base_url="https://api.kiwoom.com",
            ws_base_url="wss://api.kiwoom.com:10000",
        ),
        token_cache_path=tmp_path / "kiwoom_token.json",
    )


def test_rest_request_applies_cooldown_after_http_429(monkeypatch, tmp_path):
    api = make_api(tmp_path)
    monkeypatch.setattr(api, "_get_token", lambda: "token")
    now = {"t": 1000.0}
    monkeypatch.setattr("broker.kiwoom_api.time.time", lambda: now["t"])
    monkeypatch.setattr("broker.kiwoom_api.time.sleep", lambda _: None)

    calls = {"n": 0}

    def fake_post(*args, **kwargs):
        calls["n"] += 1
        return DummyResponse(status_code=429)

    monkeypatch.setattr("broker.kiwoom_api.requests.post", fake_post)

    with pytest.raises(KiwoomRateLimitError):
        api.volume_surge()

    assert calls["n"] == 1

    with pytest.raises(KiwoomRateLimitError, match="cooldown active"):
        api.volume_surge()

    assert calls["n"] == 1


def test_rest_request_applies_cooldown_when_payload_mentions_rate_limit(monkeypatch, tmp_path):
    api = make_api(tmp_path)
    monkeypatch.setattr(api, "_get_token", lambda: "token")
    now = {"t": 2000.0}
    monkeypatch.setattr("broker.kiwoom_api.time.time", lambda: now["t"])
    monkeypatch.setattr("broker.kiwoom_api.time.sleep", lambda _: None)

    calls = {"n": 0}

    def fake_post(*args, **kwargs):
        calls["n"] += 1
        return DummyResponse(
            status_code=200,
            json_data={"return_code": -1, "return_msg": "호출제한 초과"},
        )

    monkeypatch.setattr("broker.kiwoom_api.requests.post", fake_post)

    with pytest.raises(KiwoomRateLimitError):
        api.foreign_institution_top()

    assert calls["n"] == 1

    with pytest.raises(KiwoomRateLimitError, match="cooldown active"):
        api.foreign_institution_top()

    assert calls["n"] == 1


def test_rest_request_respects_min_interval(monkeypatch, tmp_path):
    api = make_api(tmp_path)
    monkeypatch.setattr(api, "_get_token", lambda: "token")
    current = {"t": 3000.0}

    def fake_time():
        return current["t"]

    slept = []

    def fake_sleep(seconds):
        slept.append(seconds)
        current["t"] += seconds

    monkeypatch.setattr("broker.kiwoom_api.time.time", fake_time)
    monkeypatch.setattr("broker.kiwoom_api.time.sleep", fake_sleep)
    monkeypatch.setattr(
        "broker.kiwoom_api.requests.post",
        lambda *args, **kwargs: DummyResponse(status_code=200, json_data={"ok": True}),
    )

    api.volume_surge()
    current["t"] += 0.1
    api.volume_surge()

    assert slept
    assert slept[-1] == pytest.approx(api._min_interval_seconds - 0.1, rel=1e-3)
