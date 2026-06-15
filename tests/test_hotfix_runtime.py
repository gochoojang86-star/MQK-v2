from types import SimpleNamespace

from config.settings import ModelTier
from llm.client import LLMClient
from broker.telegram_news import _source_from_event


class FakeConfig:
    max_tokens = 123
    temperature = 0.7

    def __init__(self, model):
        self._model = model

    def model_for(self, tier):
        assert tier == ModelTier.STANDARD
        return self._model


class FakeCompletions:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))],
            usage=SimpleNamespace(
                prompt_tokens=111,
                completion_tokens=22,
                total_tokens=133,
                prompt_tokens_details=SimpleNamespace(cached_tokens=11),
                completion_tokens_details=SimpleNamespace(reasoning_tokens=7),
            ),
        )


class FakeOpenAIClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=FakeCompletions())


def make_client(model):
    client = LLMClient.__new__(LLMClient)
    client._cfg = FakeConfig(model)
    client._client = FakeOpenAIClient()
    client._usage_log_dir = ""
    return client


def test_gpt5_versioned_models_use_max_completion_tokens_not_max_tokens():
    client = make_client("gpt-5.4-2026-06-01")
    records = []
    client._append_usage_record = records.append

    result = client.call("system", "user", tier=ModelTier.STANDARD)

    kwargs = client._client.chat.completions.kwargs
    assert result == {"ok": True}
    assert kwargs["max_completion_tokens"] == 123
    assert "max_tokens" not in kwargs
    assert "temperature" not in kwargs
    assert records[0]["prompt_tokens"] == 111
    assert records[0]["completion_tokens"] == 22
    assert records[0]["total_tokens"] == 133
    assert records[0]["cached_prompt_tokens"] == 11
    assert records[0]["reasoning_tokens"] == 7


def test_non_reasoning_models_use_max_tokens_and_log_usage():
    client = make_client("gpt-4.1-mini")
    records = []
    client._append_usage_record = records.append

    result = client.call("system", "user", tier=ModelTier.STANDARD)

    kwargs = client._client.chat.completions.kwargs
    assert result == {"ok": True}
    assert kwargs["max_tokens"] == 123
    assert kwargs["temperature"] == 0.7
    assert "max_completion_tokens" not in kwargs
    assert records[0]["model"] == "gpt-4.1-mini"
    assert records[0]["tier"] == "standard"


def test_telegram_news_source_handles_none_chat():
    event = SimpleNamespace(chat=None)

    assert _source_from_event(event) == ""


def test_telegram_news_source_handles_missing_username():
    event = SimpleNamespace(chat=SimpleNamespace())

    assert _source_from_event(event) == ""
