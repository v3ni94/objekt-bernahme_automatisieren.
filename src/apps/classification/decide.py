"""Entscheidungsalgorithmus (E 6.1), Zusatzpruefungen fuer Eigentuemerdokumente (E 6.2, docs/architektur.md 6.5),
Segmentierung von Gesamtdokumenten (E 6.3), Fallbildung nach der Entscheidungstabelle B-09 und Quellenweiche B-10.

Ergebnis ist ein Plan (Decision); persist() schreibt document_classifications, documents, document_owner_links,
document_tenant_links und review_cases in einer Transaktion. Jede Ablage in 06_Sonstiges traegt einen Fall (CR 8).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from django.db import transaction

from apps.classification.confidence import Combined, Stage1Result, Stage2Result, combine, thresholds
from apps.classification.context import DocContext, PeriodRef
from apps.config import store
from apps.documents.models import (
    Document,
    DocumentClassification,
    DocumentOwnerLink,
    DocumentSubfolder,
    DocumentTenantLink,
    DocumentType,
)
from apps.objects.models import Unit
from apps.parties import services as party_services
from apps.parties.models import Owner, OwnerUnitAssignment
from apps.review.models import CaseStatus, CaseType, ReviewCase

OWNERSHIP_TYPES = {
    "kaufvertrag",
    "veraeusserungsanzeige",
    "mitteilung_eigentumswechsel",
    "uebergang_nutzen_lasten",
    "verwalterzustimmung",
    "veraeusserungszustimmung",
}
SEGMENT_HEAD = {
    "gesamtjahresabrechnung": (re.compile(r"(?im)^\s*einzelabrechnung\b"), "05", "einzelabrechnung"),
    "gesamtwirtschaftsplan": (re.compile(r"(?im)^\s*einzelwirtschaftsplan\b"), "06", "einzelwirtschaftsplan"),
}
TOP = re.compile(r"(?i)\bTOP\s*\d+")


@dataclass
class LinkPlan:
    owner_id: int | None
    unit_id: int | None
    assignment_id: int | None
    owner_file_id: int | None
    subfolder: str | None
    document_type: str | None
    page_from: int | None = None
    page_to: int | None = None
    confidence: float = 1.0
    status: str = "confirmed"
    primary: bool = False  # physische Ablage in dieser Akte


@dataclass
class CasePlan:
    case_type: str
    subtype: str | None = None
    candidates: list[dict] = field(default_factory=list)
    proposed_action: dict | None = None
    context: dict = field(default_factory=dict)
    misc_subfolder: str | None = None  # Unterordner in 06 bei physischer Ablage in 06
    page_from: int | None = None
    page_to: int | None = None
    priority: int = 100


@dataclass
class Decision:
    category: str | None
    subfolder: str | None
    document_type: str | None
    scope: str | None
    confidence: float
    decided_by: str
    reason: str
    period: PeriodRef
    physical_category: str | None = None  # Kategorie des physischen Ablageorts (06 bei Ablage in Sonstiges)
    physical_subfolder: str | None = None
    links: list[LinkPlan] = field(default_factory=list)
    tenant_ids: list[int] = field(default_factory=list)
    cases: list[CasePlan] = field(default_factory=list)
    segments: list[dict] = field(default_factory=list)
    move_allowed: bool = True  # Drive-Schreibzugriff (B-10)
    physical_owner_file_id: int | None = None  # Akte ohne Verknuepfung (Unzugeordnet)
    stage3_required: bool = False
    kpi_misc: bool = False
    kpi_misc_adjusted: bool = False
    candidates_top: list[dict] = field(default_factory=list)

    @property
    def review_required(self) -> bool:
        return bool(self.cases)

    def plan_text(self) -> str:
        parts = [f"{self.physical_category or self.category or '06'}"]
        if self.physical_subfolder or self.subfolder:
            parts.append(self.physical_subfolder or self.subfolder or "")
        akte = next((link for link in self.links if link.primary), None)
        target = "/".join(p for p in parts if p)
        akte_id = akte.owner_file_id if akte else self.physical_owner_file_id
        if akte_id:
            target += f" Akte {akte_id}"
        flag = " Review" if self.cases else ""
        return f"{target} ({self.confidence:.2f}){flag}"


# ---------------------------------------------------------------- Zusatzpruefungen
def _assignments_for(unit_id: int, period: PeriodRef) -> list[OwnerUnitAssignment]:
    qs = party_services.owners_for_document(
        unit_id,
        period_year=period.year,
        period_from=period.date_from,
        period_to=period.date_to,
        document_date=None if period.has_period else period.document_date,
    )
    if qs is None:
        qs = OwnerUnitAssignment.active.filter(
            unit_id=unit_id, owner__deleted_at__isnull=True
        ).select_related("owner")
    return list(qs.order_by("valid_from", "owner__search_name"))


def _groups(assignments: list[OwnerUnitAssignment]) -> list[list[OwnerUnitAssignment]]:
    """Eigentuemergruppen: Zuordnungen mit gleichem Zeitraum gelten als eine Gruppe (Mehrfacheigentum)."""
    groups: dict[tuple, list[OwnerUnitAssignment]] = {}
    for a in assignments:
        groups.setdefault((a.valid_from, a.valid_to), []).append(a)
    return [groups[k] for k in sorted(groups, key=lambda k: (k[0] or date.min, k[1] or date.max))]


def _candidate(a: OwnerUnitAssignment) -> dict:
    return {
        "assignment_id": a.pk,
        "owner_id": a.owner_id,
        "owner": str(a.owner),
        "unit_id": a.unit_id,
        "unit": a.unit.unit_label,
        "valid_from": a.valid_from.isoformat() if a.valid_from else None,
        "valid_to": a.valid_to.isoformat() if a.valid_to else None,
    }


def owner_checks(
    ctx: DocContext,
    obj,
    *,
    subfolder: str | None,
    document_type: str | None,
    period: PeriodRef,
    unit_ids: list[int] | None = None,
    owner_ids: list[int] | None = None,
    page_from: int | None = None,
    page_to: int | None = None,
) -> tuple[list[LinkPlan], CasePlan | None]:
    """ZUSATZPRUEFUNGEN (E 6.2) mit der Entscheidungstabelle docs/architektur.md 6.5. Rueckgabe Links und Fall."""
    from apps.classification.ownerfiles import (
        owner_file_for_assignments,
        owner_file_unassigned,
        owner_file_unknown_unit,
    )

    units = list(unit_ids if unit_ids is not None else ctx.unit_ids)
    owners = list(owner_ids if owner_ids is not None else ctx.owner_ids)
    ownership = document_type in OWNERSHIP_TYPES
    span = {"page_from": page_from, "page_to": page_to}

    def links_for(assignments: list[OwnerUnitAssignment], unit: Unit, status="confirmed", confidence=1.0):
        """Eine Akte je Eigentuemergruppe (gleicher Zeitraum); erste Gruppe traegt die physische Ablage."""
        out = []
        for g_index, group in enumerate(_groups(assignments)):
            akte = owner_file_for_assignments(unit, group)
            for a_index, a in enumerate(group):
                out.append(
                    LinkPlan(
                        a.owner_id,
                        unit.pk,
                        a.pk,
                        akte.pk,
                        subfolder,
                        document_type,
                        page_from,
                        page_to,
                        confidence,
                        status,
                        primary=(g_index == 0 and a_index == 0),
                    )
                )
        return out

    if units:
        unit = Unit.objects.select_related("object").get(pk=units[0])
        candidates = _assignments_for(unit.pk, period)
        if owners:
            # a) und f): benannte Eigentuemer mit Zuordnung auf dieser Einheit
            named = [
                a
                for a in (
                    candidates
                    if not ownership
                    else list(
                        OwnerUnitAssignment.active.filter(
                            unit=unit, owner__deleted_at__isnull=True
                        ).select_related("owner")
                    )
                )
                if a.owner_id in owners
            ]
            named.sort(key=lambda a: owners.index(a.owner_id))
            if named:
                if ownership or len({a.owner_id for a in named}) >= 1:
                    return links_for(named, unit), None
            # g) Zeitraum ueberspannt einen Wechsel, Dokument benennt genau einen Kandidaten
            if candidates and len(_groups(candidates)) > 1:
                hits = [a for a in candidates if a.owner_id in owners]
                if hits:
                    return links_for(hits, unit), None
            # Name schlaegt Dokumentdatum (T21): benannter Eigentuemer besitzt die Einheit zu einer anderen Zeit
            if not period.has_period:
                named_any = [
                    a
                    for a in OwnerUnitAssignment.active.filter(
                        unit=unit, owner_id__in=owners, owner__deleted_at__isnull=True
                    ).select_related("owner")
                ]
                if named_any:
                    named_any.sort(key=lambda a: owners.index(a.owner_id))
                    return links_for(named_any, unit, confidence=0.9), None
            # b) Eigentuemer und Einheit erkannt, keine Zuordnung
            if ownership:
                return [], CasePlan(
                    CaseType.OWNER_CANDIDATES,
                    "new_assignment_proposed",
                    [
                        _candidate(a)
                        for a in OwnerUnitAssignment.active.filter(unit=unit).select_related("owner", "unit")
                    ],
                    {"action": "create_assignment", "owner_ids": owners, "unit_id": unit.pk},
                    {"reason": "Eigentümer und Einheit erkannt, keine Zuordnung; Eigentumsnachweis", **span},
                    misc_subfolder="02",
                )
            return [], CasePlan(
                CaseType.OWNER_CANDIDATES,
                "assignment_unknown",
                [
                    _candidate(a)
                    for a in OwnerUnitAssignment.active.filter(unit=unit).select_related("owner", "unit")
                ],
                {"action": "choose_or_create_assignment", "owner_ids": owners, "unit_id": unit.pk},
                {"reason": "Verbindung Eigentümer/Einheit unbekannt", **span},
                misc_subfolder="02",
            )
        # c) nur Einheit
        groups = _groups(candidates)
        if len(groups) == 1:
            return links_for(groups[0], unit), None
        if not groups:
            return [], CasePlan(
                CaseType.OWNER_CANDIDATES,
                "no_owner_in_period",
                [
                    _candidate(a)
                    for a in OwnerUnitAssignment.active.filter(unit=unit).select_related("owner", "unit")
                ],
                None,
                {"reason": "Einheit ohne Eigentümer im Zeitraum", "unit_id": unit.pk, **span},
                misc_subfolder="02",
            )
        return [], CasePlan(
            CaseType.OWNER_CANDIDATES,
            "multiple_owners" if period.has_period else "no_period",
            [_candidate(a) for a in candidates],
            None,
            {
                "reason": "mehrere Eigentümer im Zeitraum oder kein Zeitbezug; nicht raten (CR 7)",
                "unit_id": unit.pk,
                **span,
            },
            misc_subfolder="02",
        )
    if owners:
        # d) nur Eigentuemer
        qs = OwnerUnitAssignment.active.filter(
            owner_id__in=owners, unit__object=obj, unit__deleted_at__isnull=True
        ).select_related("owner", "unit")
        if period.has_period or period.document_date:
            start = period.date_from or (date(period.year, 1, 1) if period.year else period.document_date)
            end = period.date_to or (date(period.year, 12, 31) if period.year else period.document_date)
            qs = qs.filter(party_services._period_filter("", start, end))
        assignments = list(qs)
        by_unit: dict[int, list[OwnerUnitAssignment]] = {}
        for a in assignments:
            by_unit.setdefault(a.unit_id, []).append(a)
        if len(by_unit) == 1:
            unit_id, group = next(iter(by_unit.items()))
            # Mehrfacheigentum: Mitinhaber derselben Einheit im Zeitraum mit aufnehmen
            full = [
                a
                for a in _assignments_for(unit_id, period)
                if a.valid_from == group[0].valid_from and a.valid_to == group[0].valid_to
            ]
            return links_for(full or group, group[0].unit), None
        if len(by_unit) > 1:
            return [], CasePlan(
                CaseType.OWNER_CANDIDATES,
                "multiple_units",
                [_candidate(a) for a in assignments],
                {"action": "choose_unit_or_unknown_unit", "owner_ids": owners},
                {"reason": "Eigentümer mit mehreren Einheiten", **span},
                misc_subfolder="02",
            )
        owner = Owner.objects.get(pk=owners[0])
        akte = owner_file_unknown_unit(obj, owner)
        link = LinkPlan(
            owner.pk,
            None,
            None,
            akte.pk,
            subfolder,
            document_type,
            page_from,
            page_to,
            0.7,
            "suggested",
            primary=True,
        )
        return [link], CasePlan(
            CaseType.OWNER_CANDIDATES,
            "owner_without_assignment",
            [],
            {"action": "create_assignment_or_confirm_unknown_unit", "owner_ids": owners},
            {
                "reason": "Eigentümer ohne Zuordnung im Objekt; Akte Unbekannte_WE",
                "owner_file_id": akte.pk,
                **span,
            },
        )
    # e) weder Eigentuemer noch Einheit: Akte Unzugeordnet ohne Verknuepfung (document_owner_links braucht einen Bezug)
    akte = owner_file_unassigned(obj)
    return [], CasePlan(
        CaseType.UNCLEAR,
        "no_owner_no_unit",
        [],
        {"action": "assign_owner_and_unit"},
        {
            "reason": "Kategorie 05 sicher, weder Eigentümer noch Einheit erkannt; Akte Unzugeordnet",
            "owner_file_id": akte.pk,
            **span,
        },
    )


# ---------------------------------------------------------------- Segmente (E 6.3)
def find_segments(ctx: DocContext, document_type: str | None) -> list[dict]:
    if document_type in SEGMENT_HEAD:
        pattern, subfolder, seg_type = SEGMENT_HEAD[document_type]
        starts = [n for n in sorted(ctx.pages) if pattern.search(ctx.pages[n] or "")]
        if document_type == "gesamtjahresabrechnung":
            head_pattern = re.compile(r"(?im)^\s*gesamt(jahres)?abrechnung")
            starts = [n for n in starts if not head_pattern.search(ctx.pages[n] or "")]
        segments = []
        for i, start in enumerate(starts):
            end = (starts[i + 1] - 1) if i + 1 < len(starts) else max(ctx.pages)
            segments.append(
                {"page_from": start, "page_to": end, "subfolder": subfolder, "document_type": seg_type}
            )
        return segments
    if document_type == "versammlungsprotokoll":
        unit_pages = ctx.entity_pages.get("unit_label", {})
        owner_pages = {p for p, ids in ctx.entity_pages.get("person_name", {}).items() if ids}
        pages = sorted(
            n for n in ctx.pages if TOP.search(ctx.pages[n] or "") and (n in unit_pages or n in owner_pages)
        )
        segments: list[dict] = []
        for n in pages:
            if segments and segments[-1]["page_to"] == n - 1:
                segments[-1]["page_to"] = n
            else:
                segments.append(
                    {
                        "page_from": n,
                        "page_to": n,
                        "subfolder": "07",
                        "document_type": "beschluss_eigentuemerbezug",
                    }
                )
        return segments
    return []


def _segment_entities(ctx: DocContext, page_from: int, page_to: int, key: str) -> list[int]:
    out: list[int] = []
    for page in range(page_from, page_to + 1):
        for ref in ctx.entity_pages.get(key, {}).get(page, []):
            if ref not in out:
                out.append(ref)
    return out


# ---------------------------------------------------------------- Entscheidung
def decide(
    ctx: DocContext, s1: Stage1Result, s2: Stage2Result | None, doc: Document, *, stage3_enabled: bool = False
) -> Decision:
    obj = doc.object
    t = thresholds()
    combined: Combined = combine(s1, s2, ner_support=bool(ctx.owner_ids or ctx.unit_ids))
    period = ctx.period
    top = _top_candidates(s1, s2)
    base = Decision(
        category=combined.category,
        subfolder=s1.subfolder if s1.category == combined.category else None,
        document_type=s1.document_type if s1.category == combined.category else None,
        scope=s1.scope if s1.category == combined.category else None,
        confidence=combined.confidence,
        decided_by=combined.decided_by,
        reason=combined.reason,
        period=period,
        candidates_top=top,
    )
    if s2 is not None and not s2.cold_start and base.category == s2.top and base.subfolder is None:
        base.subfolder, base.document_type = s2.subfolder, s2.document_type
    if combined.stage3_required and stage3_enabled:
        base.stage3_required = True
        return base
    existing = doc.source == "drive_existing"
    # Schritt 5: keine Kategorie erreicht die Schwelle -> 06/01_Unklar mit Fall
    if base.category is None or base.confidence < t["threshold_auto_file"]:
        base.physical_category, base.physical_subfolder = "06", "01"
        if existing:
            base.move_allowed = False
            base.cases.append(
                CasePlan(
                    CaseType.MOVE_PROPOSAL,
                    "below_threshold",
                    top,
                    _proposal(base),
                    {"reason": combined.reason, "confidence": base.confidence},
                    misc_subfolder="01",
                )
            )
        else:
            base.cases.append(
                CasePlan(
                    CaseType.UNCLEAR,
                    "below_threshold",
                    top,
                    None,
                    {"reason": combined.reason, "confidence": base.confidence},
                    misc_subfolder="01",
                )
            )
        base.kpi_misc = True
        base.kpi_misc_adjusted = True
        base.category, base.subfolder, base.document_type = "06", "01", None
        return base
    category = base.category
    if category == "06":
        sub = s1.subfolder or "01"
        base.physical_category, base.physical_subfolder = "06", sub
        subtype = s1.subtype or ("foreign_object" if sub == "04" else "manual_check")
        proposal = None
        if sub == "04" and ctx.foreign_object_numbers:
            proposal = {"action": "transfer_to_object", "object_number": ctx.foreign_object_numbers[0]}
        if subtype == "tenant_document_in_weg" and ctx.unit_ids:
            proposal = {
                "action": "file_to_owner_correspondence",
                "unit_id": ctx.unit_ids[0],
                "subfolder": "08",
            }
        base.cases.append(
            CasePlan(
                CaseType.UNCLEAR,
                subtype,
                top,
                proposal,
                {"reason": s1.proposal or combined.reason, "rules": s1.rule_codes},
                misc_subfolder=sub,
            )
        )
        base.kpi_misc = True
        base.kpi_misc_adjusted = sub not in ("03", "04") and subtype not in (
            "tenant_document_in_weg",
            "misplaced_weg_document",
        )
        base.move_allowed = (
            sub in ("03", "04") or not existing
        )  # B-10: sichere 06-Entscheidung auch fuer Bestand
        return base
    dtype = DocumentType.objects.filter(code=base.document_type).first() if base.document_type else None
    # Pflichtmetadatum Jahr (CR 5)
    if dtype is not None and dtype.requires_period and period.year is None:
        base.cases.append(
            CasePlan(
                CaseType.MISSING_METADATA,
                "period_year",
                [],
                {"action": "enter_period_year"},
                {
                    "reason": "Pflichtmetadatum Abrechnungs- oder Wirtschaftsjahr fehlt",
                    "intended": _intended(base),
                },
                misc_subfolder="02",
            )
        )
        base.physical_category, base.physical_subfolder = "06", "02"
        base.kpi_misc, base.kpi_misc_adjusted = True, False
        base.move_allowed = not existing
        return base
    if category in ("01", "02", "03"):
        base.physical_category, base.physical_subfolder = category, None
        base.segments = find_segments(ctx, base.document_type)
        for seg in base.segments:
            unit_ids = _segment_entities(ctx, seg["page_from"], seg["page_to"], "unit_label")
            owner_ids = _segment_entities(ctx, seg["page_from"], seg["page_to"], "person_name")
            links, case = owner_checks(
                ctx,
                obj,
                subfolder=seg["subfolder"],
                document_type=seg["document_type"],
                period=period,
                unit_ids=unit_ids,
                owner_ids=owner_ids,
                page_from=seg["page_from"],
                page_to=seg["page_to"],
            )
            require_review = bool(store.get("classification.segments_require_review", True))
            for link in links:
                link.primary = False
                if require_review:
                    link.status = "suggested"
                base.links.append(link)
            if case is None and require_review:
                # eindeutiges Segment als bestaetigbarer Vorschlag in der Gruppe (H 2.5, Massenbearbeitung)
                case = CasePlan(
                    CaseType.OWNER_CANDIDATES,
                    "segment_proposed",
                    [
                        {
                            "assignment_id": link.assignment_id,
                            "owner_id": link.owner_id,
                            "unit_id": link.unit_id,
                            "owner_file_id": link.owner_file_id,
                        }
                        for link in links
                    ],
                    {
                        "action": "confirm_segment",
                        "subfolder": seg["subfolder"],
                        "document_type": seg["document_type"],
                        "period_year": period.year,
                    },
                    {
                        "reason": "Segment eines Gesamtdokuments, Vorschlag eindeutig",
                        "page_from": seg["page_from"],
                        "page_to": seg["page_to"],
                        "clear": True,
                    },
                    page_from=seg["page_from"],
                    page_to=seg["page_to"],
                    priority=95,
                )
            if case is not None:
                case.misc_subfolder = None  # Masterdatei bleibt physisch in 02 bzw. 03 (E 6.3)
                case.page_from, case.page_to = seg["page_from"], seg["page_to"]
                case.priority = min(case.priority, 95)
                case.context.setdefault(
                    "segment", {"subfolder": seg["subfolder"], "document_type": seg["document_type"]}
                )
                base.cases.append(case)
        base.move_allowed = True
        return base
    if category == "04":
        base.physical_category, base.physical_subfolder = "04", None
        base.tenant_ids = list(ctx.tenant_ids)
        if obj.management_type == "weg":
            base.cases.append(
                CasePlan(
                    CaseType.UNCLEAR,
                    "tenant_document_in_weg",
                    top,
                    None,
                    {"reason": "Mieterdokument in reiner WEG-Verwaltung (E 2.4, F9)"},
                    misc_subfolder="02",
                )
            )
            base.physical_category, base.physical_subfolder = "06", "02"
            base.kpi_misc = True
            base.move_allowed = not existing
        elif not base.tenant_ids:
            base.cases.append(
                CasePlan(
                    CaseType.UNCLEAR,
                    "tenant_unknown",
                    top,
                    None,
                    {"reason": "Mieterdokument ohne bekannten Mieter", "candidates": ctx.owner_candidates},
                )
            )
            base.move_allowed = not existing
        return base
    # Kategorie 05: Zusatzpruefungen
    links, case = owner_checks(
        ctx, obj, subfolder=base.subfolder, document_type=base.document_type, period=period
    )
    base.links = links
    base.physical_category, base.physical_subfolder = "05", base.subfolder
    if case is not None:
        base.cases.append(case)
        if not links and case.context.get("owner_file_id"):
            base.physical_owner_file_id = case.context["owner_file_id"]
        if case.misc_subfolder:
            base.physical_category, base.physical_subfolder = "06", case.misc_subfolder
            base.kpi_misc, base.kpi_misc_adjusted = True, False
        base.move_allowed = not existing
    return base


def _batch_key(doc: Document, decision: Decision, plan: CasePlan, run) -> str:
    """Gruppenschluessel nach H 2.5: Objekt, Herkunft (Masterdokument bei Segmenten, sonst Lauf), Kategorie,
    Unterordner, Unterart und Jahr; Faelle ohne Lauf tragen das Dokument als Herkunft. Die Fallart gehoert nicht zum
    Schluessel, damit Segmente mit Kandidaten in derselben Gruppe wie eindeutige Segmente erscheinen."""
    segment = plan.context.get("segment") or {}
    if plan.page_from:
        origin = f"doc{doc.pk}"
        parts = [
            "05",
            segment.get("subfolder") or "",
            segment.get("document_type") or "",
            decision.period.year or "",
        ]
    else:
        origin = f"run{run.pk}" if run is not None else f"doc{doc.pk}"
        parts = [
            decision.category or "",
            decision.subfolder or "",
            decision.document_type or "",
            decision.period.year or "",
        ]
    return ":".join(str(p) for p in [f"grp:{doc.object_id}", origin, *parts])[:120]


def _intended(d: Decision) -> dict:
    return {"category": d.category, "subfolder": d.subfolder, "document_type": d.document_type}


def _proposal(d: Decision) -> dict:
    top = d.candidates_top[0] if d.candidates_top else {}
    return {
        "action": "move",
        "category": top.get("category"),
        "subfolder": top.get("subfolder"),
        "document_type": top.get("document_type"),
        "confidence": top.get("confidence"),
    }


def _top_candidates(s1: Stage1Result, s2: Stage2Result | None) -> list[dict]:
    out: list[dict] = []
    for h in sorted(s1.hits, key=lambda h: (-h.get("priority", 0), -h.get("confidence", 0)))[:3]:
        out.append(
            {
                "stage": 1,
                "category": h.get("category"),
                "subfolder": h.get("subfolder"),
                "document_type": h.get("document_type"),
                "confidence": h.get("confidence"),
                "rule": h.get("rule"),
            }
        )
    if s2 is not None and not s2.cold_start:
        for label, p in s2.labels[:3]:
            out.append({"stage": 2, "category": label, "confidence": round(p, 4)})
    return out[:3] if out else []


# ---------------------------------------------------------------- Persistierung
def persist(
    doc: Document,
    ctx: DocContext,
    s1: Stage1Result,
    s2: Stage2Result | None,
    decision: Decision,
    *,
    run=None,
    dry_run: bool = False,
) -> DocumentClassification:
    obj = doc.object
    provider_final = {"stage1": "rules", "stage2": "local_model", "stage3": None}.get(
        decision.decided_by
    ) or "rules"
    with transaction.atomic():
        doc = Document.objects.select_for_update().get(pk=doc.pk)
        if not dry_run:
            DocumentClassification.objects.filter(document=doc, is_final=True).update(is_final=False)
        rows = []
        if s1.hits or s1.conflict:
            rows.append(
                DocumentClassification(
                    document=doc,
                    stage=1,
                    provider="rules",
                    category_id=s1.category,
                    subfolder=_sub(s1.category, s1.subfolder),
                    document_type=_dtype(s1.document_type),
                    period_year=ctx.period.year,
                    scope_decision=s1.scope,
                    confidence=s1.confidence,
                    reasoning=", ".join(s1.rule_codes) + (" (Konflikt)" if s1.conflict else ""),
                    features={"hits": s1.hits, "dry_run": dry_run},
                )
            )
        if s2 is not None and not s2.cold_start:
            rows.append(
                DocumentClassification(
                    document=doc,
                    stage=2,
                    provider="local_model",
                    model=s2.model_version,
                    category_id=s2.top if s2.top in ("01", "02", "03", "04", "05", "06") else None,
                    subfolder=_sub(s2.top, s2.subfolder),
                    document_type=_dtype(s2.document_type),
                    confidence=s2.p,
                    reasoning=f"Abstand {s2.gap:.2f}",
                    features={"labels": s2.labels, "dry_run": dry_run},
                )
            )
        final = DocumentClassification(
            document=doc,
            stage=1 if decision.decided_by == "stage1" else 2,
            provider=provider_final,
            model=s2.model_version if s2 is not None and decision.decided_by == "stage2" else None,
            category_id=decision.category,
            subfolder=_sub(decision.category, decision.subfolder),
            document_type=_dtype(decision.document_type),
            period_year=decision.period.year,
            scope_decision=decision.scope,
            confidence=decision.confidence,
            reasoning=decision.reason,
            owner_candidates=[c for case in decision.cases for c in case.candidates][:50] or None,
            features={
                "dry_run": dry_run,
                "plan": decision.plan_text(),
                "physical": {
                    "category": decision.physical_category,
                    "subfolder": decision.physical_subfolder,
                },
                "move_allowed": decision.move_allowed,
                "review": decision.review_required,
                "segments": decision.segments,
                "top": decision.candidates_top,
                "kpi_misc": decision.kpi_misc,
                "kpi_misc_adjusted": decision.kpi_misc_adjusted,
            },
            is_final=not dry_run,
        )
        rows.append(final)
        DocumentClassification.objects.bulk_create(rows)
        final = DocumentClassification.objects.filter(document=doc).order_by("-id").first()
        if dry_run:
            return final
        # Dokument: bei Ablage in 06 zaehlt das Dokument als 06 (KPI brutto nach CR-Definition, B-31); die fachlich
        # erkannte Kategorie bleibt in der finalen Klassifikationszeile und im Fall (context.intended)
        in_misc = decision.physical_category == "06"
        doc.category_id = "06" if in_misc else decision.category
        doc.subfolder = (
            _sub("06", decision.physical_subfolder)
            if in_misc
            else _sub(decision.category, decision.subfolder)
        )
        doc.document_type = _dtype(decision.document_type)
        doc.period_year = decision.period.year
        doc.period_from = decision.period.date_from
        doc.period_to = decision.period.date_to
        doc.document_date = decision.period.document_date
        doc.final_confidence = decision.confidence
        doc.final_decided_by = decision.decided_by
        doc.classifier_version = s2.model_version if s2 is not None else None
        doc.is_master_with_segments = bool(decision.segments)
        doc.status = "review" if decision.review_required else "classified"
        doc.save()
        # Verknuepfungen
        DocumentOwnerLink.objects.filter(document=doc, status="suggested").delete()
        DocumentTenantLink.objects.filter(document=doc, status="suggested").delete()
        cases_by_span: dict[tuple, ReviewCase] = {}
        for plan in decision.cases:
            key = _batch_key(doc, decision, plan, run)
            case = ReviewCase.objects.filter(
                document=doc,
                page_from=plan.page_from,
                page_to=plan.page_to,
                status__in=[CaseStatus.OPEN, CaseStatus.IN_PROGRESS],
            ).first()
            misc = (
                DocumentSubfolder.objects.filter(category_id="06", code=plan.misc_subfolder).first()
                if plan.misc_subfolder
                else None
            )
            if case is not None and (
                case.case_type != plan.case_type or (case.case_subtype or "") != (plan.subtype or "")[:32]
            ):
                # H 2.1 Regel 1: ein offener Fall je Seitenbereich, weitere Gruende in context.reasons
                reasons = list((case.context or {}).get("reasons") or [])
                reasons.append(
                    {
                        "case_type": plan.case_type,
                        "subtype": plan.subtype,
                        "reason": plan.context.get("reason"),
                    }
                )
                case.context = {**(case.context or {}), "reasons": reasons}
                case.save(update_fields=["context", "updated_at"])
            if case is None:
                case = ReviewCase.objects.create(
                    object=obj,
                    case_type=plan.case_type,
                    case_subtype=(plan.subtype or "")[:32] or None,
                    document=doc,
                    page_from=plan.page_from,
                    page_to=plan.page_to,
                    misc_subfolder=misc,
                    candidates=plan.candidates or None,
                    proposed_action=plan.proposed_action,
                    context={
                        **plan.context,
                        "decision": decision.plan_text(),
                        "confidence": decision.confidence,
                        "intended": plan.context.get("intended") or _intended(decision),
                        "file_name": doc.current_name,
                    },
                    batch_key=key,
                    priority=plan.priority,
                    created_by_run=run,
                )
            cases_by_span[(plan.page_from, plan.page_to)] = case
        for link in decision.links:
            DocumentOwnerLink.objects.create(
                document=doc,
                link_kind="page_range" if link.page_from else "whole_document",
                page_from=link.page_from,
                page_to=link.page_to,
                owner_id=link.owner_id,
                unit_id=link.unit_id,
                assignment_id=link.assignment_id,
                owner_file_id=link.owner_file_id,
                subfolder=_sub("05", link.subfolder),
                document_type=_dtype(link.document_type),
                period_year=decision.period.year,
                period_from=decision.period.date_from,
                period_to=decision.period.date_to,
                document_date=decision.period.document_date,
                confidence=link.confidence,
                status=link.status,
                classification=final,
                review_case=cases_by_span.get((link.page_from, link.page_to)),
            )
        for tenant_id in decision.tenant_ids:
            DocumentTenantLink.objects.create(
                document=doc,
                link_kind="whole_document",
                tenant_id=tenant_id,
                unit_id=ctx.unit_ids[0] if ctx.unit_ids else None,
                confidence=decision.confidence,
                status="confirmed" if not decision.cases else "suggested",
                classification=final,
            )
        if decision.physical_category == "06":
            assert any(c.misc_subfolder or c.case_type for c in decision.cases), (
                "Ablage in 06 ohne Review-Fall (CR 8)"
            )
    return final


def _sub(category: str | None, code: str | None):
    if not category or not code:
        return None
    return DocumentSubfolder.objects.filter(category_id=category, code=code).first()


def _dtype(code: str | None):
    return DocumentType.objects.filter(code=code).first() if code else None
