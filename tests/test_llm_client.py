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


def _client_with_response(content, tmp_path):
    c = LLMClient.__new__(LLMClient)
    from config.settings import LLM_CONFIG
    c._cfg = LLM_CONFIG
    c._client = type("FakeOpenAI", (), {"chat": _FakeChat(content)})()
    c._usage_log_dir = tmp_path  # usage 로그가 실제 logs/ 디렉토리를 오염시키지 않도록
    return c


def test_call_takes_first_json_object_when_extra_data(tmp_path):
    content = '{"next_action": "call_tool", "tool": "get_ohlcv"}\n{"next_action": "call_tool", "tool": "get_flow"}'
    c = _client_with_response(content, tmp_path)
    result = c.call("sys", "user")
    assert result == {"next_action": "call_tool", "tool": "get_ohlcv"}


def test_call_raises_on_totally_invalid_json(tmp_path):
    import pytest
    c = _client_with_response("이건 JSON이 아닙니다", tmp_path)
    with pytest.raises(ValueError):
        c.call("sys", "user")
