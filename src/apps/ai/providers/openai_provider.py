"""OpenAI-Anbindung (Stufe 3): strukturierte Ausgabe ueber das JSON-Schema, Basis-URL aus ai.providers.openai.endpoint
oder OPENAI_BASE_URL (EU-Endpunkt nach V-12), Schluessel aus dem Secret openai_api_key. Nur maskierte, gekuerzte
Auszuege (B-11, Ue15). Reasoning-Modelle (gpt-5, o-Reihe) lehnen den Parameter temperature ab; er wird nur fuer die
uebrigen Modelle gesetzt (20.09.2026)."""

from __future__ import annotations

import os

from apps.ai.provider import (
    BaseProvider,
    ProviderConfig,
    ProviderError,
    ProviderTimeout,
    RateLimited,
    RawResponse,
    api_key_for,
)
from apps.ai.schema import response_json_schema

# Modellfamilien ohne frei waehlbare temperature (nur Standardwert erlaubt); Vergleich auf den Namensanfang
REASONING_MODEL_PREFIXES = ("gpt-5", "o1", "o3", "o4")


def supports_temperature(model: str | None) -> bool:
    """Fuer Reasoning-Modelle darf temperature nicht gesetzt werden, sonst antwortet die API mit 400."""
    name = (model or "").strip().lower()
    return not name.startswith(REASONING_MODEL_PREFIXES)


DEFAULT_BASE_URL = "https://api.openai.com/v1"


def resolve_base_url(endpoint: str | None) -> str:
    """Endpunkt aus Konfiguration, sonst OPENAI_BASE_URL, sonst Standard des Anbieters.

    Der Standard wird ausdruecklich uebergeben: Das SDK liest bei base_url=None selbst die Umgebungsvariable und
    wertet eine leere Zeichenkette (wie sie docker compose bei OPENAI_BASE_URL="" setzt) als gesetzte, leere URL.
    Die Folge waere ein APIConnectionError ohne erkennbare Ursache.
    """
    return (endpoint or "").strip() or os.environ.get("OPENAI_BASE_URL", "").strip() or DEFAULT_BASE_URL


class OpenAIProvider(BaseProvider):
    name = "openai"

    def _client(self, cfg: ProviderConfig):
        from openai import OpenAI

        key = api_key_for("openai")
        if not key:
            raise ProviderError("OPENAI_API_KEY fehlt", retryable=False)
        return OpenAI(
            api_key=key, base_url=resolve_base_url(cfg.endpoint), timeout=cfg.timeout_s, max_retries=0
        )

    def send(self, system: str, user: str, cfg: ProviderConfig) -> RawResponse:
        import openai

        client = self._client(cfg)
        params: dict = {
            "model": cfg.model or "",
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "klassifikation",
                    "strict": True,
                    "schema": response_json_schema(),
                },
            },
        }
        if supports_temperature(cfg.model):
            params["temperature"] = 0
        try:
            response = client.chat.completions.create(**params)
        except openai.APITimeoutError as exc:
            raise ProviderTimeout(str(exc)) from exc
        except openai.RateLimitError as exc:
            raise RateLimited(str(exc)) from exc
        except openai.APIStatusError as exc:
            raise ProviderError(
                str(exc), http_status=exc.status_code, retryable=exc.status_code >= 500
            ) from exc
        except openai.APIConnectionError as exc:
            raise ProviderError(str(exc), retryable=True) from exc
        usage = getattr(response, "usage", None)
        text = response.choices[0].message.content or ""
        return RawResponse(
            text,
            getattr(usage, "prompt_tokens", 0) or 0,
            getattr(usage, "completion_tokens", 0) or 0,
            200,
            cfg.model,
        )
