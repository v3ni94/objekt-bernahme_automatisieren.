"""Router der Stufe 3 (E 4.2, docs/architektur.md 6.7): Primaer bis max_attempts (zweiter Versuch nur bei Timeout, 5xx,
429 oder Schemaverletzung mit Reparaturhinweis), dann Fallback mit identischem Request; Circuit Breaker je Provider im
Cache; Gesamtbudget je Dokument; Kostenlimit je Provider und Objekt gegen SUM(cost_eur); Protokoll je Versuch in
ai_calls ohne Prompttext (B-07); maskierter Prompt nur bei ai.store_masked_prompts."""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal

from django.core.cache import cache
from django.db.models import Sum
from django.utils import timezone

from apps.ai.models import AiCall, AiCallStatus
from apps.ai.provider import (
    ClassificationProvider,
    MaskCheckFailed,
    PriceList,
    ProviderConfig,
    ProviderError,
    ProviderOutcome,
    ProviderTimeout,
    RateLimited,
)
from apps.ai.schema import ClassificationRequest, ClassificationResult
from apps.config import store

logger = logging.getLogger(__name__)
BREAKER_FAILURES = 3  # ANNAHME: drei Fehler in Folge oeffnen den Schalter
BREAKER_OPEN_SECONDS = 300  # ANNAHME: fuenf Minuten, danach halboffen


@dataclass
class RouterResult:
    result: ClassificationResult | None
    provider: str | None
    call: AiCall | None
    status: str  # ok | provider_error | budget_blocked | blocked_by_mask_check | disabled
    fallback_used: bool = False
    attempts: int = 0
    message: str | None = None
    calls: list[AiCall] = field(default_factory=list)


class CircuitBreaker:
    def __init__(self, name: str):
        self.key = f"ai:breaker:{name}"

    def is_open(self) -> bool:
        state = cache.get(self.key) or {}
        return bool(state.get("open_until") and state["open_until"] > time.time())

    def record_failure(self) -> None:
        state = cache.get(self.key) or {"failures": 0}
        state["failures"] = int(state.get("failures", 0)) + 1
        if state["failures"] >= BREAKER_FAILURES:
            state["open_until"] = time.time() + BREAKER_OPEN_SECONDS
            state["failures"] = 0
        cache.set(self.key, state, timeout=BREAKER_OPEN_SECONDS * 2)

    def record_success(self) -> None:
        cache.delete(self.key)


def object_cost(provider: str, object_id: int) -> Decimal:
    total = AiCall.objects.filter(provider=provider, object_id=object_id).aggregate(s=Sum("cost_eur"))["s"]
    return total or Decimal(0)


class Router:
    def __init__(
        self,
        providers: dict[str, ClassificationProvider],
        *,
        configs: dict[str, ProviderConfig] | None = None,
        price_list: PriceList | None = None,
        order: list[str] | None = None,
    ):
        self.providers = providers
        self.configs = configs or {name: ProviderConfig.from_settings(name) for name in providers}
        self.price_list = price_list or PriceList.from_settings()
        self.order = order or [p for p in (store.get("ai.provider_order", []) or []) if p in providers]
        self.wall_budget_s = float(store.get("ai.wall_budget_s", 120))
        self.store_prompts = bool(store.get("ai.store_masked_prompts", False))

    def enabled_order(self) -> list[str]:
        return [name for name in self.order if self.configs.get(name) and self.configs[name].enabled]

    def classify(
        self,
        req: ClassificationRequest,
        *,
        obj,
        document=None,
        run=None,
        job=None,
        page_from=None,
        page_to=None,
        masked_entities_count: int = 0,
    ) -> RouterResult:
        order = self.enabled_order()
        if not order:
            return RouterResult(
                None,
                None,
                None,
                "disabled",
                message="Kein Anbieter freigegeben (AVV, ai.providers.<p>.enabled)",
            )
        started = time.monotonic()
        calls: list[AiCall] = []
        previous: AiCall | None = None
        fallback_used = False
        last_message = None
        for index, name in enumerate(order):
            cfg = self.configs[name]
            provider = self.providers[name]
            breaker = CircuitBreaker(name)
            if breaker.is_open():
                last_message = f"{name}: Circuit Breaker offen"
                logger.warning("Stufe 3: %s", last_message)
                continue
            limit = cfg.cost_limit_eur_per_object
            if limit is not None and object_cost(name, obj.pk) >= limit:
                call = self._log(
                    name,
                    cfg,
                    obj,
                    document,
                    run,
                    job,
                    page_from,
                    page_to,
                    status=AiCallStatus.BUDGET_BLOCKED,
                    message=f"Kostenlimit {limit} EUR je Objekt erreicht",
                    fallback_of=previous,
                    fallback_used=index > 0,
                    masked=masked_entities_count,
                )
                calls.append(call)
                return RouterResult(
                    None,
                    name,
                    call,
                    "budget_blocked",
                    fallback_used=index > 0,
                    attempts=len(calls),
                    message=f"Kostenlimit für {name} erreicht",
                    calls=calls,
                )
            repair_hint = None
            for attempt in range(1, cfg.max_attempts + 1):
                if time.monotonic() - started > self.wall_budget_s:
                    return RouterResult(
                        None,
                        name,
                        previous,
                        "provider_error",
                        fallback_used=fallback_used,
                        attempts=len(calls),
                        message="Gesamtbudget je Dokument überschritten",
                        calls=calls,
                    )
                try:
                    outcome: ProviderOutcome = provider.classify(req, cfg, repair_hint=repair_hint)
                except MaskCheckFailed as exc:
                    call = self._log(
                        name,
                        cfg,
                        obj,
                        document,
                        run,
                        job,
                        page_from,
                        page_to,
                        status=AiCallStatus.BLOCKED_BY_MASK_CHECK,
                        message=str(exc),
                        fallback_of=previous,
                        fallback_used=index > 0,
                        masked=masked_entities_count,
                    )
                    calls.append(call)
                    return RouterResult(
                        None,
                        name,
                        call,
                        "blocked_by_mask_check",
                        fallback_used=index > 0,
                        attempts=len(calls),
                        message=str(exc),
                        calls=calls,
                    )
                except ProviderTimeout as exc:
                    call = self._log(
                        name,
                        cfg,
                        obj,
                        document,
                        run,
                        job,
                        page_from,
                        page_to,
                        status=AiCallStatus.TIMEOUT,
                        message=str(exc),
                        fallback_of=previous,
                        fallback_used=index > 0,
                        masked=masked_entities_count,
                        attempt=attempt,
                    )
                    calls.append(call)
                    previous = call
                    breaker.record_failure()
                    last_message = f"{name}: Zeitüberschreitung"
                    continue
                except RateLimited as exc:
                    call = self._log(
                        name,
                        cfg,
                        obj,
                        document,
                        run,
                        job,
                        page_from,
                        page_to,
                        status=AiCallStatus.RATE_LIMITED,
                        message=str(exc),
                        http_status=429,
                        fallback_of=previous,
                        fallback_used=index > 0,
                        masked=masked_entities_count,
                        attempt=attempt,
                    )
                    calls.append(call)
                    previous = call
                    breaker.record_failure()
                    last_message = f"{name}: Ratenbegrenzung"
                    continue
                except ProviderError as exc:
                    call = self._log(
                        name,
                        cfg,
                        obj,
                        document,
                        run,
                        job,
                        page_from,
                        page_to,
                        status=AiCallStatus.PROVIDER_ERROR,
                        message=str(exc),
                        http_status=exc.http_status,
                        fallback_of=previous,
                        fallback_used=index > 0,
                        masked=masked_entities_count,
                        attempt=attempt,
                    )
                    calls.append(call)
                    previous = call
                    breaker.record_failure()
                    last_message = f"{name}: {exc}"
                    if not exc.retryable:
                        break
                    continue
                tokens_in = outcome.raw.tokens_in if outcome.raw else 0
                tokens_out = outcome.raw.tokens_out if outcome.raw else 0
                cost = provider.estimate_cost_eur(tokens_in, tokens_out, self.price_list, cfg.model)
                if outcome.error is not None:
                    call = self._log(
                        name,
                        cfg,
                        obj,
                        document,
                        run,
                        job,
                        page_from,
                        page_to,
                        status=AiCallStatus.SCHEMA_ERROR,
                        message=str(outcome.error)[:1000],
                        fallback_of=previous,
                        fallback_used=index > 0,
                        masked=masked_entities_count,
                        attempt=attempt,
                        outcome=outcome,
                        cost=cost,
                    )
                    calls.append(call)
                    previous = call
                    repair_hint = f"Die Antwort verletzte das Schema: {str(outcome.error)[:300]}. Verwende ausschließlich Codes aus der Taxonomie und alle Pflichtfelder."
                    last_message = f"{name}: Schemaverletzung"
                    continue
                call = self._log(
                    name,
                    cfg,
                    obj,
                    document,
                    run,
                    job,
                    page_from,
                    page_to,
                    status=AiCallStatus.OK,
                    fallback_of=previous,
                    fallback_used=index > 0,
                    masked=masked_entities_count,
                    attempt=attempt,
                    outcome=outcome,
                    cost=cost,
                )
                calls.append(call)
                breaker.record_success()
                return RouterResult(
                    outcome.result,
                    name,
                    call,
                    "ok",
                    fallback_used=index > 0,
                    attempts=len(calls),
                    calls=calls,
                )
            fallback_used = True
        return RouterResult(
            None,
            order[-1] if order else None,
            previous,
            "provider_error",
            fallback_used=len(order) > 1,
            attempts=len(calls),
            message=last_message or "Alle Anbieter ausgefallen",
            calls=calls,
        )

    def _log(
        self,
        name,
        cfg: ProviderConfig,
        obj,
        document,
        run,
        job,
        page_from,
        page_to,
        *,
        status: str,
        message: str | None = None,
        http_status: int | None = None,
        fallback_of: AiCall | None = None,
        fallback_used: bool = False,
        masked: int = 0,
        attempt: int = 1,
        outcome: ProviderOutcome | None = None,
        cost: Decimal | None = None,
    ) -> AiCall:
        summary = None
        if outcome is not None and outcome.result is not None:
            r = outcome.result
            summary = {
                "category": r.category,
                "subfolder": r.subfolder,
                "document_type": r.document_type,
                "confidence": r.confidence,
                "object_related": r.object_related,
                "period_year": r.period.year,
                "units": len(r.mentioned_units),
                "parties": len(r.mentioned_parties),
            }
        elif outcome is not None and outcome.error is not None:
            summary = {"error": str(outcome.error)[:300]}
        prompt_text = None
        call = AiCall.objects.create(
            object=obj,
            document=document,
            run=run,
            job=job,
            purpose="classify",
            provider=name,
            model=cfg.model or "unbekannt",
            endpoint=cfg.endpoint,
            region=cfg.region,
            page_from=page_from,
            page_to=page_to,
            prompt_hash=outcome.prompt_hash if outcome else None,
            prompt_chars=outcome.prompt_chars if outcome else None,
            masked_entities_count=masked,
            tokens_in=outcome.raw.tokens_in if outcome and outcome.raw else None,
            tokens_out=outcome.raw.tokens_out if outcome and outcome.raw else None,
            cost_eur=cost,
            price_list_version=self.price_list.version,
            duration_ms=outcome.latency_ms if outcome else None,
            status=status,
            http_status=http_status
            if http_status is not None
            else (outcome.raw.http_status if outcome and outcome.raw else None),
            error_message=(message or "")[:1000] or None,
            fallback_used=fallback_used,
            fallback_of_call=fallback_of,
            response_summary={**(summary or {}), "attempt": attempt},
            requested_at=timezone.now(),
        )
        if prompt_text:
            call.response_summary = {
                **(call.response_summary or {}),
                "masked_prompt_sha256": hashlib.sha256(prompt_text.encode()).hexdigest(),
            }
            call.save(update_fields=["response_summary"])
        return call


def default_router() -> Router:
    from apps.ai.providers.anthropic_provider import AnthropicProvider
    from apps.ai.providers.openai_provider import OpenAIProvider

    return Router({"openai": OpenAIProvider(), "anthropic": AnthropicProvider()})


def month_costs() -> dict[str, Decimal]:
    start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    rows = AiCall.objects.filter(requested_at__gte=start).values("provider").annotate(s=Sum("cost_eur"))
    return {r["provider"]: r["s"] or Decimal(0) for r in rows}
