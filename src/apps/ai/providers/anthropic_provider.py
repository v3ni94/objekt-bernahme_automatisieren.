"""Anthropic-Anbindung (Stufe 3): strukturierte Ausgabe ueber ein Tool mit dem JSON-Schema als Eingabeschema, Basis-URL
aus ANTHROPIC_BASE_URL (EU-Endpunkt nach V-12), Schluessel aus dem Secret anthropic_api_key."""

from __future__ import annotations

import json
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


class AnthropicProvider(BaseProvider):
    name = "anthropic"

    def _client(self, cfg: ProviderConfig):
        from anthropic import Anthropic

        key = api_key_for("anthropic")
        if not key:
            raise ProviderError("ANTHROPIC_API_KEY fehlt", retryable=False)
        base_url = cfg.endpoint or os.environ.get("ANTHROPIC_BASE_URL") or None
        return Anthropic(api_key=key, base_url=base_url, timeout=cfg.timeout_s, max_retries=0)

    def send(self, system: str, user: str, cfg: ProviderConfig) -> RawResponse:
        import anthropic

        client = self._client(cfg)
        try:
            response = client.messages.create(
                model=cfg.model or "",
                max_tokens=800,
                system=system,
                messages=[{"role": "user", "content": user}],
                tools=[
                    {
                        "name": "klassifikation",
                        "description": "Klassifikationsergebnis im Schema",
                        "input_schema": response_json_schema(),
                    }
                ],
                tool_choice={"type": "tool", "name": "klassifikation"},
                temperature=0,
            )
        except anthropic.APITimeoutError as exc:
            raise ProviderTimeout(str(exc)) from exc
        except anthropic.RateLimitError as exc:
            raise RateLimited(str(exc)) from exc
        except anthropic.APIStatusError as exc:
            raise ProviderError(
                str(exc), http_status=exc.status_code, retryable=exc.status_code >= 500
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderError(str(exc), retryable=True) from exc
        payload = None
        for block in response.content:
            if getattr(block, "type", "") == "tool_use":
                payload = block.input
                break
        text = json.dumps(payload, ensure_ascii=False) if payload is not None else ""
        usage = getattr(response, "usage", None)
        return RawResponse(
            text,
            getattr(usage, "input_tokens", 0) or 0,
            getattr(usage, "output_tokens", 0) or 0,
            200,
            cfg.model,
        )
