"""Fachfunktionen zu Stammdaten (Fachentwurf D 3.4, 12.1; H 5.5; docs/architektur.md 5.4, 9.3).

- Ueberlappungspruefung fuer Zuordnungen in der Transaktion (SELECT ... FOR UPDATE auf die Einheit)
- Stichtags- und Zeitraumabfrage: wer war Eigentuemer (historische Zuordnung, CR 14 Pflichttest)
- Datensatzstatus aus field_provenance (incomplete vor ai_suggested vor confirmed, B-31)
- IBAN-Behandlung: nur iban_last4 und iban_hash (HMAC), kein Klartext (B-18)
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Q, QuerySet
from django.utils import timezone

from apps.objects.models import Unit
from apps.parties.models import FieldProvenance, Owner, OwnerUnitAssignment, PartyType
from objektakte.masking import iban_hmac, iban_mod97, normalize_iban


class OverlapError(ValueError):
    """Dieselbe Eigentuemer-Einheit-Kombination hat bereits eine Zuordnung im Zeitraum (D 3.4)."""


class InvalidIban(ValueError):
    pass


# ---------------------------------------------------------------- Zeitraeume
def periods_overlap(a_from: date | None, a_to: date | None, b_from: date | None, b_to: date | None) -> bool:
    """Einschliessliche Grenzen; NULL bedeutet unbegrenzt in der Vergangenheit (from) bzw. aktuell (to)."""
    a_start = a_from or date.min
    a_end = a_to or date.max
    b_start = b_from or date.min
    b_end = b_to or date.max
    return a_start <= b_end and b_start <= a_end


def _period_filter(prefix: str, on_from: date, on_to: date) -> Q:
    return (Q(**{f"{prefix}valid_from__isnull": True}) | Q(**{f"{prefix}valid_from__lte": on_to})) & (
        Q(**{f"{prefix}valid_to__isnull": True}) | Q(**{f"{prefix}valid_to__gte": on_from})
    )


def owners_for_period(
    unit: Unit | int, period_from: date, period_to: date | None = None
) -> QuerySet[OwnerUnitAssignment]:
    """Zuordnungen, die den Zeitraum beruehren (docs/architektur.md 5.4: valid_from <= Ende und valid_to >= Beginn oder NULL)."""
    period_to = period_to or period_from
    unit_id = unit.pk if isinstance(unit, Unit) else unit
    return (
        OwnerUnitAssignment.active.filter(unit_id=unit_id, owner__deleted_at__isnull=True)
        .filter(_period_filter("", period_from, period_to))
        .select_related("owner")
        .order_by("owner__search_name")
    )


def owners_at(unit: Unit | int, on_date: date) -> QuerySet[OwnerUnitAssignment]:
    """Wer war am Stichtag Eigentuemer (D 12.1). Mehrere Zeilen bei Mehrfacheigentum."""
    return owners_for_period(unit, on_date, on_date)


def owners_for_document(
    unit: Unit | int,
    *,
    period_year: int | None = None,
    period_from: date | None = None,
    period_to: date | None = None,
    document_date: date | None = None,
) -> QuerySet[OwnerUnitAssignment] | None:
    """Zeitbezug in der Reihenfolge Abrechnungsjahr, expliziter Zeitraum, Dokumentdatum (docs/architektur.md 5.4).
    Ohne Zeitbezug None: dann entscheidet der Aufrufer ueber einen Review-Fall owner_candidates."""
    if period_year:
        return owners_for_period(unit, date(period_year, 1, 1), date(period_year, 12, 31))
    if period_from or period_to:
        start = period_from or period_to
        return owners_for_period(unit, start, period_to or period_from)
    if document_date:
        return owners_at(unit, document_date)
    return None


# ---------------------------------------------------------------- Zuordnung mit Ueberlappungspruefung
@dataclass(frozen=True)
class AssignmentResult:
    assignment: OwnerUnitAssignment
    share_warning: Decimal | None  # Summe der Anteile am Beginn ueber 1,0 (Warnung, keine Sperre)


def create_assignment(
    *,
    owner: Owner,
    unit: Unit,
    valid_from: date | None,
    valid_to: date | None,
    share: Decimal | None = None,
    data_status: str = "incomplete",
    user=None,
    confirmed: bool = False,
    notes: str | None = None,
    source_import_row=None,
    source_document=None,
) -> AssignmentResult:
    if valid_from and valid_to and valid_from > valid_to:
        raise ValueError("Eigentumsbeginn liegt nach dem Eigentumsende")
    with transaction.atomic():
        Unit.objects.select_for_update().get(pk=unit.pk)
        existing = OwnerUnitAssignment.active.filter(unit=unit, owner=owner)
        for other in existing:
            if periods_overlap(valid_from, valid_to, other.valid_from, other.valid_to):
                raise OverlapError(
                    f"Zuordnung von {owner} an {unit.unit_label} überschneidet sich mit Zuordnung {other.pk} "
                    f"({other.valid_from or 'unbekannt'} bis {other.valid_to or 'heute'})"
                )
        assignment = OwnerUnitAssignment.objects.create(
            owner=owner,
            unit=unit,
            valid_from=valid_from,
            valid_to=valid_to,
            share=share,
            data_status="confirmed" if confirmed else data_status,
            confirmed_by=user if confirmed else None,
            confirmed_at=timezone.now() if confirmed else None,
            notes=notes,
            source_import_row=source_import_row,
            source_document=source_document,
        )
        warning = None
        if share is not None:
            probe = valid_from or date.min
            total = sum((a.share or Decimal("0")) for a in owners_for_period(unit, probe, probe))
            if total > Decimal("1"):
                warning = total
        _name_owner_file(unit, assignment, user)
    return AssignmentResult(assignment, warning)


def _name_owner_file(unit: Unit, assignment: OwnerUnitAssignment, user) -> None:
    """Akten-Vorlage (11.09.2026): Die Eigentuemerakte der Einheit erhaelt mit der Zuordnung ihren Namen
    (Platzhalter WE01 wird zu WE01_Mustermann). Ohne Vorlage (Schalter owner_file.create_folders_eagerly aus und
    keine Platzhalterakte) bleibt es bei der Anlage der Akte mit der ersten Ablage (F 6.3)."""
    from apps.config import store
    from apps.parties.models import OwnerFile

    placeholder = OwnerFile.active.filter(
        unit=unit, file_kind="unit_owner", file_assignments__isnull=True
    ).exists()
    if not placeholder and not store.get("owner_file.create_folders_eagerly", False):
        return
    from apps.classification.ownerfiles import owner_file_for_assignments
    from apps.parties.unit_files import refresh_owner_file_name

    group = list(
        OwnerUnitAssignment.active.filter(
            unit=unit, valid_from=assignment.valid_from, valid_to=assignment.valid_to
        ).select_related("owner")
    )
    before_ids = set(OwnerFile.active.filter(unit=unit, file_kind="unit_owner").values_list("pk", flat=True))
    akte = owner_file_for_assignments(unit, group)
    if akte.pk not in before_ids and not (akte.name_basis or {}).get("adopted_placeholder"):
        # mit der Zuordnung angelegt (kein Platzhalter): der Name bleibt von der Anwendung gefuehrt und folgt der Gruppe
        akte.name_basis = {**(akte.name_basis or {}), "managed_name": True}
        akte.save(update_fields=["name_basis", "updated_at"])
    refresh_owner_file_name(akte, unit, group)
    from apps.drive.tasks import trigger_unit_folders

    user_id = getattr(user, "pk", None)
    transaction.on_commit(lambda: trigger_unit_folders(unit.object_id, user_id=user_id))


def end_assignment(assignment: OwnerUnitAssignment, valid_to: date, *, user=None) -> OwnerUnitAssignment:
    """Eigentuemerwechsel: Altzuordnung endet, nie Update von owner_id (D 3.4)."""
    if assignment.valid_from and valid_to < assignment.valid_from:
        raise ValueError("Ende liegt vor dem Beginn")
    assignment.valid_to = valid_to
    assignment.save(update_fields=["valid_to", "updated_at"])
    return assignment


def find_overlaps(unit: Unit | None = None) -> list[tuple[OwnerUnitAssignment, OwnerUnitAssignment]]:
    """Verstoesse fuer den naechtlichen Konsistenzlauf: gleiche Eigentuemer-Einheit-Kombination, ueberlappende Zeitraeume."""
    qs = OwnerUnitAssignment.active.select_related("owner", "unit").order_by(
        "unit_id", "owner_id", "valid_from", "id"
    )
    if unit is not None:
        qs = qs.filter(unit=unit)
    result = []
    by_pair: dict[tuple[int, int], list[OwnerUnitAssignment]] = {}
    for a in qs:
        by_pair.setdefault((a.unit_id, a.owner_id), []).append(a)
    for rows in by_pair.values():
        for i, a in enumerate(rows):
            for b in rows[i + 1 :]:
                if periods_overlap(a.valid_from, a.valid_to, b.valid_from, b.valid_to):
                    result.append((a, b))
    return result


# ---------------------------------------------------------------- Datensatzstatus (H 5.5, B-31)
STATUS_RANK = {"incomplete": 0, "ai_suggested": 1, "confirmed": 2}
REQUIRED_FIELDS = {
    "owner": (
        "last_name_or_company",
        "correspondence_street",
        "correspondence_postal_code",
        "correspondence_city",
    ),
    "unit": ("unit_label",),
    "owner_unit_assignment": ("valid_from",),
    "tenant": ("last_name_or_company",),
    "lease": ("start_date", "base_rent"),
    "tenant_unit_assignment": ("valid_from",),
    "object": ("object_number", "management_type"),
}


def derive_data_status(field_status: dict[str, str], required: tuple[str, ...]) -> str:
    """incomplete, wenn ein Pflichtfeld fehlt oder unvollstaendig ist; sonst ai_suggested, wenn ein Feld unbestaetigt ist;
    sonst confirmed (Vorrang unvollstaendig vor KI-Vorschlag vor bestaetigt)."""
    for name in required:
        if field_status.get(name, "incomplete") == "incomplete":
            return "incomplete"
    if any(s == "ai_suggested" for s in field_status.values()):
        return "ai_suggested"
    return "confirmed" if field_status else "incomplete"


def set_provenance(
    entity_type: str,
    entity_id: int,
    field_name: str,
    *,
    source_kind: str,
    status: str,
    user=None,
    confidence: Decimal | None = None,
    source_document=None,
    source_import_row=None,
    page_from: int | None = None,
    page_to: int | None = None,
) -> FieldProvenance:
    row, _ = FieldProvenance.objects.update_or_create(
        entity_type=entity_type,
        entity_id=entity_id,
        field_name=field_name,
        defaults={
            "source_kind": source_kind,
            "status": status,
            "set_by": user,
            "set_at": timezone.now(),
            "confidence": confidence,
            "source_document": source_document,
            "source_import_row": source_import_row,
            "source_page_from": page_from,
            "source_page_to": page_to,
        },
    )
    return row


def recompute_data_status(entity_type: str, entity) -> str:
    """Leitet data_status eines Datensatzes aus field_provenance ab und speichert ihn."""
    rows = FieldProvenance.objects.filter(entity_type=entity_type, entity_id=entity.pk)
    field_status = {r.field_name: r.status for r in rows}
    status = derive_data_status(field_status, REQUIRED_FIELDS.get(entity_type, ()))
    if getattr(entity, "data_status", None) != status:
        entity.data_status = status
        entity.save(update_fields=["data_status", "updated_at"])
    return status


# ---------------------------------------------------------------- Namen und IBAN
def search_name(*, type: str, first_name: str | None, last_name: str | None, company_name: str | None) -> str:
    """Normalisiert fuer Suche und Dublettenpruefung (D 3.3): Grossschreibung, Umlaute transliteriert, ohne Doppelleerzeichen."""
    from apps.drive.naming import transliterate

    if type == PartyType.NATURAL_PERSON:
        raw = " ".join(p for p in [last_name, first_name] if p)
    else:
        raw = company_name or last_name or ""
    return " ".join(transliterate(raw).upper().split())[:240]


def iban_fields(raw_iban: str | None) -> dict:
    """Aus einer eingegebenen IBAN werden ausschliesslich iban_last4 und iban_hash gebildet (B-18, F16).
    Der Klartext wird nicht gespeichert; security.store_full_iban bleibt falsch bis zur Entscheidung F16."""
    if not raw_iban or not raw_iban.strip():
        return {"iban_last4": None, "iban_hash": None}
    compact = normalize_iban(raw_iban)
    if not iban_mod97(compact):
        raise InvalidIban("IBAN-Prüfziffer ungültig")
    key = _hmac_key()
    return {"iban_last4": compact[-4:], "iban_hash": bytes.fromhex(iban_hmac(compact, key))}


def _hmac_key() -> bytes:
    raw = (settings.FIELD_KEYS or {}).get("iban_hmac", "")
    if not raw:
        if settings.DEBUG:
            return hashlib.sha256(f"{settings.SECRET_KEY}:iban_hmac".encode()).digest()
        from objektakte.secrets import SecretMissing

        raise SecretMissing("IBAN_HMAC_KEY fehlt")
    try:
        return base64.b64decode(raw, validate=True)
    except Exception:
        return hashlib.sha256(raw.encode("utf-8")).digest()
