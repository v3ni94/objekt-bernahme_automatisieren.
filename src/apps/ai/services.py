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
    lease: dict | None = None  # Vertragsdaten eines Mieterdokuments (12.09.2026), lokal abgeglichen
    related_forced: bool = False  # object_related false verworfen (Anschrift unbekannt, 24.09.2026)


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


def _address_line(street, house_number, postal_code, city) -> str | None:
    if not (street or "").strip():
        return None
    strasse = " ".join(p for p in [(street or "").strip(), (house_number or "").strip()] if p)
    ort = " ".join(p for p in [(postal_code or "").strip(), (city or "").strip()] if p)
    return f"{strasse}, {ort}" if ort else strasse


def object_context(obj) -> dict:
    """Objektangaben fuer die KI ohne Personenbezug (24.09.2026): Objektnummer und Anschriften (Hauptanschrift und
    weitere Anschriften desselben Gebaeudes). Die Bezeichnung des Objekts bleibt weg, sie kann Personennamen tragen.
    anschrift_bekannt false: die KI darf object_related nicht verneinen, und run_stage3 ignoriert ein false."""
    anschriften = []
    haupt = _address_line(obj.street, obj.house_number, obj.postal_code, obj.city)
    if haupt:
        anschriften.append(haupt)
    for a in obj.additional_addresses or []:
        if not isinstance(a, dict):
            continue
        zeile = _address_line(a.get("street"), a.get("house_number"), a.get("postal_code"), a.get("city"))
        if zeile and zeile not in anschriften:
            anschriften.append(zeile)
    return {
        "objektnummer": obj.object_number,
        "anschriften": anschriften,
        "anschrift_bekannt": bool(anschriften),
    }


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
    names = party_names(obj)
    if ctx.email is not None:
        # E-Mail (26.09.2026): Kennzeichen und bereinigter Betreff als Hinweis, Parteinamen wie im Dateinamen
        # maskiert (Ue15, Gegenpruefung 26.09.2026); Absender und Empfaenger gehen nicht als Hinweis mit
        hints["is_email"] = True
        hints["email_subject"] = mask_filename((ctx.email.get("subject_clean") or "")[:120], names) or None
    return ClassificationRequest(
        excerpt_masked=build_excerpt(ctx.pages),
        filename_masked=mask_filename(doc.current_name or "", names),
        management_type=obj.management_type,
        taxonomy=taxonomy_from_catalog(),
        unit_label_patterns=unit_label_patterns(obj),
        hints=hints,
        object_context=object_context(obj),
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
    # Ohne bekannte Anschrift hat die KI keine Grundlage fuer "kein Objektbezug" (Befund 24.09.2026: 4.303
    # Dokumente so nach 06 gelegt, davon 963 in Objekten ohne erfasste Strasse): das false wird verworfen, ausser
    # der Text nennt lokal erkannt eine fremde Objektnummer (dann steht die Aussage der KI nicht allein)
    related = r.object_related or (not req.address_known and not ctx.foreign_object_numbers)
    category = r.category if related else "06"
    subfolder = r.subfolder if related else "04"
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
        scope_decision="unclear" if not related else None,
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
            "lease_extracted": bool(r.lease and not r.lease.empty),
            "object_related_ignored": bool(related and not r.object_related),
        },
        is_final=False,
    )
    return Stage3Outcome(
        "ok",
        category,
        subfolder,
        r.document_type,
        float(r.confidence),
        related,
        r.period.year,
        result.provider,
        result.fallback_used,
        call_id=result.call.pk if result.call else None,
        reasoning=r.reasoning,
        lease=r.lease.model_dump() if r.lease and not r.lease.empty else None,
        related_forced=bool(related and not r.object_related),
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
    if s3.category == "06" and s3.object_related and local.category not in (None, "06"):
        # „06“ mit Objektbezug heisst nur: die KI ist unsicher. Das ist kein Gegenvorschlag und darf den lokalen
        # Kandidaten nicht verdraengen (24.09.2026: sonst Fall manual_check ohne Vorschlag statt below_threshold mit
        # dem Regelkandidaten als Umzugsvorschlag fuer die Sammelaktion)
        return Combined(
            local.category,
            local.confidence,
            f"{local.reason}; Stufe 3 ohne eindeutige Zuordnung ({s3.provider})",
            decided_by=local.decided_by,
        ), False
    if s3.confidence >= t["threshold_stage3_override"]:
        return Combined(
            s3.category,
            round(s3.confidence, 4),
            f"Stufe 3 entscheidet ({s3.provider}): {s3.reasoning or ''}"[:300],
            decided_by="stage3",
        ), True
    if local.category is None:
        # kein lokaler Kandidat: die KI-Antwort ist der einzige Vorschlag und traegt ihre eigene Konfidenz; unter
        # threshold_auto_file entsteht 06/01 mit dem Vorschlag als Ziel fuer das Pruefcenter (24.09.2026)
        return Combined(
            s3.category,
            round(s3.confidence, 4),
            f"Stufe 3 schlaegt vor ({s3.provider}): {s3.reasoning or ''}"[:300],
            decided_by="stage3",
        ), False
    # Widerspruch unterhalb der Ueberschreibschwelle: der lokale Kandidat bleibt, verliert aber an Konfidenz, damit
    # eine widersprochene Hinweisregel (0,8 bis 0,85) nicht ohne Bestaetigung ablegt (24.09.2026)
    c = max(0.0, local.confidence - t["malus_disagree"])
    return Combined(
        local.category,
        round(c, 4),
        f"{local.reason}; Stufe 3 widerspricht ({s3.provider}, {s3.category or '-'} mit {s3.confidence:.2f})",
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
