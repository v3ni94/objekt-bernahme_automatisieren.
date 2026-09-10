"""Requirement Engine (CR 12, Fachentwurf H 3): datengetriebene Bewertung der Pruefpunkte je Objekt, Einheit und
Zuordnung mit Zeitraumlogik (H 3.3), Upsert auf position_key (idempotent, H 3.5), manueller Uebersteuerung, Zusammen-
fassung je Einheit und Objekt (H 3.4) und Ausgabe "Offene Punkte" fuer Listen (M11) und Nachforderung (H 4).

Bewerter sind reine Funktionen ueber einem einmal geladenen Datenbestand (Mengenabfragen statt Einzelabfragen).
Das Fehlen eines Dokuments beweist nicht das Fehlen des Sachverhalts (Negativnachweis, H 3.2): Objektfindings der
Punkte 12 bis 15 bleiben offen, bis ein Sachbearbeiter sie manuell schliesst.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

from django.db import transaction
from django.utils import timezone

from apps.audit.services import record
from apps.config import store
from apps.documents.models import Document, DocumentEntity, DocumentOwnerLink
from apps.imports.models import ImportBatch
from apps.objects.models import ManagedObject, Unit
from apps.parties.models import FieldProvenance, OwnerUnitAssignment, TenantUnitAssignment
from apps.requirements.models import CompletenessCheck, CompletenessFinding, FindingStatus, ScopeType

logger = logging.getLogger(__name__)

CONFIRMED_DOC_STATUSES = ("filed", "classified")
REVIEW_DOC_STATUSES = ("review",)
OWNERSHIP_PROOF_TYPES = ("veraeusserungsanzeige", "mitteilung_eigentumswechsel", "uebergang_nutzen_lasten")
STATEMENT_TYPES = ("einzelabrechnung", "korrekturabrechnung", "abrechnungsspitze")
PLAN_TYPES = ("einzelwirtschaftsplan", "hausgeldvorschuss")
DUNNING_TYPES = ("mahnverfahren", "anwaltsschreiben", "klageunterlagen", "forderungsaufstellung", "mahnung")
WEG_TYPES = ("weg", "weg_with_se")
OWNER_LIST_KINDS = ("owner_list", "mixed")
TENANT_LIST_KINDS = ("tenant_list", "mixed")
PENDING_BATCH_STATUSES = ("uploaded", "parsed", "in_review", "partially_committed")
MANUAL_STATUSES = (FindingStatus.FULFILLED, FindingStatus.NOT_APPLICABLE)
NEGATIVE_PROOF_HINT = "Aufstellung oder Negativerklärung der Vorverwaltung erforderlich"

M, P, F, NA = (
    FindingStatus.MISSING,
    FindingStatus.PARTIAL,
    FindingStatus.FULFILLED,
    FindingStatus.NOT_APPLICABLE,
)


# ---------------------------------------------------------------- Zeitraumlogik (H 3.3)
@dataclass
class Period:
    start: date
    end: date
    years: list[int]
    plan_years: list[int]
    fiscal_start_month: int
    hints: list[str]
    provisional: bool
    reference: date


def fiscal_year_of(d: date, fy: int) -> int:
    return d.year if fy == 1 or d.month >= fy else d.year - 1


def fiscal_year_start(year: int, fy: int) -> date:
    return date(year, fy, 1)


def fiscal_year_end(year: int, fy: int) -> date:
    return date(year + 1, fy, 1) - timedelta(days=1)


def period_for(obj: ManagedObject, today: date) -> Period:
    hints: list[str] = []
    fy = int(obj.fiscal_year_start_month or 1)
    if not obj.fiscal_year_start_month:
        hints.append("Wirtschaftsjahr nicht erfasst, Kalenderjahr angenommen")
    end = obj.takeover_to or today
    if obj.takeover_to is None:
        hints.append("Übernahmestichtag nicht erfasst, heutiges Datum angenommen")
    if obj.takeover_from:
        start = obj.takeover_from
    else:
        n = int(store.get("completeness.default_period_years", 3))
        start = fiscal_year_start(fiscal_year_of(end, fy) - n, fy)
        hints.append(
            f"Übernahmezeitraum aus Vorgabe: {n} abgeschlossene Wirtschaftsjahre vor dem Stichtag zuzüglich laufendes Jahr"
        )
    years = list(range(fiscal_year_of(start, fy), fiscal_year_of(end, fy) + 1))
    plan_years = list(years)
    if (
        end.month - fy
    ) % 12 >= 9:  # letztes Quartal: Plan fuer das Folgejahr ueblicherweise beschlossen (ANNAHME)
        plan_years.append(years[-1] + 1)
    return Period(start, end, years, plan_years, fy, hints, end > today, end)


def statement_due(year: int, fy: int) -> date:
    """Einzelabrechnung fuer Y gilt ab einem konfigurierbaren Datum im Folgejahr als erwartbar (ANNAHME 30.06.)."""
    raw = str(store.get("completeness.statement_expected_after", "30.06.") or "30.06.").strip(". ")
    parts = [int(p) for p in raw.split(".") if p]
    dd, mm_ = (parts + [30, 6])[:2]
    offset = (date(2001, mm_, dd) - date(2000, 12, 31)).days
    return fiscal_year_end(year, fy) + timedelta(days=offset)


def overlaps_year(valid_from: date | None, valid_to: date | None, year: int, fy: int) -> bool:
    start, end = fiscal_year_start(year, fy), fiscal_year_end(year, fy)
    return (valid_from is None or valid_from <= end) and (valid_to is None or valid_to >= start)


# ---------------------------------------------------------------- Datenbestand
@dataclass
class Spec:
    check_code: str
    scope_type: str
    status: str
    unit_id: int | None = None
    assignment_id: int | None = None
    period_year: int | None = None
    details: dict = field(default_factory=dict)
    evidence_document_id: int | None = None
    evidence_link_id: int | None = None

    @property
    def key(self) -> str:
        return (
            f"{self.check_code}|{self.scope_type}|{self.unit_id or 0}|{self.assignment_id or 0}|"
            f"{self.period_year or 0}"
        )


class Data:
    """Einmal geladener Datenbestand eines Objekts fuer alle Bewerter."""

    def __init__(self, obj: ManagedObject, period: Period, today: date):
        self.obj, self.period, self.today = obj, period, today
        self.units = list(Unit.active.filter(object=obj, status="active").order_by("unit_label_normalized"))
        self.assignments = list(
            OwnerUnitAssignment.active.filter(unit__object=obj)
            .select_related("owner", "source_document__document_type")
            .order_by("unit_id", "valid_from")
        )
        self.by_unit: dict[int, list[OwnerUnitAssignment]] = {}
        for a in self.assignments:
            self.by_unit.setdefault(a.unit_id, []).append(a)
        ref = period.reference
        self.current: dict[int, list[OwnerUnitAssignment]] = {
            uid: [
                a
                for a in lst
                if (a.valid_from is None or a.valid_from <= ref) and (a.valid_to is None or a.valid_to >= ref)
            ]
            for uid, lst in self.by_unit.items()
        }
        owner_ids = {a.owner_id for a in self.assignments}
        self.provenance = {
            (p.entity_id, p.field_name): p.status
            for p in FieldProvenance.objects.filter(entity_type="owner", entity_id__in=owner_ids)
        }
        self.links = list(
            DocumentOwnerLink.objects.filter(
                document__object=obj, deleted_at__isnull=True, status__in=("confirmed", "suggested")
            ).select_related("document_type", "document")
        )
        self.docs = list(
            Document.objects.filter(object=obj, deleted_at__isnull=True, document_type__isnull=False)
            .exclude(status__in=("duplicate", "moved_out", "error"))
            .select_related("document_type")
        )
        self.docs_by_type: dict[str, list[Document]] = {}
        for d in self.docs:
            self.docs_by_type.setdefault(d.document_type.code, []).append(d)
        self.batches = list(ImportBatch.objects.filter(object=obj))
        statements = self.docs_by_type.get("rueckstandsaufstellung", [])
        self.units_in_statement = (
            set(
                DocumentEntity.objects.filter(
                    document__in=statements, matched_unit_id__isnull=False
                ).values_list("matched_unit_id", flat=True)
            )
            if statements
            else set()
        )
        tenants = TenantUnitAssignment.objects.filter(
            unit__object=obj, deleted_at__isnull=True
        ).select_related("tenant", "lease")
        self.tenants_by_unit: dict[int, list[TenantUnitAssignment]] = {}
        for t in tenants:
            if (t.valid_from is None or t.valid_from <= ref) and (t.valid_to is None or t.valid_to >= ref):
                self.tenants_by_unit.setdefault(t.unit_id, []).append(t)

    # Verknuepfungen einer Zuordnung: direkt oder ueber Einheit und Eigentuemer
    def links_for(self, a: OwnerUnitAssignment, types: tuple[str, ...], year: int | None = None):
        out = []
        for link in self.links:
            if link.document_type is None or link.document_type.code not in types:
                continue
            if year is not None and link.period_year != year:
                continue
            if link.assignment_id == a.pk or (link.unit_id == a.unit_id and link.owner_id == a.owner_id):
                out.append(link)
        out.sort(key=lambda link: 0 if link.status == "confirmed" else 1)
        return out

    def field_status(self, owner, field_name: str) -> str | None:
        value = getattr(owner, field_name, None)
        if value in (None, ""):
            return None
        return self.provenance.get((owner.pk, field_name)) or owner.data_status


def _owner_name(a: OwnerUnitAssignment) -> str:
    o = a.owner
    return o.company_name or o.last_name or str(o)


def _link_spec(check: str, a: OwnerUnitAssignment, links, *, year: int | None = None, missing_hint: str = ""):
    if links:
        best = links[0]
        status = F if best.status == "confirmed" else P
        return Spec(
            check,
            ScopeType.ASSIGNMENT,
            status,
            unit_id=a.unit_id,
            assignment_id=a.pk,
            period_year=year,
            details={"hint": "" if status == F else "Verknüpfung noch nicht bestätigt"},
            evidence_document_id=best.document_id,
            evidence_link_id=best.pk,
        )
    return Spec(
        check,
        ScopeType.ASSIGNMENT,
        M,
        unit_id=a.unit_id,
        assignment_id=a.pk,
        period_year=year,
        details={"hint": missing_hint},
    )


def _doc_spec(check: str, docs: list[Document], *, missing_hint: str = "") -> Spec:
    confirmed = [d for d in docs if d.status in CONFIRMED_DOC_STATUSES]
    review = [d for d in docs if d.status in REVIEW_DOC_STATUSES]
    if confirmed:
        return Spec(check, ScopeType.OBJECT, F, evidence_document_id=confirmed[0].pk, details={"hint": ""})
    if review:
        return Spec(
            check,
            ScopeType.OBJECT,
            P,
            evidence_document_id=review[0].pk,
            details={"hint": "Dokument vorhanden, Prüfung im Review Center offen"},
        )
    return Spec(check, ScopeType.OBJECT, M, details={"hint": missing_hint})


# ---------------------------------------------------------------- WEG-Katalog (H 3.2)
def evaluate_weg(d: Data) -> list[Spec]:
    obj, p = d.obj, d.period
    specs: list[Spec] = []
    # 1 vollstaendige Eigentuemerliste
    docs = d.docs_by_type.get("eigentuemerliste", [])
    committed = [b for b in d.batches if b.import_kind in OWNER_LIST_KINDS and b.status == "committed"]
    pending = [
        b for b in d.batches if b.import_kind in OWNER_LIST_KINDS and b.status in PENDING_BATCH_STATUSES
    ]
    spec = _doc_spec(
        "owner_list_complete", docs, missing_hint="Eigentümerliste weder als Dokument noch als Import"
    )
    if spec.status != F and committed:
        spec = Spec("owner_list_complete", ScopeType.OBJECT, F, details={"hint": f"Import {committed[0].pk}"})
    elif spec.status == M and pending:
        spec = Spec(
            "owner_list_complete",
            ScopeType.OBJECT,
            P,
            details={"hint": f"Import {pending[0].pk} noch nicht abgeschlossen"},
        )
    specs.append(spec)
    specs.append(_all_units_known(d))
    not_evaluable: set[int] = set()
    # 3 Eigentuemer je Einheit
    for u in d.units:
        cur = d.current.get(u.pk, [])
        if any(a.data_status == "confirmed" for a in cur):
            specs.append(Spec("owner_per_unit", ScopeType.UNIT, F, unit_id=u.pk, details={"hint": ""}))
        elif cur:
            specs.append(
                Spec(
                    "owner_per_unit",
                    ScopeType.UNIT,
                    P,
                    unit_id=u.pk,
                    details={
                        "hint": "Zuordnung nicht bestätigt (" + ", ".join(a.data_status for a in cur) + ")"
                    },
                )
            )
        else:
            not_evaluable.add(u.pk)
            specs.append(
                Spec(
                    "owner_per_unit",
                    ScopeType.UNIT,
                    M,
                    unit_id=u.pk,
                    details={"hint": "kein Eigentümer zugeordnet"},
                )
            )
    # 4 Eigentuemerwechsel
    for u in d.units:
        lst = d.by_unit.get(u.pk, [])
        cur = d.current.get(u.pk, [])
        if u.pk in not_evaluable:
            specs.append(
                Spec(
                    "owner_changes",
                    ScopeType.UNIT,
                    M,
                    unit_id=u.pk,
                    details={"hint": "Folgefehler: kein Eigentümer bekannt", "consequential": True},
                )
            )
            continue
        unknown = [a for a in cur if a.valid_from is None]
        changes = [a for a in lst if a.valid_from and p.start <= a.valid_from <= p.end]
        if unknown:
            specs.append(
                Spec(
                    "owner_changes",
                    ScopeType.UNIT,
                    M,
                    unit_id=u.pk,
                    details={"hint": "Eigentumsbeginn der aktuellen Zuordnung unbekannt"},
                )
            )
        elif changes:
            proven = [
                a
                for a in changes
                if a.source_document_id
                and a.source_document.document_type is not None
                and a.source_document.document_type.code in OWNERSHIP_PROOF_TYPES
            ]
            if len(proven) == len(changes):
                specs.append(
                    Spec(
                        "owner_changes",
                        ScopeType.UNIT,
                        F,
                        unit_id=u.pk,
                        details={"hint": f"{len(changes)} Wechsel mit Nachweis"},
                        evidence_document_id=proven[0].source_document_id,
                    )
                )
            else:
                specs.append(
                    Spec(
                        "owner_changes",
                        ScopeType.UNIT,
                        P,
                        unit_id=u.pk,
                        details={
                            "hint": "Eigentumsbeginn bekannt, Nachweis aus 02_Eigentumsnachweise fehlt",
                            "assignments": [a.pk for a in changes if a not in proven],
                        },
                    )
                )
        else:
            specs.append(
                Spec(
                    "owner_changes",
                    ScopeType.UNIT,
                    F,
                    unit_id=u.pk,
                    details={"hint": "kein Eigentümerwechsel im Übernahmezeitraum"},
                )
            )
    # Zuordnungsbezogene Punkte fuer die aktuellen Eigentuemer
    required_channels = int(store.get("completeness.contact_channels_required", 1))
    for u in d.units:
        for a in d.current.get(u.pk, []):
            owner = a.owner
            # 5 Anschriften
            statuses = [
                d.field_status(owner, f)
                for f in ("correspondence_street", "correspondence_postal_code", "correspondence_city")
            ]
            filled = [s for s in statuses if s is not None]
            if len(filled) == 3 and all(s == "confirmed" for s in filled):
                st, hint = F, ""
            elif filled:
                st, hint = P, "Anschrift unvollständig oder nicht bestätigt"
            else:
                st, hint = M, "keine Anschrift"
            specs.append(
                Spec(
                    "addresses",
                    ScopeType.ASSIGNMENT,
                    st,
                    unit_id=u.pk,
                    assignment_id=a.pk,
                    details={"hint": hint},
                )
            )
            # 6 Kommunikationsdaten (ANNAHME: ein bestaetigter Kanal reicht)
            channels = [d.field_status(owner, f) for f in ("email", "phone", "mobile")]
            confirmed = [s for s in channels if s == "confirmed"]
            present = [s for s in channels if s is not None]
            if len(confirmed) >= required_channels:
                st, hint = F, ""
            elif present:
                st, hint = P, "Kanal vorhanden, nicht bestätigt"
            else:
                st, hint = M, "kein Kommunikationskanal"
            specs.append(
                Spec(
                    "contact_data",
                    ScopeType.ASSIGNMENT,
                    st,
                    unit_id=u.pk,
                    assignment_id=a.pk,
                    details={"hint": hint},
                )
            )
            # 7 Eigentuemerkonto
            specs.append(
                _link_spec(
                    "owner_accounts",
                    a,
                    d.links_for(a, ("eigentuemerkonto",)),
                    missing_hint="kein Eigentümerkonto zum Stichtag",
                )
            )
            # 8 und 9 je Zuordnung: Saldo erfasst oder Einheit in der Aufstellung genannt
            for code in ("open_house_fees", "credits"):
                if a.balance_at_takeover is not None or u.pk in d.units_in_statement:
                    specs.append(
                        Spec(
                            code,
                            ScopeType.ASSIGNMENT,
                            F,
                            unit_id=u.pk,
                            assignment_id=a.pk,
                            details={
                                "hint": "Saldo erfasst"
                                if a.balance_at_takeover is not None
                                else "Einheit in der Aufstellung genannt"
                            },
                        )
                    )
                else:
                    specs.append(
                        Spec(
                            code,
                            ScopeType.ASSIGNMENT,
                            M,
                            unit_id=u.pk,
                            assignment_id=a.pk,
                            details={"hint": "kein Saldo zum Stichtag"},
                        )
                    )
            # 10 Einzelabrechnungen je Jahr
            for year in p.years:
                if not overlaps_year(a.valid_from, a.valid_to, year, p.fiscal_start_month):
                    specs.append(
                        Spec(
                            "annual_statement_year",
                            ScopeType.ASSIGNMENT,
                            NA,
                            unit_id=u.pk,
                            assignment_id=a.pk,
                            period_year=year,
                            details={"hint": f"Eigentümer in {year} nicht zugeordnet"},
                        )
                    )
                    continue
                spec = _link_spec(
                    "annual_statement_year",
                    a,
                    d.links_for(a, STATEMENT_TYPES, year),
                    year=year,
                    missing_hint=f"Einzelabrechnung {year} fehlt",
                )
                if spec.status == M:
                    due = statement_due(year, p.fiscal_start_month)
                    if d.today < due:
                        spec.status = NA
                        spec.details = {
                            "hint": f"noch nicht fällig (erwartbar ab {due.strftime('%d.%m.%Y')})"
                        }
                specs.append(spec)
            # 11 Wirtschaftsplaene je Jahr
            for year in p.plan_years:
                if not overlaps_year(a.valid_from, a.valid_to, year, p.fiscal_start_month):
                    specs.append(
                        Spec(
                            "business_plan_year",
                            ScopeType.ASSIGNMENT,
                            NA,
                            unit_id=u.pk,
                            assignment_id=a.pk,
                            period_year=year,
                            details={"hint": f"Eigentümer in {year} nicht zugeordnet"},
                        )
                    )
                    continue
                specs.append(
                    _link_spec(
                        "business_plan_year",
                        a,
                        d.links_for(a, PLAN_TYPES, year),
                        year=year,
                        missing_hint=f"Wirtschaftsplan {year} fehlt",
                    )
                )
            # 12 SEPA
            if obj.sepa_used is False:
                specs.append(
                    Spec(
                        "sepa_mandate",
                        ScopeType.ASSIGNMENT,
                        NA,
                        unit_id=u.pk,
                        assignment_id=a.pk,
                        details={"hint": "SEPA im Objekt nicht verwendet"},
                    )
                )
            else:
                mandates = d.links_for(a, ("sepa_mandat",))
                flag = owner.sepa_mandate_present
                if flag is False:
                    specs.append(
                        Spec(
                            "sepa_mandate",
                            ScopeType.ASSIGNMENT,
                            NA,
                            unit_id=u.pk,
                            assignment_id=a.pk,
                            details={"hint": "kein Mandat laut Stammdaten"},
                        )
                    )
                elif mandates and mandates[0].status == "confirmed":
                    specs.append(
                        Spec(
                            "sepa_mandate",
                            ScopeType.ASSIGNMENT,
                            F,
                            unit_id=u.pk,
                            assignment_id=a.pk,
                            details={"hint": ""},
                            evidence_document_id=mandates[0].document_id,
                            evidence_link_id=mandates[0].pk,
                        )
                    )
                elif flag or mandates:
                    specs.append(
                        Spec(
                            "sepa_mandate",
                            ScopeType.ASSIGNMENT,
                            P,
                            unit_id=u.pk,
                            assignment_id=a.pk,
                            details={
                                "hint": "Kennzeichen ohne bestätigtes Mandatsdokument"
                                if flag
                                else "Mandat vorgeschlagen, nicht bestätigt"
                            },
                            evidence_link_id=mandates[0].pk if mandates else None,
                        )
                    )
                else:
                    specs.append(
                        Spec(
                            "sepa_mandate",
                            ScopeType.ASSIGNMENT,
                            M,
                            unit_id=u.pk,
                            assignment_id=a.pk,
                            details={"hint": "SEPA-Mandat unbekannt"},
                        )
                    )
            # 13 Sonderumlagen nur bei bekannter Umlage im Zeitraum
            if obj.special_levies_in_period is True:
                specs.append(
                    _link_spec(
                        "special_levy",
                        a,
                        d.links_for(a, ("sonderumlage_einzel",)),
                        missing_hint="Unterlagen zur Sonderumlage fehlen",
                    )
                )
            elif obj.special_levies_in_period is False:
                specs.append(
                    Spec(
                        "special_levy",
                        ScopeType.ASSIGNMENT,
                        NA,
                        unit_id=u.pk,
                        assignment_id=a.pk,
                        details={"hint": "keine Sonderumlagen im Zeitraum"},
                    )
                )
            # 14 und 15 je Zuordnung nur positiv (Abwesenheit ist kein Mangel je Eigentuemer)
            for code, types in (
                ("payment_agreement", ("zahlungsvereinbarung",)),
                ("dunning_procedure", DUNNING_TYPES),
            ):
                links = d.links_for(a, types)
                if links and links[0].status == "confirmed":
                    specs.append(
                        Spec(
                            code,
                            ScopeType.ASSIGNMENT,
                            F,
                            unit_id=u.pk,
                            assignment_id=a.pk,
                            details={"hint": ""},
                            evidence_document_id=links[0].document_id,
                            evidence_link_id=links[0].pk,
                        )
                    )
                else:
                    specs.append(
                        Spec(
                            code,
                            ScopeType.ASSIGNMENT,
                            NA,
                            unit_id=u.pk,
                            assignment_id=a.pk,
                            details={"hint": "kein Vorgang bekannt"},
                        )
                    )
    # Objektebene 8 und 9
    statements = d.docs_by_type.get("rueckstandsaufstellung", [])
    for code in ("open_house_fees", "credits"):
        specs.append(
            _doc_spec(code, statements, missing_hint="Rückstands- und Guthabenaufstellung zum Stichtag fehlt")
        )
    # Objektebene 12 und 13 (Klaerung), 14 und 15 (Negativnachweis)
    if obj.sepa_used is None:
        specs.append(
            Spec("sepa_mandate", ScopeType.OBJECT, M, details={"hint": "Klärung SEPA-Nutzung im Objekt"})
        )
    else:
        specs.append(
            Spec(
                "sepa_mandate",
                ScopeType.OBJECT,
                F if obj.sepa_used else NA,
                details={"hint": "SEPA-Nutzung erfasst"},
            )
        )
    if obj.special_levies_in_period is None:
        specs.append(
            Spec(
                "special_levy",
                ScopeType.OBJECT,
                M,
                details={"hint": "Klärung Sonderumlagen im Übernahmezeitraum"},
            )
        )
    else:
        specs.append(
            Spec(
                "special_levy",
                ScopeType.OBJECT,
                F if obj.special_levies_in_period else NA,
                details={"hint": "Sonderumlagen erfasst"},
            )
        )
    for code in ("payment_agreement", "dunning_procedure"):
        specs.append(
            Spec(code, ScopeType.OBJECT, M, details={"hint": NEGATIVE_PROOF_HINT, "negative_proof": True})
        )
    return specs


def _all_units_known(d: Data) -> Spec:
    n = len(d.units)
    expected = d.obj.expected_unit_count
    if n == 0:
        return Spec(
            "all_units_known", ScopeType.OBJECT, M, details={"hint": "keine Einheiten erfasst", "units": 0}
        )
    details: dict = {"units": n, "expected": expected}
    if expected is None:
        details["hint"] = "Sollzahl der Einheiten erfassen (Teilungserklärung oder Eigentümerliste)"
        return Spec("all_units_known", ScopeType.OBJECT, P, details=details)
    if n != expected:
        details["hint"] = f"{n} Einheiten erfasst, Sollzahl {expected}"
        return Spec("all_units_known", ScopeType.OBJECT, P, details=details)
    shares = [u for u in d.units if u.co_ownership_share is not None and u.co_ownership_share_base]
    if len(shares) == n:
        bases = {u.co_ownership_share_base for u in shares}
        total = sum(u.co_ownership_share for u in shares)
        if len(bases) != 1 or total != next(iter(bases)):
            details["hint"] = f"Summe der Miteigentumsanteile {total} ungleich Nenner {sorted(bases)}"
            return Spec("all_units_known", ScopeType.OBJECT, P, details=details)
        details["hint"] = "Anzahl und Miteigentumsanteile stimmen"
    else:
        details["hint"] = "Anzahl stimmt, Miteigentumsanteile nicht vollständig erfasst"
    return Spec("all_units_known", ScopeType.OBJECT, F, details=details)


# ---------------------------------------------------------------- Mietkatalog (H 3.6, Vorschlag)
def evaluate_rental(d: Data, units: list[Unit], *, include_shared: bool = True) -> list[Spec]:
    obj = d.obj
    specs: list[Spec] = []
    committed = [b for b in d.batches if b.import_kind in TENANT_LIST_KINDS and b.status == "committed"]
    pending = [
        b for b in d.batches if b.import_kind in TENANT_LIST_KINDS and b.status in PENDING_BATCH_STATUSES
    ]
    if committed:
        specs.append(
            Spec("tenant_list_complete", ScopeType.OBJECT, F, details={"hint": f"Import {committed[0].pk}"})
        )
    elif pending:
        specs.append(
            Spec(
                "tenant_list_complete",
                ScopeType.OBJECT,
                P,
                details={"hint": f"Import {pending[0].pk} noch nicht abgeschlossen"},
            )
        )
    else:
        specs.append(
            Spec(
                "tenant_list_complete", ScopeType.OBJECT, M, details={"hint": "keine Mieterliste importiert"}
            )
        )
    if include_shared:
        specs.append(_all_units_known(d))
    for u in units:
        tenants = d.tenants_by_unit.get(u.pk, [])
        if any(t.data_status == "confirmed" for t in tenants):
            specs.append(Spec("tenant_per_unit", ScopeType.UNIT, F, unit_id=u.pk, details={"hint": ""}))
        elif tenants:
            specs.append(
                Spec(
                    "tenant_per_unit",
                    ScopeType.UNIT,
                    P,
                    unit_id=u.pk,
                    details={"hint": "Mieterzuordnung nicht bestätigt"},
                )
            )
        elif u.vacancy_confirmed:
            specs.append(
                Spec(
                    "tenant_per_unit",
                    ScopeType.UNIT,
                    F,
                    unit_id=u.pk,
                    details={"hint": "Leerstand bestätigt"},
                )
            )
        else:
            specs.append(
                Spec(
                    "tenant_per_unit",
                    ScopeType.UNIT,
                    M,
                    unit_id=u.pk,
                    details={"hint": "kein Mieter und kein bestätigter Leerstand"},
                )
            )
            continue
        if not tenants:
            continue
        tenant_names = ", ".join(
            t.tenant.company_name or t.tenant.last_name or str(t.tenant) for t in tenants
        )
        lease = next((t.lease for t in tenants if t.lease_id), None)
        values = (
            [
                getattr(lease, f, None)
                for f in ("start_date", "base_rent", "utilities_prepayment", "heating_prepayment")
            ]
            if lease
            else []
        )
        present = [v for v in values if v is not None]
        if lease and len(present) == 4 and lease.data_status == "confirmed":
            st, hint = F, ""
        elif present:
            st, hint = P, "Mietkonditionen unvollständig oder nicht bestätigt"
        else:
            st, hint = M, "kein Mietverhältnis mit Konditionen"
        specs.append(
            Spec(
                "lease_terms",
                ScopeType.UNIT,
                st,
                unit_id=u.pk,
                details={"hint": hint, "tenant": tenant_names},
            )
        )
        if lease and lease.deposit_amount is not None and lease.deposit_type not in (None, "", "unknown"):
            st, hint = F, ""
        elif lease and (lease.deposit_amount is not None or lease.deposit_type not in (None, "", "unknown")):
            st, hint = P, "Kaution nur teilweise erfasst"
        else:
            st, hint = M, "Kaution nicht erfasst"
        specs.append(
            Spec("deposit", ScopeType.UNIT, st, unit_id=u.pk, details={"hint": hint, "tenant": tenant_names})
        )
        if lease and lease.rent_adjustment_type not in (None, "", "unknown"):
            specs.append(
                Spec(
                    "rent_adjustment",
                    ScopeType.UNIT,
                    F,
                    unit_id=u.pk,
                    details={"hint": "", "tenant": tenant_names},
                )
            )
        else:
            specs.append(
                Spec(
                    "rent_adjustment",
                    ScopeType.UNIT,
                    M,
                    unit_id=u.pk,
                    details={"hint": "Staffel oder Index unbekannt", "tenant": tenant_names},
                )
            )
        channels = [getattr(t.tenant, f, None) for t in tenants for f in ("email", "phone", "mobile")]
        if any(channels):
            specs.append(
                Spec(
                    "tenant_contact_data",
                    ScopeType.UNIT,
                    F,
                    unit_id=u.pk,
                    details={"hint": "", "tenant": tenant_names},
                )
            )
        else:
            specs.append(
                Spec(
                    "tenant_contact_data",
                    ScopeType.UNIT,
                    M,
                    unit_id=u.pk,
                    details={"hint": "kein Kommunikationskanal", "tenant": tenant_names},
                )
            )
        if obj.sepa_used is False:
            specs.append(
                Spec(
                    "tenant_sepa_mandate",
                    ScopeType.UNIT,
                    NA,
                    unit_id=u.pk,
                    details={"hint": "SEPA im Objekt nicht verwendet", "tenant": tenant_names},
                )
            )
        else:
            flags = [t.tenant.sepa_mandate_present for t in tenants]
            if all(f is True for f in flags):
                st, hint = F, ""
            elif all(f is False for f in flags):
                st, hint = NA, "kein Mandat laut Stammdaten"
            else:
                st, hint = M, "SEPA-Mandat unbekannt"
            specs.append(
                Spec(
                    "tenant_sepa_mandate",
                    ScopeType.UNIT,
                    st,
                    unit_id=u.pk,
                    details={"hint": hint, "tenant": tenant_names},
                )
            )
    for code in ("tenant_accounts", "tenant_dunning_procedure"):
        specs.append(
            Spec(code, ScopeType.OBJECT, M, details={"hint": NEGATIVE_PROOF_HINT, "negative_proof": True})
        )
    return specs


# ---------------------------------------------------------------- Lauf mit Upsert (H 3.5)
def evaluate_object(
    obj: ManagedObject, *, today: date | None = None, trigger: str = "manual", user=None
) -> dict:
    today = today or timezone.localdate()
    period = period_for(obj, today)
    d = Data(obj, period, today)
    checks = {c.code: c for c in CompletenessCheck.objects.filter(is_active=True)}
    specs: list[Spec] = []
    if obj.management_type in WEG_TYPES:
        specs.extend(evaluate_weg(d))
    if obj.management_type == "rental":
        specs.extend(evaluate_rental(d, d.units))
    elif obj.management_type == "weg_with_se":
        se_units = [u for u in d.units if u.se_managed]
        if se_units:
            specs.extend(evaluate_rental(d, se_units, include_shared=False))
    by_key: dict[str, Spec] = {}
    for s in specs:
        if s.check_code not in checks:
            continue
        mt = checks[s.check_code].management_types
        if mt and obj.management_type not in mt:
            continue
        by_key[s.key] = s  # letzter Eintrag gewinnt, Schluessel sind eindeutig
    for s in by_key.values():
        s.details = {**s.details, "provisional": period.provisional} if period.provisional else s.details
    now = timezone.now()
    with transaction.atomic():
        existing = {f.position_key: f for f in CompletenessFinding.objects.filter(object=obj)}
        new: list[CompletenessFinding] = []
        changed: list[CompletenessFinding] = []
        touched: list[int] = []
        for key, s in by_key.items():
            f = existing.get(key)
            if f is None:
                new.append(
                    CompletenessFinding(
                        object=obj,
                        check_code=s.check_code,
                        scope_type=s.scope_type,
                        unit_id=s.unit_id,
                        assignment_id=s.assignment_id,
                        period_year=s.period_year,
                        status=s.status,
                        evidence_document_id=s.evidence_document_id,
                        evidence_link_id=s.evidence_link_id,
                        details=s.details,
                        last_evaluated_at=now,
                    )
                )
                continue
            same = (
                f.status == s.status
                and (f.details or {}) == s.details
                and f.evidence_document_id == s.evidence_document_id
                and f.evidence_link_id == s.evidence_link_id
            )
            if same:
                touched.append(f.pk)
            else:
                f.status = s.status
                f.details = s.details
                f.evidence_document_id = s.evidence_document_id
                f.evidence_link_id = s.evidence_link_id
                f.last_evaluated_at = now
                changed.append(f)
        CompletenessFinding.objects.bulk_create(new, batch_size=500)
        CompletenessFinding.objects.bulk_update(
            changed,
            ["status", "details", "evidence_document", "evidence_link", "last_evaluated_at", "updated_at"],
            batch_size=500,
        )
        if touched:
            CompletenessFinding.objects.filter(pk__in=touched).update(last_evaluated_at=now)
        stale = [f for key, f in existing.items() if key not in by_key and not f.manual_status]
        if stale:
            CompletenessFinding.objects.filter(pk__in=[f.pk for f in stale]).delete()
    result = {
        "trigger": trigger,
        "findings": len(by_key),
        "created": len(new),
        "changed": len(changed),
        "unchanged": len(touched),
        "removed": len(stale),
        "period": {"start": period.start.isoformat(), "end": period.end.isoformat(), "years": period.years},
        "hints": period.hints,
    }
    if user is not None:
        record(
            "completeness.evaluate",
            entity_type="object",
            entity_id=obj.pk,
            object_id=obj.pk,
            actor=user,
            after={k: v for k, v in result.items() if k != "hints"},
        )
    logger.info("Vollständigkeit Objekt %s (%s): %s", obj.pk, trigger, result)
    return result


# ---------------------------------------------------------------- Zusammenfassung und Offene Punkte (H 3.4)
def effective_status(f: CompletenessFinding) -> str:
    return f.manual_status or f.status


def _consequential(f: CompletenessFinding) -> bool:
    return bool((f.details or {}).get("consequential"))


def summary(obj: ManagedObject) -> dict:
    findings = list(CompletenessFinding.objects.filter(object=obj).select_related("unit"))
    checks = {c.code: c for c in CompletenessCheck.objects.all()}
    if not findings:
        return {
            "status": "not_evaluated",
            "ratio": None,
            "units": {},
            "unit_counts": {},
            "missing_by_category": {},
            "last_evaluated_at": None,
            "counts": {},
        }
    per_unit: dict[int, dict] = {}
    counts = {F: 0, P: 0, M: 0, NA: 0}
    missing_by_category: dict[str, int] = {}
    blocking_missing = False
    for f in findings:
        eff = effective_status(f)
        cons = _consequential(f)
        if not cons:
            counts[eff] = counts.get(eff, 0) + 1
        if eff == M and not cons:
            cat = checks[f.check_code].category if f.check_code in checks else "sonstige"
            missing_by_category[cat or "sonstige"] = missing_by_category.get(cat or "sonstige", 0) + 1
            if f.check_code in checks and checks[f.check_code].blocking:
                blocking_missing = True
        if f.unit_id:
            bucket = per_unit.setdefault(
                f.unit_id, {"label": f.unit.unit_label, "missing": 0, "partial": 0, "not_evaluable": False}
            )
            if f.check_code in ("owner_per_unit", "tenant_per_unit") and eff == M:
                bucket["not_evaluable"] = True
            if cons:
                continue
            if eff == M:
                bucket["missing"] += 1
            elif eff == P:
                bucket["partial"] += 1
    units: dict[int, dict] = {}
    unit_counts = {"complete": 0, "partial": 0, "incomplete": 0, "not_evaluable": 0}
    for uid, b in per_unit.items():
        if b["not_evaluable"]:
            st = "not_evaluable"
        elif b["missing"]:
            st = "incomplete"
        elif b["partial"]:
            st = "partial"
        else:
            st = "complete"
        units[uid] = {**b, "status": st}
        unit_counts[st] += 1
    applicable = counts[F] + counts[P] + counts[M]
    ratio = round(counts[F] / applicable, 4) if applicable else None
    pct = float(store.get("completeness.mostly_complete_pct", 90))
    if ratio is None:
        status = "not_evaluated"
    elif ratio >= 1.0:
        status = "complete"
    elif ratio * 100 >= pct and not blocking_missing:
        status = "mostly_complete"
    else:
        status = "incomplete"
    return {
        "status": status,
        "ratio": ratio,
        "counts": counts,
        "units": units,
        "unit_counts": unit_counts,
        "missing_by_category": missing_by_category,
        "blocking_missing": blocking_missing,
        "last_evaluated_at": max(f.last_evaluated_at for f in findings),
    }


def open_items(obj: ManagedObject) -> list[dict]:
    """Eine Zeile je Finding mit Status missing oder partial (H 3.4); Objektfindings zuerst, dann Einheit, Pruefpunkt,
    Jahr. Grundlage fuer Blatt 3 der Listen und fuer die Nachforderung."""
    checks = {c.code: c for c in CompletenessCheck.objects.all()}
    rows = []
    qs = CompletenessFinding.objects.filter(object=obj).select_related(
        "unit", "assignment__owner", "evidence_document"
    )
    for f in qs:
        eff = effective_status(f)
        if eff not in (M, P) or _consequential(f):
            continue
        check = checks.get(f.check_code)
        owner = ""
        if f.assignment_id:
            o = f.assignment.owner
            owner = o.company_name or o.last_name or str(o)
        elif (f.details or {}).get("tenant"):
            owner = f.details["tenant"]
        details = f.details or {}
        rows.append(
            {
                "finding_id": f.pk,
                "check_code": f.check_code,
                "check_name": check.name if check else f.check_code,
                "category": (check.category if check else None) or "sonstige",
                "sort_order": check.sort_order if check else 999,
                "scope_type": f.scope_type,
                "unit_label": f.unit.unit_label if f.unit_id else "",
                "owner": owner,
                "period_year": f.period_year,
                "status": eff,
                "hint": details.get("hint") or "",
                "evidence": f.evidence_document.current_name if f.evidence_document_id else "",
                "include_in_request": f.include_in_request,
                "manual": f.manual_reason or "",
                "requested_deadline": details.get("requested_deadline"),
                "requested_at": details.get("requested_at"),
            }
        )
    rows.sort(key=lambda r: (r["unit_label"] != "", r["unit_label"], r["sort_order"], r["period_year"] or 0))
    return rows


def set_manual(
    finding: CompletenessFinding,
    user,
    *,
    status: str | None,
    reason: str | None,
    include_in_request: bool | None = None,
    request=None,
) -> CompletenessFinding:
    """Manuelle Uebersteuerung (Negativerklaerung liegt vor, Punkt nicht anwendbar) mit Pflichtgrund und Audit."""
    if status not in (None, "", *MANUAL_STATUSES):
        raise ValueError("Übersteuerung nur auf erfüllt oder nicht anwendbar")
    if status and not (reason or "").strip():
        raise ValueError("Grund ist Pflicht")
    before = {"manual_status": finding.manual_status, "include_in_request": finding.include_in_request}
    finding.manual_status = status or None
    finding.manual_reason = (reason or "").strip() or None if status else None
    finding.manual_by = user if status else None
    finding.manual_at = timezone.now() if status else None
    if include_in_request is not None:
        finding.include_in_request = include_in_request
    finding.save(
        update_fields=[
            "manual_status",
            "manual_reason",
            "manual_by",
            "manual_at",
            "include_in_request",
            "updated_at",
        ]
    )
    record(
        "completeness.override",
        entity_type="completeness_finding",
        entity_id=finding.pk,
        object_id=finding.object_id,
        request=request,
        actor=user,
        before=before,
        after={"manual_status": finding.manual_status, "include_in_request": finding.include_in_request},
        reason=reason,
    )
    return finding
