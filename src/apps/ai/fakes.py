"""FakeClassificationProvider fuer Tests (docs/architektur.md 6.7, B-28): skriptbares Fehlverhalten je Aufruf
(timeout, 5xx, 429, schema_error, ok) und Aufzeichnung der gesendeten Requests (Datenminimierung pruefbar)."""

from __future__ import annotations

import json
from collections import deque

from apps.ai.provider import (
    BaseProvider,
    ProviderConfig,
    ProviderError,
    ProviderTimeout,
    RateLimited,
    RawResponse,
)


class FakeClassificationProvider(BaseProvider):
    def __init__(
        self, name: str, *, script: list[str] | None = None, answer: dict | None = None, tokens=(800, 120)
    ):
        self.name = name
        self.script = deque(script or [])
        self.answer = answer or {
            "object_related": True,
            "category": "05",
            "subfolder": "05",
            "document_type": "einzelabrechnung",
            "period": {"year": 2025, "from": None, "to": None, "document_date": None},
            "mentioned_units": ["WE03"],
            "mentioned_parties": ["Mustermann"],
            "confidence": 0.93,
            "reasoning": "Einzelabrechnung mit Einheit und Abrechnungsjahr",
        }
        self.tokens = tokens
        self.sent: list[dict] = []

    def send(self, system: str, user: str, cfg: ProviderConfig) -> RawResponse:
        self.sent.append({"system": system, "user": user, "model": cfg.model})
        step = self.script.popleft() if self.script else "ok"
        if step == "timeout":
            raise ProviderTimeout()
        if step == "5xx":
            raise ProviderError("Bad Gateway", http_status=502, retryable=True)
        if step == "429":
            raise RateLimited()
        if step == "4xx":
            raise ProviderError("Unauthorized", http_status=401, retryable=False)
        if step == "schema_error":
            return RawResponse(
                '{"category": "05_Eigentümerakte", "confidence": 0.9}', *self.tokens, model=cfg.model
            )
        if step == "not_json":
            return RawResponse("Das Dokument ist eine Einzelabrechnung.", *self.tokens, model=cfg.model)
        if step.startswith("answer:"):
            return RawResponse(step[len("answer:") :], *self.tokens, model=cfg.model)
        return RawResponse(json.dumps(self.answer, ensure_ascii=False), *self.tokens, model=cfg.model)
