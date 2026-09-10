"""OpenAI-Anbindung (Stufe 3): strukturierte Ausgabe ueber das JSON-Schema, Basis-URL aus OPENAI_BASE_URL (EU-Endpunkt
nach V-12), Schluessel aus dem Secret openai_api_key. Nur maskierte, gekuerzte Auszuege (B-11, Ue15)."""

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


class OpenAIProvider(BaseProvider):
    name = "openai"

    def _client(self, cfg: ProviderConfig):
        from openai import OpenAI

        key = api_key_for("openai")
        if not key:
            raise ProviderError("OPENAI_API_KEY fehlt", retryable=False)
        base_url = cfg.endpoint or os.environ.get("OPENAI_BASE_URL") or None
        return OpenAI(api_key=key, base_url=base_url, timeout=cfg.timeout_s, max_retries=0)

    def send(self, system: str, user: str, cfg: ProviderConfig) -> RawResponse:
        import openai

        client = self._client(cfg)
        try:
            response = client.chat.completions.create(
                model=cfg.model or "",
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "klassifikation",
                        "strict": True,
                        "schema": response_json_schema(),
                    },
                },
                temperature=0,
            )
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
