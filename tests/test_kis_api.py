import json
import time
from dataclasses import dataclass

from broker.kis_api import KISApi


@dataclass
class FakeKISConfig:
    mode: str = "paper"
    app_key: str = "app-key"
    app_secret: str = "app-secret"
    account_no: str = "12345678-01"
    base_url: str = "https://example.test"


def test_get_token_uses_valid_file_cache(tmp_path):
    cache_path = tmp_path / "kis_token.json"
    cache_path.write_text(
        json.dumps({
            "access_token": "cached-token",
            "expires_at": time.time() + 3600,
        }),
        encoding="utf-8",
    )
    api = KISApi(config=FakeKISConfig(), token_cache_path=cache_path)

    assert api._get_token() == "cached-token"


def test_get_token_saves_new_token_to_cache(tmp_path, monkeypatch):
    cache_path = tmp_path / "kis_token.json"
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"access_token": "fresh-token", "expires_in": 7200}

    def fake_post(url, json, timeout):
        calls.append((url, json, timeout))
        return FakeResponse()

    monkeypatch.setattr("broker.kis_api.requests.post", fake_post)

    api = KISApi(config=FakeKISConfig(), token_cache_path=cache_path)

    assert api._get_token() == "fresh-token"
    assert len(calls) == 1
    cached = json.loads(cache_path.read_text(encoding="utf-8"))
    assert cached["access_token"] == "fresh-token"
    assert cached["expires_at"] > time.time()


def test_get_index_quote_retries_transient_server_error(tmp_path, monkeypatch):
    cache_path = tmp_path / "kis_token.json"
    cache_path.write_text(
        json.dumps({
            "access_token": "cached-token",
            "expires_at": time.time() + 3600,
        }),
        encoding="utf-8",
    )
    calls = []

    class FakeResponse:
        def __init__(self, status_code):
            self.status_code = status_code

        def raise_for_status(self):
            if self.status_code >= 500:
                import requests
                raise requests.HTTPError(f"{self.status_code} Server Error")

        def json(self):
            return {
                "rt_cd": "0",
                "output": {
                    "bstp_nmix_prpr": "2800.50",
                    "bstp_nmix_prdy_ctrt": "0.75",
                },
            }

    def fake_get(url, headers, params, timeout):
        calls.append((url, headers, params, timeout))
        return FakeResponse(500 if len(calls) == 1 else 200)

    monkeypatch.setattr("broker.kis_api.requests.get", fake_get)
    monkeypatch.setattr("broker.kis_api.time.sleep", lambda _: None)

    api = KISApi(config=FakeKISConfig(), token_cache_path=cache_path)

    assert api._get_index_quote("0001")["bstp_nmix_prpr"] == "2800.50"
    assert len(calls) == 2
