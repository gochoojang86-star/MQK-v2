"""LLMClient JSON 파싱 보강 테스트"""
import json

from llm.client import LLMClient


class _FakeMsg:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMsg(content)


class _FakeResponse:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self, content):
        self._content = content

    def create(self, **kwargs):
        return _FakeResponse(self._content)


class _FakeChat:
    def __init__(self, content):
        self.completions = _FakeCompletions(content)


def _client_with_response(content):
    c = LLMClient.__new__(LLMClient)
    from config.settings import LLM_CONFIG
    c._cfg = LLM_CONFIG
    c._client = type("FakeOpenAI", (), {"chat": _FakeChat(content)})()
    return c


def test_call_takes_first_json_object_when_extra_data():
    content = '{"next_action": "call_tool", "tool": "get_ohlcv"}\n{"next_action": "call_tool", "tool": "get_flow"}'
    c = _client_with_response(content)
    result = c.call("sys", "user")
    assert result == {"next_action": "call_tool", "tool": "get_ohlcv"}


def test_call_raises_on_totally_invalid_json():
    import pytest
    c = _client_with_response("이건 JSON이 아닙니다")
    with pytest.raises(ValueError):
        c.call("sys", "user")
