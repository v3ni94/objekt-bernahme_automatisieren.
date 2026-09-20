"""OpenAI-Provider (Stufe 3): temperature nur fuer Modelle, die sie unterstuetzen; Basis-URL aus der Konfiguration;
fehlender Schluessel als nicht wiederholbarer Fehler. Der SDK-Client wird durch eine Attrappe ersetzt, es geht nichts
nach aussen."""

from __future__ import annotations

import json
from types import SimpleNamespace

import openai
import pytest

from apps.ai.provider import ProviderConfig, ProviderError
from apps.ai.providers import openai_provider
from apps.ai.providers.openai_provider import OpenAIProvider, supports_temperature


class FakeCompletions:
    def __init__(self, calls):
        self.calls = calls

    def create(self, **kwargs):
        self.calls.append(kwargs)
        content = json.dumps({"category": "05", "confidence": 0.95})
        return SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=120, completion_tokens=15),
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        )


class FakeOpenAI:
    instances: list = []
    calls: list = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.chat = SimpleNamespace(completions=FakeCompletions(FakeOpenAI.calls))
        FakeOpenAI.instances.append(self)


@pytest.fixture(autouse=True)
def fake_sdk(monkeypatch):
    FakeOpenAI.instances = []
    FakeOpenAI.calls = []
    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
    monkeypatch.setenv("OPENAI_API_KEY", "test-schluessel")
    monkeypatch.delenv("OPENAI_API_KEY_FILE", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("gpt-4.1-mini", True),
        ("gpt-4o", True),
        ("GPT-5-mini", False),
        ("gpt-5", False),
        ("o4-mini", False),
        ("o3", False),
        (None, True),
    ],
)
def test_supports_temperature(model, expected):
    assert supports_temperature(model) is expected


def test_temperature_nur_fuer_klassische_modelle():
    provider = OpenAIProvider()
    provider.send("system", "user", ProviderConfig(name="openai", enabled=True, model="gpt-4.1-mini"))
    provider.send("system", "user", ProviderConfig(name="openai", enabled=True, model="gpt-5-mini"))
    klassisch, reasoning = FakeOpenAI.calls
    assert klassisch["temperature"] == 0
    assert "temperature" not in reasoning
    for call in (klassisch, reasoning):
        assert call["response_format"]["type"] == "json_schema"
        assert call["response_format"]["json_schema"]["strict"] is True
        assert [m["role"] for m in call["messages"]] == ["system", "user"]


def test_antwort_und_tokenzahlen_werden_uebernommen():
    raw = OpenAIProvider().send(
        "system", "user", ProviderConfig(name="openai", enabled=True, model="gpt-4.1-mini", timeout_s=7)
    )
    assert json.loads(raw.text)["category"] == "05"
    assert (raw.tokens_in, raw.tokens_out, raw.http_status, raw.model) == (120, 15, 200, "gpt-4.1-mini")
    client = FakeOpenAI.instances[-1]
    assert client.kwargs["api_key"] == "test-schluessel"
    assert client.kwargs["timeout"] == 7
    assert client.kwargs["max_retries"] == 0


def test_endpunkt_aus_konfiguration_vor_umgebung(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://umgebung.example/v1")
    OpenAIProvider().send(
        "s",
        "u",
        ProviderConfig(name="openai", enabled=True, model="gpt-4.1-mini", endpoint="https://eu.example/v1"),
    )
    assert FakeOpenAI.instances[-1].kwargs["base_url"] == "https://eu.example/v1"
    OpenAIProvider().send("s", "u", ProviderConfig(name="openai", enabled=True, model="gpt-4.1-mini"))
    assert FakeOpenAI.instances[-1].kwargs["base_url"] == "https://umgebung.example/v1"


def test_fehlender_schluessel_ist_nicht_wiederholbar(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ProviderError) as exc:
        OpenAIProvider().send("s", "u", ProviderConfig(name="openai", enabled=True, model="gpt-4.1-mini"))
    assert exc.value.retryable is False
    assert openai_provider.REASONING_MODEL_PREFIXES[0] == "gpt-5"


def test_leere_umgebungsvariable_ergibt_standardendpunkt(monkeypatch):
    # docker compose setzt OPENAI_BASE_URL="" ; das SDK wuerde die leere URL uebernehmen und mit Connection error scheitern
    monkeypatch.setenv("OPENAI_BASE_URL", "")
    OpenAIProvider()._client(ProviderConfig(name="openai", enabled=True, model="gpt-4.1-mini"))
    assert FakeOpenAI.instances[-1].kwargs["base_url"] == "https://api.openai.com/v1"
    monkeypatch.delenv("OPENAI_BASE_URL")
    OpenAIProvider()._client(ProviderConfig(name="openai", enabled=True, model="gpt-4.1-mini", endpoint="  "))
    assert FakeOpenAI.instances[-1].kwargs["base_url"] == "https://api.openai.com/v1"
