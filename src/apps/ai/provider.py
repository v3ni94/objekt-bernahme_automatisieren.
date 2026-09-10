"""Provider-Schnittstelle der Stufe 3 (E 4.1, docs/architektur.md 6.7): Protocol, Konfiguration je Anbieter aus
app_settings, Preisliste (versioniert), Basisklasse mit zweiter Maskierungspruefung unmittelbar vor dem Senden (B-11)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

from apps.ai.prompt import build_messages
from apps.ai.schema import ClassificationRequest, ClassificationResult, SchemaViolation, parse_result
from apps.config import store
from objektakte.masking import contains_sensitive


class ProviderError(Exception):
    """Anbieterfehler (5xx, Netz), Wiederholung erlaubt."""

    def __init__(self, message: str, *, http_status: int | None = None, retryable: bool = True):
        super().__init__(message)
        self.http_status = http_status
        self.retryable = retryable


class ProviderTimeout(ProviderError):
    def __init__(self, message: str = "Zeitüberschreitung"):
        super().__init__(message, http_status=None, retryable=True)


class RateLimited(ProviderError):
    def __init__(self, message: str = "Ratenbegrenzung"):
        super().__init__(message, http_status=429, retryable=True)


class MaskCheckFailed(Exception):
    """Zweite Maskierungspruefung hat sensible Muster gefunden; Aufruf wird nicht gesendet."""


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    enabled: bool = False
    model: str | None = None
    endpoint: str | None = None
    region: str | None = None
    timeout_s: float = 30.0
    max_attempts: int = 2
    cost_limit_eur_per_object: Decimal | None = None

    @classmethod
    def from_settings(cls, name: str) -> ProviderConfig:
        raw = (store.get("ai.providers", {}) or {}).get(name) or {}
        limit = raw.get("cost_limit_eur_per_object")
        return cls(
            name=name,
            enabled=bool(raw.get("enabled")),
            model=raw.get("model"),
            endpoint=raw.get("endpoint"),
            region=raw.get("region"),
            timeout_s=float(raw.get("timeout_s") or 30),
            max_attempts=int(raw.get("max_attempts") or 2),
            cost_limit_eur_per_object=Decimal(str(limit)) if limit is not None else None,
        )


@dataclass(frozen=True)
class PriceList:
    """Preis je 1.000 Token in EUR je Modell (ai.price_list, vom Auftraggeber freigegeben); Version fuer ai_calls."""

    version: str
    prices: dict[str, tuple[Decimal, Decimal]] = field(default_factory=dict)  # model -> (eingang, ausgang)

    @classmethod
    def from_settings(cls) -> PriceList:
        raw = store.get("ai.price_list", {}) or {}
        prices = {}
        for model, entry in (raw.get("models") or {}).items():
            prices[model] = (
                Decimal(str(entry.get("input_per_1k", 0))),
                Decimal(str(entry.get("output_per_1k", 0))),
            )
        return cls(version=str(raw.get("version") or "unbekannt"), prices=prices)

    def cost(self, model: str | None, tokens_in: int, tokens_out: int) -> Decimal:
        inp, out = self.prices.get(model or "", (Decimal(0), Decimal(0)))
        return (Decimal(tokens_in) / 1000 * inp + Decimal(tokens_out) / 1000 * out).quantize(
            Decimal("0.000001")
        )


@dataclass
class RawResponse:
    text: str
    tokens_in: int
    tokens_out: int
    http_status: int | None = 200
    model: str | None = None


@dataclass
class ProviderOutcome:
    result: ClassificationResult | None
    raw: RawResponse | None
    latency_ms: int
    prompt_hash: str
    prompt_chars: int
    error: Exception | None = None
    masked_prompt: str | None = None


class ClassificationProvider(Protocol):
    name: str

    def classify(self, req: ClassificationRequest, cfg: ProviderConfig) -> ProviderOutcome: ...

    def estimate_cost_eur(
        self, tokens_in: int, tokens_out: int, price_list: PriceList, model: str | None
    ) -> Decimal: ...


class BaseProvider:
    """Basisklasse: Maskierungspruefung vor dem Senden, Zeitmessung, Schema-Validierung mit Reparaturhinweis."""

    name = "base"

    def send(self, system: str, user: str, cfg: ProviderConfig) -> RawResponse:  # pragma: no cover
        raise NotImplementedError

    def classify(
        self, req: ClassificationRequest, cfg: ProviderConfig, *, repair_hint: str | None = None
    ) -> ProviderOutcome:
        from apps.ai.prompt import prompt_hash

        system, user = build_messages(req, repair_hint=repair_hint)
        if contains_sensitive(user) or contains_sensitive(req.filename_masked):
            raise MaskCheckFailed("Sensible Muster im Request (IBAN, Kontonummer oder Ausweisnummer)")
        started = time.monotonic()
        raw = self.send(system, user, cfg)
        latency = int((time.monotonic() - started) * 1000)
        try:
            result = parse_result(raw.text, req.taxonomy)
        except SchemaViolation as exc:
            return ProviderOutcome(
                None, raw, latency, prompt_hash(system, user), len(system) + len(user), error=exc
            )
        return ProviderOutcome(result, raw, latency, prompt_hash(system, user), len(system) + len(user))

    def estimate_cost_eur(
        self, tokens_in: int, tokens_out: int, price_list: PriceList, model: str | None
    ) -> Decimal:
        return price_list.cost(model, tokens_in, tokens_out)


def api_key_for(name: str) -> str | None:
    from objektakte.secrets import read_secret

    return read_secret(f"{name.upper()}_API_KEY", default=None)
