"""Review Center (H 2, docs/architektur.md 8): Arbeitsliste mit Filtern und Keyset-Paginierung, Zielbildung aus dem
Vorschlag, Validierung mit Warnung bei Widerspruch zur Stichtagsabfrage (CR 7), Aktionen nach H 2.4 in einer
Transaktion (review_decisions, review_cases, audit_events; Klassifikation Stufe 4; Verknuepfungen; Trainingsdatum);
Drive-Schreibvorgaenge nie in der Anfrage (Job file_to_drive)."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from apps.audit.services import record
from apps.classification.ownerfiles import (
    owner_file_for_assignments,
    owner_file_unassigned,
    owner_file_unknown_unit,
)
from apps.config import store
from apps.documents.models import (
    Document,
    DocumentClassification,
    DocumentOwnerLink,
    DocumentPage,
    DocumentSubfolder,
    DocumentTenantLink,
    DocumentType,
    TrainingSample,
)
from apps.objects.models import Unit
from apps.parties import services as party_services
from apps.parties.models import Owner, OwnerUnitAssignment, Tenant
from apps.pipeline.jobs import JobType, enqueue, idempotency_key
from apps.review import hooks
from apps.review.models import CaseStatus, CaseType, DecisionType, ReviewCase, ReviewDecision

PAGE_SIZE = 50  # ANNAHME A-29
DEFAULT_SNOOZE_DAYS = 7  # ANNAHME H 2.4
OBJECT_CASE_TYPES = (CaseType.DUPLICATE_OBJECT_NUMBER, CaseType.DRIVE_STRUCTURE)


class ReviewError(ValueError):
    pass


# ---------------------------------------------------------------- Liste
@dataclass
class ListFilters:
    objects: list[int] = field(default_factory=list)
    case_type: str | None = None
    subtype: str | None = None
    status: list[str] = field(default_factory=lambda: [CaseStatus.OPEN, CaseStatus.IN_PROGRESS])
    assigned_to: int | None = None
    target: str | None = None  # Zielbereich des Vorschlags 01 bis 06
    band: str | None = None  # hoch | mittel | niedrig
    created_from: date | None = None
    created_to: date | None = None
    year: int | None = None
    grouped_only: bool = False
    q: str | None = None
    include_snoozed: bool = False
    mine: bool = False

    @classmethod
    def from_params(cls, params, user=None) -> ListFilters:
        f = cls()
        f.objects = [int(v) for v in params.getlist("objekt") if str(v).isdigit()]
        f.case_type = params.get("fallart") or None
        f.subtype = params.get("unterfall") or None
        status = [s for s in params.getlist("status") if s in CaseStatus.values]
        if status:
            f.status = status
        f.assigned_to = int(params["bearbeiter"]) if str(params.get("bearbeiter", "")).isdigit() else None
        f.target = params.get("ziel") or None
        f.band = params.get("band") or None
        for key, attr in (("von", "created_from"), ("bis", "created_to")):
            raw = params.get(key)
            if raw:
                try:
                    setattr(f, attr, date.fromisoformat(raw))
                except ValueError:
                    pass
        f.year = int(params["jahr"]) if str(params.get("jahr", "")).isdigit() else None
        f.grouped_only = params.get("gruppen") == "1"
        f.q = (params.get("q") or "").strip() or None
        f.include_snoozed = params.get("zurueckgestellt") == "1"
        f.mine = params.get("meine") == "1"
        return f

    def as_dict(self) -> dict:
        d = asdict(self)
        d["created_from"] = self.created_from.isoformat() if self.created_from else None
        d["created_to"] = self.created_to.isoformat() if self.created_to else None
        return d


def case_queryset(filters: ListFilters, user=None):
    qs = ReviewCase.objects.select_related("object", "document", "assigned_to", "misc_subfolder")
    if filters.objects:
        qs = qs.filter(object_id__in=filters.objects)
    if filters.case_type:
        qs = qs.filter(case_type=filters.case_type)
    if filters.subtype:
        qs = qs.filter(case_subtype=filters.subtype)
    if filters.status:
        qs = qs.filter(status__in=filters.status)
    if filters.assigned_to:
        qs = qs.filter(assigned_to_id=filters.assigned_to)
    if filters.mine and user is not None:
        qs = qs.filter(assigned_to=user)
    if filters.target:
        qs = qs.filter(
            Q(context__intended__category=filters.target)
            | Q(context__target_category=filters.target)
            | Q(document__category_id=filters.target)
        )
    if filters.band:
        low, high = {"hoch": (0.9, 1.01), "mittel": (0.6, 0.9), "niedrig": (-0.01, 0.6)}.get(
            filters.band, (-1, 2)
        )
        qs = qs.filter(context__confidence__gte=low, context__confidence__lt=high)
    if filters.created_from:
        qs = qs.filter(created_at__date__gte=filters.created_from)
    if filters.created_to:
        qs = qs.filter(created_at__date__lte=filters.created_to)
    if filters.year:
        qs = qs.filter(Q(document__period_year=filters.year) | Q(context__period_year=filters.year))
    if filters.grouped_only:
        qs = qs.filter(batch_key__isnull=False)
    if filters.q:
        qs = qs.filter(
            Q(document__current_name__icontains=filters.q)
            | Q(context__reason__icontains=filters.q)
            | Q(case_subtype__icontains=filters.q)
        )
    if not filters.include_snoozed:
        qs = qs.filter(Q(snoozed_until__isnull=True) | Q(snoozed_until__lte=timezone.now()))
    return qs


def encode_cursor(case: ReviewCase) -> str:
    raw = json.dumps([case.priority, case.created_at.isoformat(), case.pk])
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_cursor(cursor: str | None):
    if not cursor:
        return None
    try:
        priority, created, pk = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
        from datetime import datetime

        return int(priority), datetime.fromisoformat(created), int(pk)
    except Exception:
        return None


def page(filters: ListFilters, *, cursor: str | None = None, user=None, size: int = PAGE_SIZE):
    """Keyset-Paginierung nach (priority, created_at, id) ueber ix_review_queue (H 2.2)."""
    qs = case_queryset(filters, user).order_by("priority", "created_at", "id")
    key = decode_cursor(cursor)
    if key:
        prio, created, pk = key
        qs = qs.filter(
            Q(priority__gt=prio)
            | Q(priority=prio, created_at__gt=created)
            | Q(priority=prio, created_at=created, id__gt=pk)
        )
    rows = list(qs[: size + 1])
    has_more = len(rows) > size
    rows = rows[:size]
    next_cursor = encode_cursor(rows[-1]) if has_more and rows else None
    return rows, next_cursor


def counters(filters: ListFilters, user=None) -> dict:
    base = case_queryset(ListFilters(status=filters.status, include_snoozed=filters.include_snoozed), user)
    by_type = {r["case_type"]: r["c"] for r in base.values("case_type").annotate(c=Count("id"))}
    by_object = {
        r["object__object_number"]: r["c"]
        for r in base.values("object__object_number")
        .annotate(c=Count("id"))
        .order_by("object__object_number")
    }
    filtered = case_queryset(filters, user)
    return {
        "total": sum(by_type.values()),
        "filtered": filtered.count(),
        "grouped": filtered.filter(batch_key__isnull=False).count(),
        "by_type": by_type,
        "by_object": by_object,
    }


def group_sizes(cases: list[ReviewCase]) -> dict[str, int]:
    keys = [c.batch_key for c in cases if c.batch_key]
    if not keys:
        return {}
    return {
        r["batch_key"]: r["c"]
        for r in ReviewCase.objects.filter(
            batch_key__in=keys, status__in=[CaseStatus.OPEN, CaseStatus.IN_PROGRESS]
        )
        .values("batch_key")
        .annotate(c=Count("id"))
    }


# ---------------------------------------------------------------- Ziel
@dataclass
class Target:
    category: str | None = None
    subfolder: str | None = None
    document_type: str | None = None
    period_year: int | None = None
    period_from: date | None = None
    period_to: date | None = None
    owner_id: int | None = None
    unit_id: int | None = None
    assignment_id: int | None = None
    tenant_id: int | None = None
    owner_unknown: bool = False
    unit_unknown: bool = False
    new_assignment_from: date | None = None
    new_assignment_to: date | None = None
    reason: str | None = None

    def as_dict(self) -> dict:
        d = asdict(self)
        for k in ("period_from", "period_to", "new_assignment_from", "new_assignment_to"):
            d[k] = d[k].isoformat() if d[k] else None
        return d

    @classmethod
    def from_post(cls, data) -> Target:
        def _int(name):
            v = data.get(name)
            return int(v) if v not in (None, "", "None") and str(v).isdigit() else None

        def _date(name):
            v = data.get(name)
            try:
                return date.fromisoformat(v) if v else None
            except ValueError as exc:
                raise ReviewError(f"Datum {name} im Format JJJJ-MM-TT eingeben") from exc

        return cls(
            category=data.get("category") or None,
            subfolder=data.get("subfolder") or None,
            document_type=data.get("document_type") or None,
            period_year=_int("period_year"),
            period_from=_date("period_from"),
            period_to=_date("period_to"),
            owner_id=_int("owner_id"),
            unit_id=_int("unit_id"),
            assignment_id=_int("assignment_id"),
            tenant_id=_int("tenant_id"),
            owner_unknown=data.get("owner_unknown") == "1",
            unit_unknown=data.get("unit_unknown") == "1",
            new_assignment_from=_date("new_assignment_from"),
            new_assignment_to=_date("new_assignment_to"),
            reason=(data.get("reason") or "").strip() or None,
        )


def proposal_target(case: ReviewCase) -> Target:
    """Vorschlag des Systems als Zielvorgabe: finale Klassifikation (fachliche Kategorie), Zeitbezug, Verknuepfung."""
    doc = case.document
    t = Target()
    if doc is None:
        return t
    final = DocumentClassification.objects.filter(document=doc, is_final=True).order_by("-id").first()
    if final is not None:
        t.category = final.category_id
        t.subfolder = final.subfolder.code if final.subfolder else None
        t.document_type = final.document_type.code if final.document_type else None
        t.period_year = final.period_year
    intended = (case.context or {}).get("intended") or {}
    segment = (case.context or {}).get("segment") or {}
    if case.page_from and segment:
        # Segment eines Gesamtdokuments: Ziel ist die Eigentuemerakte, nicht die Kategorie der Masterdatei
        t.category, t.subfolder, t.document_type = (
            "05",
            segment.get("subfolder"),
            segment.get("document_type"),
        )
    else:
        t.category = intended.get("category") or t.category or doc.category_id
        t.subfolder = intended.get("subfolder") or t.subfolder
        t.document_type = intended.get("document_type") or t.document_type
    t.period_year = t.period_year or doc.period_year
    t.period_from, t.period_to = doc.period_from, doc.period_to
    if t.category == "06" and doc.subfolder_id and case.misc_subfolder_id:
        t.subfolder = case.misc_subfolder.code
    link = (
        DocumentOwnerLink.objects.filter(
            document=doc, deleted_at__isnull=True, page_from=case.page_from, page_to=case.page_to
        )
        .order_by("id")
        .first()
    )
    if link is not None:
        t.owner_id, t.unit_id, t.assignment_id = link.owner_id, link.unit_id, link.assignment_id
    cands = case.candidates or []
    if len(cands) == 1:
        t.owner_id = t.owner_id or cands[0].get("owner_id")
        t.unit_id = t.unit_id or cands[0].get("unit_id")
        t.assignment_id = t.assignment_id or cands[0].get("assignment_id")
    proposal = case.proposed_action or {}
    if proposal.get("unit_id") and not t.unit_id:
        t.unit_id = proposal["unit_id"]
    if proposal.get("owner_ids") and not t.owner_id:
        t.owner_id = proposal["owner_ids"][0]
    tl = DocumentTenantLink.objects.filter(document=doc, deleted_at__isnull=True).first()
    if tl is not None:
        t.tenant_id = tl.tenant_id
    return t


def validate(case: ReviewCase, target: Target) -> tuple[list[str], list[str]]:
    """Fehler blockieren, Warnungen werden mit der Entscheidung protokolliert (CR 7 Zusatzpruefung 5)."""
    errors: list[str] = []
    warnings: list[str] = []
    doc = case.document
    if target.category not in {"01", "02", "03", "04", "05", "06"}:
        errors.append("Zielbereich 01 bis 06 wählen")
        return errors, warnings
    dtype = DocumentType.objects.filter(code=target.document_type).first() if target.document_type else None
    if target.category in ("01", "02", "03", "04", "05"):
        if (
            dtype is None
            and DocumentType.objects.filter(category_id=target.category, is_active=True).exists()
        ):
            errors.append("Unterart wählen")
        elif dtype is not None and dtype.category_id != target.category:
            errors.append("Unterart passt nicht zum Zielbereich")
    if target.category == "05":
        if target.subfolder is None and dtype is not None and dtype.subfolder_id:
            target.subfolder = dtype.subfolder.code
        if not target.subfolder:
            errors.append("Unterordner 01 bis 11 wählen")
        if not target.owner_id and not target.owner_unknown:
            errors.append("Eigentümer wählen oder ausdrücklich als unbekannt kennzeichnen")
        if not target.unit_id and not target.unit_unknown:
            errors.append("Einheit wählen oder ausdrücklich als unbekannt kennzeichnen")
    if target.category == "04" and not target.tenant_id:
        errors.append("Mieter wählen")
    if target.category == "06":
        if not target.subfolder:
            errors.append("Unterordner in 06_Sonstiges wählen")
        if not target.reason:
            errors.append("Begründung angeben")
    if dtype is not None and dtype.requires_period and not target.period_year and not target.period_from:
        errors.append("Jahr oder Zeitraum ist für diese Unterart Pflicht")
    if target.category == "05" and target.owner_id and target.unit_id and doc is not None:
        assignments = list(
            OwnerUnitAssignment.active.filter(
                owner_id=target.owner_id, unit_id=target.unit_id
            ).select_related("owner")
        )
        if not assignments and not target.new_assignment_from and not target.assignment_id:
            errors.append(
                "Keine Zuordnung dieses Eigentümers zur Einheit; neue Zuordnung anlegen (Eigentumsbeginn)"
            )
        else:
            # Widerspruch zur Stichtagsabfrage: wer war laut Datenbank Eigentuemer im Zeitraum?
            ref = party_services.owners_for_document(
                target.unit_id,
                period_year=target.period_year,
                period_from=target.period_from or doc.period_from,
                period_to=target.period_to or doc.period_to,
                document_date=doc.document_date,
            )
            if ref is not None:
                in_period = {a.owner_id: str(a.owner) for a in ref}
                if in_period and target.owner_id not in in_period:
                    warnings.append(
                        "Laut Zuordnungen war im Zeitraum des Dokuments Eigentümer: "
                        + ", ".join(sorted(in_period.values()))
                    )
    return errors, warnings


# ---------------------------------------------------------------- Aktionen
def _snapshot(doc: Document) -> dict:
    return {
        "status": doc.status,
        "category": doc.category_id,
        "subfolder": doc.subfolder.code if doc.subfolder_id else None,
        "document_type": doc.document_type.code if doc.document_type_id else None,
        "period_year": doc.period_year,
        "final_confidence": float(doc.final_confidence) if doc.final_confidence is not None else None,
        "final_decided_by": doc.final_decided_by,
    }


def _features_snapshot(doc: Document) -> dict:
    rows = DocumentClassification.objects.filter(document=doc, stage__in=[1, 2]).order_by("-id")[:4]
    return {
        f"stage{r.stage}": {
            "category": r.category_id,
            "confidence": float(r.confidence),
            "features": r.features,
        }
        for r in rows
    }


def _text_hashes(doc: Document, page_from: int | None, page_to: int | None) -> list[str]:
    qs = DocumentPage.objects.filter(document=doc)
    if page_from:
        qs = qs.filter(page_no__gte=page_from, page_no__lte=page_to or page_from)
    return list(qs.order_by("page_no").values_list("text_hash", flat=True))


def _resolve_assignment(
    target: Target, doc: Document, user
) -> tuple[OwnerUnitAssignment | None, list[OwnerUnitAssignment]]:
    """Zuordnung aus Eigentuemer und Einheit; legt bei Bedarf eine neue an (data_status confirmed, Quelle Dokument)."""
    if target.assignment_id:
        a = OwnerUnitAssignment.active.select_related("owner", "unit").get(pk=target.assignment_id)
        target.owner_id, target.unit_id = a.owner_id, a.unit_id
        group = list(
            OwnerUnitAssignment.active.filter(
                unit=a.unit, valid_from=a.valid_from, valid_to=a.valid_to
            ).select_related("owner")
        )
        return a, group
    if not (target.owner_id and target.unit_id):
        return None, []
    existing = list(
        OwnerUnitAssignment.active.filter(owner_id=target.owner_id, unit_id=target.unit_id)
        .select_related("owner", "unit")
        .order_by("-valid_from")
    )
    if existing:
        a = existing[0]
    else:
        if not target.new_assignment_from:
            raise ReviewError("Eigentumsbeginn für die neue Zuordnung angeben")
        a = party_services.create_assignment(
            owner=Owner.objects.get(pk=target.owner_id),
            unit=Unit.objects.get(pk=target.unit_id),
            valid_from=target.new_assignment_from,
            valid_to=target.new_assignment_to,
            data_status="confirmed",
            user=user,
            confirmed=True,
            source_document=doc,
        ).assignment
    group = list(
        OwnerUnitAssignment.active.filter(
            unit_id=a.unit_id, valid_from=a.valid_from, valid_to=a.valid_to
        ).select_related("owner")
    )
    return a, group


def apply_decision(
    case: ReviewCase,
    user,
    target: Target,
    *,
    request=None,
    is_bulk: bool = False,
    bulk_key: str | None = None,
    warnings: list[str] | None = None,
) -> ReviewDecision:
    """Bestaetigen oder Umklassifizieren (H 2.4): eine Transaktion fuer Klassifikation Stufe 4, Dokument, Verknuepfungen,
    review_decisions, review_cases, audit_events und Trainingsdatum; Drive-Verschiebung als Job."""
    errors, auto_warnings = validate(case, target)
    if errors:
        raise ReviewError("; ".join(errors))
    warnings = list(warnings or []) + auto_warnings
    with transaction.atomic():
        case = ReviewCase.objects.select_for_update().select_related("document", "object").get(pk=case.pk)
        if case.status in (CaseStatus.RESOLVED, CaseStatus.DISMISSED):
            raise ReviewError("Fall ist bereits erledigt")
        doc = Document.objects.select_for_update().get(pk=case.document_id)
        obj = case.object or doc.object
        before = _snapshot(doc)
        proposal = proposal_target(case)
        system_correct = (
            proposal.category,
            proposal.subfolder,
            proposal.document_type,
            proposal.owner_id,
            proposal.unit_id,
        ) == (
            target.category,
            target.subfolder,
            target.document_type,
            target.owner_id,
            target.unit_id,
        )
        decision_type = DecisionType.CONFIRM if system_correct else DecisionType.CORRECT
        segment = bool(case.page_from)
        subfolder = (
            DocumentSubfolder.objects.filter(category_id=target.category, code=target.subfolder).first()
            if target.subfolder
            else None
        )
        dtype = (
            DocumentType.objects.filter(code=target.document_type).first() if target.document_type else None
        )
        # Verknuepfungen
        links: list[DocumentOwnerLink] = []
        owner_file = None
        physical = {"category": target.category, "subfolder": target.subfolder}
        if target.category == "05":
            if target.owner_id and target.unit_id:
                assignment, group = _resolve_assignment(target, doc, user)
                unit = assignment.unit
                owner_file = owner_file_for_assignments(unit, group)
                for a in group:
                    links.append(
                        DocumentOwnerLink(owner_id=a.owner_id, unit=unit, assignment=a, owner_file=owner_file)
                    )
                decision_type = (
                    decision_type
                    if system_correct
                    else (DecisionType.ASSIGN_OWNER if proposal.category == "05" else DecisionType.CORRECT)
                )
            elif target.owner_id and target.unit_unknown:
                owner = Owner.objects.get(pk=target.owner_id)
                owner_file = owner_file_unknown_unit(obj, owner)
                links.append(DocumentOwnerLink(owner=owner, owner_file=owner_file))
            elif target.unit_id and target.owner_unknown:
                unit = Unit.objects.get(pk=target.unit_id)
                owner_file = owner_file_unassigned(obj)
                links.append(DocumentOwnerLink(unit=unit, owner_file=owner_file))
            else:
                owner_file = owner_file_unassigned(obj)
            physical["owner_file_id"] = owner_file.pk if owner_file else None
        elif target.category == "04" and target.tenant_id:
            decision_type = decision_type if system_correct else DecisionType.ASSIGN_TENANT
        # Klassifikation Stufe 4
        DocumentClassification.objects.filter(document=doc, is_final=True).update(is_final=False)
        final = DocumentClassification.objects.create(
            document=doc,
            page_from=case.page_from,
            page_to=case.page_to,
            stage=4,
            provider="human",
            category_id=target.category,
            subfolder=subfolder,
            document_type=dtype,
            period_year=target.period_year,
            scope_decision={"05": "owner", "04": "tenant", "03": "accounting", "06": "unclear"}.get(
                target.category, "object"
            ),
            confidence=1.0,
            reasoning=target.reason or ("Vorschlag bestätigt" if system_correct else "Vorschlag korrigiert"),
            features={"physical": physical, "warnings": warnings, "case_id": case.pk, "review": True},
            is_final=not segment,
            decided_by=user,
        )
        # Dokument (Segmente lassen die Masterdatei unveraendert, CR 6)
        if not segment:
            doc.category_id = target.category
            doc.subfolder = subfolder
            doc.document_type = dtype
            doc.period_year = target.period_year
            if target.period_from:
                doc.period_from, doc.period_to = target.period_from, target.period_to
            doc.final_confidence = 1.0
            doc.final_decided_by = "human"
            doc.status = (
                "classified" if doc.status in ("review", "classified", "filed", "ocr_done") else doc.status
            )
            doc.save()
        DocumentOwnerLink.objects.filter(
            document=doc, page_from=case.page_from, page_to=case.page_to, status="suggested"
        ).delete()
        if target.category != "05":
            DocumentOwnerLink.objects.filter(
                document=doc, page_from=case.page_from, page_to=case.page_to, status="confirmed"
            ).update(deleted_at=timezone.now(), delete_reason="Review: anderer Zielbereich")
        for link in links:
            link.document = doc
            link.link_kind = "page_range" if segment else "whole_document"
            link.page_from, link.page_to = case.page_from, case.page_to
            link.subfolder = subfolder
            link.document_type = dtype
            link.period_year = target.period_year
            link.period_from, link.period_to = (
                target.period_from or doc.period_from,
                target.period_to or doc.period_to,
            )
            link.document_date = doc.document_date
            link.confidence = 1.0
            link.status = "confirmed"
            link.classification = final
            link.review_case = case
            link.confirmed_by = user
            link.confirmed_at = timezone.now()
            link.created_by = user
            link.save()
        if target.category == "04" and target.tenant_id:
            DocumentTenantLink.objects.filter(document=doc, status="suggested").delete()
            DocumentTenantLink.objects.update_or_create(
                document=doc,
                tenant_id=target.tenant_id,
                link_kind="whole_document",
                defaults={
                    "unit_id": target.unit_id,
                    "confidence": 1.0,
                    "status": "confirmed",
                    "classification": final,
                    "confirmed_by": user,
                    "confirmed_at": timezone.now(),
                },
            )
        # Fall und Entscheidung
        case.status = CaseStatus.RESOLVED
        case.resolved_by = user
        case.resolved_at = timezone.now()
        case.resolution = {"decision": decision_type, "target": target.as_dict(), "warnings": warnings}
        case.save(update_fields=["status", "resolved_by", "resolved_at", "resolution", "updated_at"])
        decision = ReviewDecision.objects.create(
            review_case=case,
            document=doc,
            page_from=case.page_from,
            page_to=case.page_to,
            decision_type=decision_type,
            decided_by=user,
            decided_at=timezone.now(),
            is_bulk=is_bulk,
            bulk_key=bulk_key,
            before_state={"document": before, "proposal": proposal.as_dict()},
            after_state={"target": target.as_dict(), "warnings": warnings, "physical": physical},
            features_snapshot=_features_snapshot(doc),
            text_hashes=_text_hashes(doc, case.page_from, case.page_to),
            label_category_id=target.category,
            label_subfolder=subfolder,
            label_document_type=dtype,
            label_owner_id=target.owner_id,
            label_unit_id=target.unit_id,
            label_assignment_id=links[0].assignment_id if links and links[0].assignment_id else None,
            label_period_year=target.period_year,
            label_scope=final.scope_decision,
            system_was_correct=system_correct,
        )
        # Trainingsdatum (CR 11): auch Bestaetigungen; Ablagen in 06 nur mit Bestaetigung (hier gegeben)
        weights = store.get("classification.label_weights", {}) or {}
        TrainingSample.objects.filter(
            document=doc, label_source="rules_high_confidence", page_from=case.page_from, page_to=case.page_to
        ).update(is_active=False)
        TrainingSample.objects.create(
            document=doc,
            page_from=case.page_from,
            page_to=case.page_to,
            text_hash=hashlib.sha256("|".join(decision.text_hashes or []).encode()).hexdigest()
            if decision.text_hashes
            else None,
            label_category_id=target.category,
            label_subfolder=subfolder,
            label_document_type=dtype,
            label_scope=final.scope_decision,
            label_source="review_decision",
            weight=float(weights.get("review", 1.0)),
            review_decision=decision,
        )
        record(
            "review.confirm" if system_correct else "review.reclassify",
            entity_type="review_case",
            entity_id=case.pk,
            object_id=obj.pk if obj else None,
            request=request,
            actor=user,
            before=before,
            after={
                "target": target.as_dict(),
                "warnings": warnings,
                "decision_id": decision.pk,
                "bulk_key": bulk_key,
            },
            reason=target.reason,
        )
        # Drive-Verschiebung als Job, nie synchron (H 2.4 Regel 3); Segmente ohne Bewegung des Masters
        if not segment and doc.status == "classified":
            enqueue(
                JobType.FILE_TO_DRIVE,
                obj,
                key=idempotency_key(JobType.FILE_TO_DRIVE, obj.pk, doc.sha256 or f"doc-{doc.pk}"),
                document=doc,
                payload={
                    "category": target.category,
                    "subfolder": target.subfolder,
                    "owner_file_id": physical.get("owner_file_id"),
                    "link_subfolder": target.subfolder,
                },
            )
        hooks.request_regeneration(obj.pk, bulk_key=bulk_key)
    return decision


def defer(
    case: ReviewCase, user, *, days: int | None = None, reason: str | None = None, request=None
) -> ReviewCase:
    until = timezone.now() + timedelta(days=days or DEFAULT_SNOOZE_DAYS)
    with transaction.atomic():
        case = ReviewCase.objects.select_for_update().get(pk=case.pk)
        case.snoozed_until = until
        case.save(update_fields=["snoozed_until", "updated_at"])
        record(
            "review.defer",
            entity_type="review_case",
            entity_id=case.pk,
            object_id=case.object_id,
            request=request,
            actor=user,
            reason=reason,
            after={"snoozed_until": until.isoformat()},
        )
    return case


def dismiss(case: ReviewCase, user, *, reason: str, request=None) -> ReviewDecision:
    if not reason or not reason.strip():
        raise ReviewError("Grund ist Pflicht")
    with transaction.atomic():
        case = ReviewCase.objects.select_for_update().get(pk=case.pk)
        if case.status in (CaseStatus.RESOLVED, CaseStatus.DISMISSED):
            raise ReviewError("Fall ist bereits erledigt")
        case.status = CaseStatus.DISMISSED
        case.resolved_by = user
        case.resolved_at = timezone.now()
        case.resolution = {"decision": "reject", "reason": reason}
        case.save(update_fields=["status", "resolved_by", "resolved_at", "resolution", "updated_at"])
        decision = ReviewDecision.objects.create(
            review_case=case,
            document=case.document,
            page_from=case.page_from,
            page_to=case.page_to,
            decision_type=DecisionType.REJECT,
            decided_by=user,
            decided_at=timezone.now(),
            before_state={"status": "open"},
            after_state={"reason": reason},
            system_was_correct=None,
        )
        action = "review.dismiss_object_case" if case.case_type in OBJECT_CASE_TYPES else "review.dismiss"
        record(
            action,
            entity_type="review_case",
            entity_id=case.pk,
            object_id=case.object_id,
            request=request,
            actor=user,
            reason=reason,
        )
    return decision


def assign(case: ReviewCase, user, assignee, *, request=None) -> ReviewCase:
    with transaction.atomic():
        case = ReviewCase.objects.select_for_update().get(pk=case.pk)
        case.assigned_to = assignee
        case.assigned_at = timezone.now()
        if case.status == CaseStatus.OPEN:
            case.status = CaseStatus.IN_PROGRESS
        case.save(update_fields=["assigned_to", "assigned_at", "status", "updated_at"])
        record(
            "review.assign",
            entity_type="review_case",
            entity_id=case.pk,
            object_id=case.object_id,
            request=request,
            actor=user,
            after={"assigned_to": assignee.pk if assignee else None},
        )
    return case


def reopen(case: ReviewCase, user, *, reason: str | None = None, request=None) -> ReviewDecision:
    with transaction.atomic():
        case = ReviewCase.objects.select_for_update().select_related("document").get(pk=case.pk)
        if case.status not in (CaseStatus.RESOLVED, CaseStatus.DISMISSED):
            raise ReviewError("Nur erledigte oder verworfene Fälle können wiedereröffnet werden")
        doc = case.document
        before = _snapshot(doc) if doc else None
        if doc is not None:
            DocumentClassification.objects.filter(
                document=doc, stage=4, is_final=True, page_from=case.page_from, page_to=case.page_to
            ).update(is_final=False)
            previous = (
                DocumentClassification.objects.filter(document=doc, stage__lt=4).order_by("-id").first()
            )
            if previous is not None and not case.page_from:
                previous.is_final = True
                previous.save(update_fields=["is_final"])
            DocumentOwnerLink.objects.filter(document=doc, review_case=case, status="confirmed").update(
                status="suggested"
            )
            if not case.page_from and doc.status in ("classified", "filed"):
                doc.status = "review"
                doc.save(update_fields=["status", "updated_at"])
        case.status = CaseStatus.OPEN
        case.resolved_by = None
        case.resolved_at = None
        case.snoozed_until = None
        case.save(update_fields=["status", "resolved_by", "resolved_at", "snoozed_until", "updated_at"])
        decision = ReviewDecision.objects.create(
            review_case=case,
            document=doc,
            page_from=case.page_from,
            page_to=case.page_to,
            decision_type=DecisionType.REVERT,
            decided_by=user,
            decided_at=timezone.now(),
            before_state=before,
            after_state={"status": "open", "reason": reason},
        )
        record(
            "review.reopen",
            entity_type="review_case",
            entity_id=case.pk,
            object_id=case.object_id,
            request=request,
            actor=user,
            reason=reason,
        )
    return decision


def merge_duplicate(case: ReviewCase, user, original: Document, *, request=None) -> ReviewDecision:
    """Zusammenfuehren als Dublette (H 2.4): Verweis auf das Original, Verknuepfungen uebertragen, Verschiebung nach 03."""
    with transaction.atomic():
        case = ReviewCase.objects.select_for_update().select_related("document").get(pk=case.pk)
        doc = Document.objects.select_for_update().get(pk=case.document_id)
        if original.pk == doc.pk or original.object_id != doc.object_id:
            raise ReviewError("Original muss ein anderes Dokument desselben Objekts sein")
        before = _snapshot(doc)
        for link in DocumentOwnerLink.objects.filter(document=doc, deleted_at__isnull=True):
            exists = DocumentOwnerLink.objects.filter(
                document=original,
                owner_id=link.owner_id,
                unit_id=link.unit_id,
                page_from=link.page_from,
                deleted_at__isnull=True,
            ).exists()
            if not exists:
                link.pk = None
                link.document = original
                link.save()
        doc.status = "duplicate"
        doc.duplicate_of = original
        doc.category_id = "06"
        doc.subfolder = DocumentSubfolder.objects.get(category_id="06", code="03")
        doc.save(update_fields=["status", "duplicate_of", "category", "subfolder", "updated_at"])
        case.status = CaseStatus.RESOLVED
        case.resolved_by = user
        case.resolved_at = timezone.now()
        case.resolution = {"decision": "merge", "original_document_id": original.pk}
        case.save(update_fields=["status", "resolved_by", "resolved_at", "resolution", "updated_at"])
        decision = ReviewDecision.objects.create(
            review_case=case,
            document=doc,
            decision_type=DecisionType.MERGE,
            decided_by=user,
            decided_at=timezone.now(),
            before_state=before,
            after_state={"duplicate_of": original.pk},
            label_category_id="06",
        )
        record(
            "review.merge_duplicate",
            entity_type="review_case",
            entity_id=case.pk,
            object_id=doc.object_id,
            request=request,
            actor=user,
            before=before,
            after={"duplicate_of": original.pk},
        )
        enqueue(
            JobType.FILE_TO_DRIVE,
            doc.object,
            key=idempotency_key(JobType.FILE_TO_DRIVE, doc.object_id, f"dup-{doc.pk}"),
            document=doc,
            payload={"category": "06", "subfolder": "03"},
        )
    return decision


def split(case: ReviewCase, user, segments: list[dict], *, request=None) -> list[ReviewDecision]:
    """Aufteilen nach Seitenbereichen: je Segment Ziel nach 05 mit Verknuepfung page_range; Master behaelt Kategorie."""
    doc = case.document
    if doc is None or not segments:
        raise ReviewError("Segmente angeben")
    spans = sorted((int(s["page_from"]), int(s["page_to"])) for s in segments)
    for (_a1, b1), (a2, _b2) in zip(spans, spans[1:], strict=False):
        if a2 <= b1:
            raise ReviewError("Seitenbereiche überschneiden sich")
    if spans[0][0] < 1 or (doc.page_count and spans[-1][1] > doc.page_count):
        raise ReviewError("Seitenbereich außerhalb des Dokuments")
    decisions = []
    with transaction.atomic():
        for seg in segments:
            target = Target.from_post(seg) if not isinstance(seg.get("target"), Target) else seg["target"]
            sub_case = ReviewCase.objects.create(
                object=case.object,
                case_type=CaseType.OWNER_CANDIDATES,
                case_subtype="split_segment",
                document=doc,
                page_from=int(seg["page_from"]),
                page_to=int(seg["page_to"]),
                batch_key=f"split:{doc.pk}",
                priority=case.priority,
                context={"reason": "Aufteilung im Review", "parent_case": case.pk},
            )
            decisions.append(
                apply_decision(sub_case, user, target, request=request, bulk_key=f"split:{doc.pk}")
            )
            decisions[-1].decision_type = DecisionType.SPLIT
            decisions[-1].save(update_fields=["decision_type"])
        doc.is_master_with_segments = True
        doc.save(update_fields=["is_master_with_segments", "updated_at"])
        case.status = CaseStatus.RESOLVED
        case.resolved_by = user
        case.resolved_at = timezone.now()
        case.resolution = {"decision": "split", "segments": [[a, b] for a, b in spans]}
        case.save(update_fields=["status", "resolved_by", "resolved_at", "resolution", "updated_at"])
        record(
            "review.split",
            entity_type="review_case",
            entity_id=case.pk,
            object_id=case.object_id,
            request=request,
            actor=user,
            after={"segments": spans},
        )
    return decisions


def transfer(
    case: ReviewCase, user, target_obj, *, reason: str | None = None, request=None
) -> ReviewDecision:
    from apps.documents.transfer import transfer_document

    with transaction.atomic():
        case = ReviewCase.objects.select_for_update().select_related("document").get(pk=case.pk)
        new = transfer_document(case.document, target_obj, user=user, request=request, reason=reason)
        case.status = CaseStatus.RESOLVED
        case.resolved_by = user
        case.resolved_at = timezone.now()
        case.resolution = {
            "decision": "transfer_object",
            "new_document_id": new.pk,
            "object_id": target_obj.pk,
        }
        case.save(update_fields=["status", "resolved_by", "resolved_at", "resolution", "updated_at"])
        decision = ReviewDecision.objects.create(
            review_case=case,
            document=case.document,
            decision_type=DecisionType.TRANSFER_OBJECT,
            decided_by=user,
            decided_at=timezone.now(),
            before_state={"object_id": case.object_id},
            after_state={"object_id": target_obj.pk, "new_document_id": new.pk},
        )
    return decision


# ---------------------------------------------------------------- Suche
def owner_suggestions(q: str, obj, *, limit: int = 12) -> list[dict]:
    """Eigentuemersuche ab zwei Zeichen: erst Eigentuemer mit Zuordnung im Objekt, dann objektuebergreifend (gekennzeichnet)."""
    q = (q or "").strip()
    if len(q) < 2:
        return []
    from apps.drive.naming import transliterate

    needle = transliterate(q).upper()
    in_object = set(OwnerUnitAssignment.active.filter(unit__object=obj).values_list("owner_id", flat=True))
    qs = Owner.active.filter(
        Q(search_name__icontains=needle) | Q(last_name__icontains=q) | Q(company_name__icontains=q)
    )
    rows = []
    ql = q.lower()

    def rank(o: Owner):
        primary = (o.last_name or o.company_name or "").lower()
        return (o.pk not in in_object, not primary.startswith(ql), o.search_name or "")

    for owner in sorted(qs[: limit * 3], key=rank):
        assignments = OwnerUnitAssignment.active.filter(owner=owner).select_related("unit", "unit__object")
        rows.append(
            {
                "id": owner.pk,
                "name": str(owner),
                "in_object": owner.pk in in_object,
                "units": [
                    {
                        "assignment_id": a.pk,
                        "unit_id": a.unit_id,
                        "unit": a.unit.unit_label,
                        "object": a.unit.object.object_number,
                        "valid_from": a.valid_from.isoformat() if a.valid_from else None,
                        "valid_to": a.valid_to.isoformat() if a.valid_to else None,
                    }
                    for a in assignments
                ],
            }
        )
        if len(rows) >= limit:
            break
    return rows


def tenant_suggestions(q: str, obj, *, limit: int = 12) -> list[dict]:
    from apps.parties.models import TenantUnitAssignment

    q = (q or "").strip()
    if len(q) < 2:
        return []
    in_object = set(TenantUnitAssignment.active.filter(unit__object=obj).values_list("tenant_id", flat=True))
    qs = Tenant.active.filter(
        Q(search_name__icontains=q.upper()) | Q(last_name__icontains=q) | Q(company_name__icontains=q)
    )
    return [
        {"id": t.pk, "name": str(t), "in_object": t.pk in in_object}
        for t in sorted(qs[: limit * 2], key=lambda t: t.pk not in in_object)[:limit]
    ]


def units_of(obj) -> list[dict]:
    return [
        {"id": u.pk, "label": u.unit_label, "type": u.get_unit_type_display(), "number": u.unit_number}
        for u in Unit.active.filter(object=obj).order_by("unit_type", "unit_label_normalized")
    ]


def case_history(case: ReviewCase):
    from apps.audit.models import AuditEvent

    return AuditEvent.objects.filter(entity_type="review_case", entity_id=case.pk).order_by("-occurred_at")[
        :50
    ]


# ---------------------------------------------------------------- Massenbearbeitung (H 2.5)
@dataclass
class BulkRow:
    case_id: int
    document: str
    page_from: int | None
    page_to: int | None
    target: Target
    owner: str | None
    unit: str | None
    folder_name: str | None
    folder_exists: bool
    warnings: list[str]
    errors: list[str]
    candidates: list[dict] = field(default_factory=list)

    @property
    def state(self) -> str:
        return "rot" if self.errors else ("gelb" if self.warnings else "gruen")


def bulk_max() -> int:
    return int(store.get("review.bulk_max_cases", 500))


def _folder_for_target(obj, target: Target) -> tuple[str | None, bool]:
    """Zielpfad aus Benennungsfunktion und drive_nodes (Folder-ID-Cache), ohne Drive-Aufruf."""
    from apps.drive.models import DriveNode as DriveNodeRow
    from apps.drive.models import NodeStatus
    from apps.drive.naming import (
        OwnerFileNamingConfig,
        OwnerNameInput,
        OwnerNamePart,
        build_owner_folder_name,
    )
    from apps.parties.models import OwnerFile

    if target.category != "05":
        cat = DocumentCategory_name(target.category)
        sub = (
            DocumentSubfolder.objects.filter(category_id=target.category, code=target.subfolder).first()
            if target.subfolder
            else None
        )
        path = "/".join(p for p in [cat, sub.folder_name if sub else None] if p)
        exists = DriveNodeRow.objects.filter(
            object=obj,
            category_id=target.category,
            status=NodeStatus.ACTIVE,
            owner_file__isnull=True,
            subfolder=sub,
        ).exists()
        return path, exists
    sub = (
        DocumentSubfolder.objects.filter(category_id="05", code=target.subfolder).first()
        if target.subfolder
        else None
    )
    if target.assignment_id or (target.owner_id and target.unit_id):
        a = (
            OwnerUnitAssignment.active.filter(pk=target.assignment_id).select_related("owner", "unit").first()
            if target.assignment_id
            else None
        )
        owner = a.owner if a else Owner.objects.filter(pk=target.owner_id).first()
        unit = a.unit if a else Unit.objects.filter(pk=target.unit_id).first()
        if owner is None or unit is None:
            return None, False
        existing = OwnerFile.active.filter(file_assignments__assignment=a).first() if a else None
        if existing is None:
            cfg = OwnerFileNamingConfig.from_settings()
            group = (
                list(
                    OwnerUnitAssignment.active.filter(
                        unit=unit, valid_from=a.valid_from, valid_to=a.valid_to
                    ).select_related("owner")
                )
                if a
                else []
            )
            names = tuple(
                OwnerNamePart(kind=o.type, last_name=o.last_name, company_name=o.company_name)
                for o in ([x.owner for x in group] or [owner])
            )
            name = build_owner_folder_name(
                OwnerNameInput(
                    file_kind="unit_owner",
                    unit_label=unit.unit_label,
                    unit_type=unit.unit_type,
                    owner_names=names,
                    valid_from=a.valid_from if a else None,
                    existing_names_in_object=frozenset(
                        OwnerFile.active.filter(object=obj).values_list("folder_name", flat=True)
                    ),
                ),
                cfg,
            )
        else:
            name = existing.folder_name
        exists = (
            existing is not None
            and DriveNodeRow.objects.filter(
                owner_file=existing, status=NodeStatus.ACTIVE, subfolder=sub
            ).exists()
        )
        return "/".join(
            p
            for p in [
                "05_Eigentümerakte" if False else DocumentCategory_name("05"),
                name,
                sub.folder_name if sub else None,
            ]
            if p
        ), exists
    if target.owner_id and target.unit_unknown:
        owner = Owner.objects.filter(pk=target.owner_id).first()
        cfg = OwnerFileNamingConfig.from_settings()
        name = (
            build_owner_folder_name(
                OwnerNameInput(
                    file_kind="unknown_unit",
                    owner_names=(
                        OwnerNamePart(
                            kind=owner.type, last_name=owner.last_name, company_name=owner.company_name
                        ),
                    ),
                ),
                cfg,
            )
            if owner
            else None
        )
        return "/".join(
            p for p in [DocumentCategory_name("05"), name, sub.folder_name if sub else None] if p
        ), False
    return "/".join(
        p
        for p in [
            DocumentCategory_name("05"),
            OwnerFileNamingConfig.from_settings().name_unassigned,
            sub.folder_name if sub else None,
        ]
        if p
    ), False


def DocumentCategory_name(code: str) -> str:  # noqa: N802
    from apps.documents.models import DocumentCategory

    cat = DocumentCategory.objects.filter(code=code).first()
    return cat.folder_name if cat else code


def bulk_rows(
    case_ids: list[int],
    overrides: dict | None = None,
    row_overrides: dict[int, dict] | None = None,
    user=None,
) -> list[BulkRow]:
    """Vorschau ohne Schreibwirkung: je Fall Ziel aus Vorschlag plus Sammelfeldern plus Zeilenkorrektur, Pfad aus der
    Benennungsfunktion, Ordner vorhanden ja oder nein, Warnungen und Konflikte."""
    overrides = {k: v for k, v in (overrides or {}).items() if v not in (None, "")}
    rows: list[BulkRow] = []
    cases = (
        ReviewCase.objects.filter(pk__in=case_ids[: bulk_max()])
        .select_related("document", "object")
        .order_by("document_id", "page_from", "id")
    )
    for case in cases:
        target = proposal_target(case)
        data = target.as_dict()
        data.update(overrides)
        data.update((row_overrides or {}).get(case.pk, {}))
        merged = Target.from_post({k: ("" if v is None else v) for k, v in data.items()})
        merged.owner_unknown = bool(data.get("owner_unknown")) and str(data.get("owner_unknown")) not in (
            "0",
            "False",
            "false",
        )
        merged.unit_unknown = bool(data.get("unit_unknown")) and str(data.get("unit_unknown")) not in (
            "0",
            "False",
            "false",
        )
        errors, warnings = validate(case, merged) if case.document_id else (["Fall ohne Dokument"], [])
        if case.status not in (CaseStatus.OPEN, CaseStatus.IN_PROGRESS):
            errors.append("Fall bereits erledigt")
        if (
            len(case.candidates or []) > 1
            and not merged.assignment_id
            and not (merged.owner_id and merged.unit_id)
        ):
            errors.append("mehrere Kandidaten: Eigentümer wählen")
        folder, exists = (None, False)
        if not errors:
            try:
                folder, exists = _folder_for_target(case.object or case.document.object, merged)
            except Exception as exc:  # Pfadbildung ist Anzeige, kein Abbruchgrund
                warnings.append(f"Zielpfad nicht bestimmbar: {exc}")
        owner = Owner.objects.filter(pk=merged.owner_id).first() if merged.owner_id else None
        unit = Unit.objects.filter(pk=merged.unit_id).first() if merged.unit_id else None
        rows.append(
            BulkRow(
                case.pk,
                case.document.current_name if case.document else "",
                case.page_from,
                case.page_to,
                merged,
                str(owner) if owner else None,
                unit.unit_label if unit else None,
                folder,
                exists,
                warnings,
                errors,
                list(case.candidates or []),
            )
        )
    return rows


def bulk_execute(
    case_ids: list[int],
    user,
    *,
    overrides: dict | None = None,
    row_overrides: dict[int, dict] | None = None,
    exclude: list[int] | None = None,
    bulk_key: str | None = None,
    request=None,
) -> dict:
    """Sammelaktion: je Zeile eigene Entscheidung, Audit und Verschiebungsjob; Fehler einer Zeile stoppen die uebrigen
    nicht; Listenerzeugung genau einmal je Gruppe (Entprellung ueber bulk_key)."""
    import uuid

    bulk_key = bulk_key or f"bulk:{uuid.uuid4().hex[:12]}"
    exclude = set(exclude or [])
    rows = [r for r in bulk_rows(case_ids, overrides, row_overrides, user) if r.case_id not in exclude]
    ok, failed = [], []
    for row in rows:
        if row.errors:
            failed.append({"case_id": row.case_id, "error": "; ".join(row.errors)})
            continue
        try:
            case = ReviewCase.objects.get(pk=row.case_id)
            decision = apply_decision(
                case,
                user,
                row.target,
                request=request,
                is_bulk=True,
                bulk_key=bulk_key,
                warnings=row.warnings,
            )
            ok.append({"case_id": row.case_id, "decision_id": decision.pk, "folder": row.folder_name})
        except Exception as exc:  # Zeile scheitert, die uebrigen laufen weiter (H 2.5)
            failed.append({"case_id": row.case_id, "error": str(exc)[:300]})
    record(
        "review.bulk_execute",
        entity_type="review_bulk",
        entity_id=None,
        object_id=rows[0].target
        and ReviewCase.objects.filter(pk=rows[0].case_id).values_list("object_id", flat=True).first()
        if rows
        else None,
        request=request,
        actor=user,
        after={
            "bulk_key": bulk_key,
            "ok": len(ok),
            "failed": len(failed),
            "cases": [r.case_id for r in rows],
        },
    )
    return {"bulk_key": bulk_key, "ok": ok, "failed": failed, "total": len(rows)}


# ---------------------------------------------------------------- Import aus erkannter Liste (B-34, M9 Schritt 5)
def document_bytes(doc: Document) -> bytes:
    """Quelldatei eines Dokuments: Arbeitsverzeichnis, Upload-Transit oder Download aus Drive."""
    import shutil
    from pathlib import Path

    from apps.pipeline import storage

    if doc.sha256:
        work = storage.work_dir(doc.sha256)
        if work.exists():
            for candidate in sorted(work.glob("original.*")):
                return candidate.read_bytes()
    if doc.source_path and Path(doc.source_path).exists():
        return Path(doc.source_path).read_bytes()
    if doc.drive_file_id:
        from apps.drive import oauth

        adapter = oauth.get_adapter()
        if adapter is None:
            raise ReviewError("Keine Google-Verbindung für den Download der Liste")
        tmp = storage.work_tmp_dir()
        try:
            target = tmp / ("original" + Path(doc.current_name).suffix.lower())
            adapter.download(doc.drive_file_id, target)
            return target.read_bytes()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    raise ReviewError("Quelldatei der Liste ist nicht verfügbar")


def start_import(case: ReviewCase, user, *, import_kind: str | None = None, request=None):
    """Erkannte Liste in die Importkette (M3) uebergeben: import_batches anlegen, Einlesen als Job, Fall erledigt mit
    Verweis auf den Import. Die Uebernahme selbst bleibt die Entscheidung im Import (H 6.7)."""
    from apps.imports import services as import_services
    from apps.imports.models import ImportKind
    from apps.imports.tasks import parse_batch_task

    if case.case_type != CaseType.IMPORT_CANDIDATE:
        raise ReviewError("Import starten gilt nur für erkannte Listen")
    doc = case.document
    if doc is None:
        raise ReviewError("Kein Dokument am Fall")
    kind = import_kind or (case.context or {}).get("import_kind") or ImportKind.OWNER_LIST
    if kind not in ImportKind.values:
        raise ReviewError("Unbekannte Art der Liste")
    data = document_bytes(doc)
    with transaction.atomic():
        case = ReviewCase.objects.select_for_update().get(pk=case.pk)
        if case.status in (CaseStatus.RESOLVED, CaseStatus.DISMISSED):
            raise ReviewError("Fall ist bereits erledigt")
        try:
            batch, created = import_services.create_batch(
                doc.object, filename=doc.current_name, data=data, user=user, import_kind=kind
            )
        except import_services.ImportError_ as exc:
            raise ReviewError(str(exc)) from exc
        case.status = CaseStatus.RESOLVED
        case.resolved_by = user
        case.resolved_at = timezone.now()
        case.resolution = {
            "decision": "import",
            "import_batch_id": batch.pk,
            "import_kind": kind,
            "created": created,
        }
        case.save(update_fields=["status", "resolved_by", "resolved_at", "resolution", "updated_at"])
        ReviewDecision.objects.create(
            review_case=case,
            document=doc,
            decision_type=DecisionType.CONFIRM,
            decided_by=user,
            decided_at=timezone.now(),
            before_state={"status": "open", "proposal": case.context},
            after_state={"import_batch_id": batch.pk, "import_kind": kind},
            system_was_correct=True,
        )
        record(
            "review.import_started",
            entity_type="review_case",
            entity_id=case.pk,
            object_id=case.object_id,
            request=request,
            actor=user,
            after={"import_batch_id": batch.pk, "import_kind": kind, "created": created},
        )
    if created:
        parse_batch_task.delay(batch.pk)
    return batch
