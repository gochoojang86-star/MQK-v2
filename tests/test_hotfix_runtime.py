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
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))]
        )


class FakeOpenAIClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=FakeCompletions())


def make_client(model):
    client = LLMClient.__new__(LLMClient)
    client._cfg = FakeConfig(model)
    client._client = FakeOpenAIClient()
    return client


def test_gpt5_versioned_models_use_max_completion_tokens_not_max_tokens():
    client = make_client("gpt-5.4-2026-06-01")

    result = client.call("system", "user", tier=ModelTier.STANDARD)

    kwargs = client._client.chat.completions.kwargs
    assert result == {"ok": True}
    assert kwargs["max_completion_tokens"] == 123
    assert "max_tokens" not in kwargs
    assert "temperature" not in kwargs


def test_telegram_news_source_handles_none_chat():
    event = SimpleNamespace(chat=None)

    assert _source_from_event(event) == ""


def test_telegram_news_source_handles_missing_username():
    event = SimpleNamespace(chat=SimpleNamespace())

    assert _source_from_event(event) == ""
