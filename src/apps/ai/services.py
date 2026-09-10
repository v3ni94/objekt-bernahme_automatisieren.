"""Stufe 3 im Ablauf (docs/architektur.md 6.1 Nr. 8, E 7.2 NACH STUFE 3): Request aus dem Dokument bilden (maskierter
Auszug, Dateiname ohne Namen, Verwaltungsart, Taxonomie, Praefixmuster, Hinweise ohne Personenbezug), Router aufrufen,
Ergebnis in document_classifications (Stufe 3) schreiben, Kombination mit der lokalen Konfidenz, Stichprobe."""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass

from apps.ai.excerpt import build_excerpt, mask_filename
from apps.ai.router import Router, RouterResult, default_router
from apps.ai.schema import ClassificationRequest, taxonomy_from_catalog
from apps.classification.confidence import Combined, thresholds
from apps.classification.context import DocContext
from apps.config import store
from apps.documents.models import Document, DocumentClassification, DocumentSubfolder, DocumentType
from apps.objects.units import parse_unit_label
from apps.parties.models import Owner, OwnerUnitAssignment, Tenant, TenantUnitAssignment

logger = logging.getLogger(__name__)


@dataclass
class Stage3Outcome:
    status: str  # ok | provider_error | budget_blocked | blocked_by_mask_check | disabled | skipped
    category: str | None = None
    subfolder: str | None = None
    document_type: str | None = None
    confidence: float = 0.0
    object_related: bool = True
    period_year: int | None = None
    provider: str | None = None
    fallback_used: bool = False
    message: str | None = None
    call_id: int | None = None
    reasoning: str | None = None


def unit_label_patterns(obj) -> list[str]:
    """Nur Praefixmuster der Einheiten (WE, GE, ST), keine konkreten Einheiten (Ue15)."""
    from apps.objects.models import Unit

    prefixes = set()
    for label in Unit.active.filter(object=obj).values_list("unit_label", flat=True):
        parsed = parse_unit_label(label)
        if parsed.prefix:
            prefixes.add(parsed.prefix.replace(" ", "").upper())
    return sorted(prefixes)


def party_names(obj) -> list[str]:
    names: list[str] = []
    owner_ids = OwnerUnitAssignment.active.filter(unit__object=obj).values_list("owner_id", flat=True)
    for o in Owner.active.filter(pk__in=owner_ids):
        names.extend(n for n in (o.last_name, o.company_name) if n)
    tenant_ids = TenantUnitAssignment.active.filter(unit__object=obj).values_list("tenant_id", flat=True)
    for t in Tenant.active.filter(pk__in=tenant_ids):
        names.extend(n for n in (t.last_name, t.company_name) if n)
    return names


def build_request(doc: Document, ctx: DocContext, s1_payload: dict | None) -> ClassificationRequest:
    obj = doc.object
    hints = {
        "rule_candidates": sorted(
            {h.get("category") for h in (s1_payload or {}).get("hits", []) if h.get("category")}
        ),
        "has_period_year": ctx.period.year is not None,
        "has_unit": bool(ctx.unit_ids),
        "has_owner": bool(ctx.owner_ids),
        "has_iban": ctx.iban_found,
        "page_count": ctx.page_count,
        "source_folder": (ctx.folder_code or "").split("/")[0] or None,
    }
    return ClassificationRequest(
        excerpt_masked=build_excerpt(ctx.pages),
        filename_masked=mask_filename(doc.current_name or "", party_names(obj)),
        management_type=obj.management_type,
        taxonomy=taxonomy_from_catalog(),
        unit_label_patterns=unit_label_patterns(obj),
        hints=hints,
    )


def stage3_allowed(doc: Document, ctx: DocContext, local_category: str | None) -> tuple[bool, str | None]:
    """Sperren nach B-11 und F19: Ausweiskopie erkannt oder Kategorie 01 lokal sicher -> kein externer Aufruf."""
    if ctx.id_document_found and bool(store.get("classification.mask_id_documents_block_stage3", True)):
        return False, "Ausweisdaten erkannt (B-11, F19)"
    if local_category == "01":
        return False, "Legitimationsunterlagen gehen nicht an Stufe 3 (F19)"
    return True, None


def run_stage3(
    doc: Document,
    ctx: DocContext,
    *,
    s1_payload: dict | None,
    run=None,
    job=None,
    router: Router | None = None,
) -> Stage3Outcome:
    allowed, reason = stage3_allowed(doc, ctx, (s1_payload or {}).get("category"))
    if not allowed:
        return Stage3Outcome("skipped", message=reason)
    router = router or default_router()
    req = build_request(doc, ctx, s1_payload)
    masked = sum(int(h) for h in [])  # Treffer der Maskierung werden je Seite im OCR-Cache gefuehrt
    from apps.pipeline import storage

    masked = 0
    for page_no in ctx.pages:
        cached = storage.read_page(doc.sha256, page_no) if doc.sha256 else None
        masked += len(cached[1]) if cached else 0
    result: RouterResult = router.classify(
        req, obj=doc.object, document=doc, run=run, job=job, masked_entities_count=masked
    )
    if result.result is None:
        return Stage3Outcome(
            result.status,
            provider=result.provider,
            fallback_used=result.fallback_used,
            message=result.message,
            call_id=result.call.pk if result.call else None,
        )
    r = result.result
    category = r.category if r.object_related else "06"
    subfolder = r.subfolder if r.object_related else "04"
    DocumentClassification.objects.create(
        document=doc,
        stage=3,
        provider=result.provider,
        model=result.call.model if result.call else None,
        category_id=category,
        subfolder=DocumentSubfolder.objects.filter(category_id=category, code=subfolder).first()
        if subfolder
        else None,
        document_type=DocumentType.objects.filter(code=r.document_type).first() if r.document_type else None,
        period_year=r.period.year,
        scope_decision="unclear" if not r.object_related else None,
        confidence=r.confidence,
        reasoning=r.reasoning[:400],
        ai_call=result.call,
        tokens_in=result.call.tokens_in if result.call else None,
        tokens_out=result.call.tokens_out if result.call else None,
        cost_eur=result.call.cost_eur if result.call else None,
        duration_ms=result.call.duration_ms if result.call else None,
        features={
            "mentioned_units": r.mentioned_units[:20],
            "mentioned_parties_count": len(r.mentioned_parties),
            "fallback_used": result.fallback_used,
            "attempts": result.attempts,
        },
        is_final=False,
    )
    return Stage3Outcome(
        "ok",
        category,
        subfolder,
        r.document_type,
        float(r.confidence),
        r.object_related,
        r.period.year,
        result.provider,
        result.fallback_used,
        call_id=result.call.pk if result.call else None,
        reasoning=r.reasoning,
    )


def combine_after_stage3(local: Combined, s3: Stage3Outcome) -> tuple[Combined, bool]:
    """NACH STUFE 3 (E 7.2): Uebereinstimmung hebt die Konfidenz, Widerspruch ab threshold_stage3_override uebernimmt
    die KI-Kategorie (decided_by ai, Stichprobenflag), sonst bleibt es unter t_auto (06/01_Unklar)."""
    t = thresholds()
    if s3.status != "ok":
        return local, False
    if local.category is not None and s3.category == local.category:
        c = min(1.0, max(local.confidence, s3.confidence) + t["bonus_agree"])
        return Combined(
            local.category,
            round(c, 4),
            f"{local.reason}; Stufe 3 stimmt zu ({s3.provider})",
            decided_by=local.decided_by,
        ), False
    if s3.confidence >= t["threshold_stage3_override"]:
        return Combined(
            s3.category,
            round(s3.confidence, 4),
            f"Stufe 3 entscheidet ({s3.provider}): {s3.reasoning or ''}"[:300],
            decided_by="stage3",
        ), True
    return Combined(
        local.category,
        local.confidence,
        f"{local.reason}; Stufe 3 widerspricht ohne ausreichende Konfidenz",
        decided_by=local.decided_by,
    ), False


def sample_for_review() -> bool:
    pct = int(store.get("classification.ai_sample_pct", 10))
    return random.random() * 100 < pct  # noqa: S311


def object_costs() -> list[dict]:
    """Kosten je Objekt fuer die Statusseite (Summe ai_calls.cost_eur, Anzahl, letzter Fehler)."""
    from django.db.models import Count, Max, Sum

    from apps.ai.models import AiCall

    rows = (
        AiCall.objects.values("object__object_number", "provider")
        .annotate(cost=Sum("cost_eur"), calls=Count("id"), last=Max("requested_at"))
        .order_by("object__object_number", "provider")
    )
    return list(rows)


def recent_errors(limit: int = 10):
    from apps.ai.models import AiCall

    return AiCall.objects.exclude(status="ok").order_by("-requested_at")[:limit]
