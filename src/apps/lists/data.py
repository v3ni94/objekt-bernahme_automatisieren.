"""Datensatzbildung der Eigentuemer- und Mieterliste (CR 12a, Fachentwurf H 5.1, 5.2, 5.5).

Ausschliesslich Stammdatentabellen: eine Zeile je Zuordnung, Blaetter Aktuell und Historie, Einheiten ohne Zuordnung
mit Status unvollstaendig; Blatt Offene Punkte aus der Vollstaendigkeitspruefung (H 3.4). Spalten wortgetreu aus
lists.owner_columns und lists.tenant_columns; der Mindestumfang wird ergaenzt, falls die Konfiguration ihn entfernt.
IBAN erscheint nur maskiert (iban_last4), nie aus einem Klartext.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from django.utils import timezone

from apps.config import store
from apps.objects.models import ManagedObject, Unit
from apps.parties.models import FieldProvenance, OwnerUnitAssignment, TenantUnitAssignment

OWNER_MIN_COLUMNS = [
    "Einheit",
    "Einheitentyp",
    "Anrede",
    "Vorname",
    "Nachname",
    "Firma",
    "Straße und Hausnummer",
    "PLZ",
    "Ort",
    "Abweichende Zustelladresse",
    "Telefon",
    "Mobil",
    "E-Mail",
    "Miteigentumsanteil",
    "Eigentumsbeginn",
    "Eigentumsende",
    "Hausgeld monatlich",
    "SEPA-Mandat vorhanden",
    "IBAN (maskiert)",
    "Mehrere Eigentümer je Einheit",
    "Bemerkung",
    "Status",
    "Quelle",
]
TENANT_MIN_COLUMNS = [
    "Einheit",
    "Einheitentyp",
    "Anrede",
    "Vorname",
    "Nachname",
    "Firma",
    "Telefon",
    "Mobil",
    "E-Mail",
    "Mietbeginn",
    "Mietende",
    "Kaltmiete",
    "Nebenkostenvorauszahlung",
    "Heizkostenvorauszahlung",
    "Gesamtmiete",
    "Kaution Betrag",
    "Kaution Anlageform",
    "Staffel oder Index",
    "Anzahl Personen",
    "SEPA-Mandat vorhanden",
    "IBAN (maskiert)",
    "Bemerkung",
    "Status",
    "Quelle",
]
OPEN_COLUMNS = [
    "Einheit",
    "Eigentümer",
    "Prüfpunkt",
    "Jahr",
    "Status",
    "Hinweis",
    "Nachweis vorhanden",
    "In Nachforderung",
    "Manuelle Übersteuerung",
    "Angefordert bis",
]
AMOUNT_COLUMNS = {
    "Hausgeld monatlich",
    "Kaltmiete",
    "Nebenkostenvorauszahlung",
    "Heizkostenvorauszahlung",
    "Gesamtmiete",
    "Kaution Betrag",
}
DATE_COLUMNS = {"Eigentumsbeginn", "Eigentumsende", "Mietbeginn", "Mietende"}
TEXT_COLUMNS = {"PLZ"}  # fuehrende Null bleibt
WRAP_COLUMNS = {"Bemerkung", "Abweichende Zustelladresse", "Hinweis"}
STATUS_CONFIRMED, STATUS_AI, STATUS_INCOMPLETE = "bestätigt", "KI-Vorschlag", "unvollständig"
FINDING_STATUS_DE = {"missing": "fehlt", "partial": "teilweise"}
_NUM = re.compile(r"\d+")


@dataclass
class ListData:
    list_type: str
    title: str
    columns: list[str]
    current: list[dict]
    history: list[dict]
    open_items: list[dict]
    header: dict
    provenance: list[dict] = field(default_factory=list)
    content_hash: str = ""

    @property
    def open_columns(self) -> list[str]:
        return OPEN_COLUMNS

    def rows(self, sheet: str) -> list[dict]:
        return {"current": self.current, "history": self.history, "open": self.open_items}[sheet]


def _columns(key: str, minimum: list[str]) -> list[str]:
    configured = [str(c) for c in (store.get(key, []) or []) if str(c).strip()]
    return configured + [c for c in minimum if c not in configured]


def _ja_nein(value: bool | None) -> str:
    return "" if value is None else ("ja" if value else "nein")


def _unit_sort_key(unit: Unit) -> tuple:
    m = _NUM.search(unit.unit_number or "") or _NUM.search(unit.unit_label or "")
    return (int(m.group(0)) if m else 10**9, unit.unit_label_normalized or "")


def _share_text(unit: Unit) -> str | Decimal | None:
    if unit.co_ownership_share is None:
        return None
    if unit.co_ownership_share_base:
        share = unit.co_ownership_share.normalize()
        return f"{share:f}/{unit.co_ownership_share_base}"
    return unit.co_ownership_share


def _address(street, number) -> str:
    return " ".join(p for p in (street, number) if p)


def _delivery(owner) -> str:
    parts = [
        _address(owner.delivery_street, owner.delivery_house_number),
        " ".join(p for p in (owner.delivery_postal_code, owner.delivery_city) if p),
        owner.delivery_addition,
    ]
    return ", ".join(p for p in parts if p)


def _row_status(mandatory_present: bool, statuses: list[str | None]) -> str:
    """H 5.5: Luecke vor Vorschlag vor bestaetigt."""
    if not mandatory_present:
        return STATUS_INCOMPLETE
    if any(s == "ai_suggested" for s in statuses):
        return STATUS_AI
    return STATUS_CONFIRMED


def _source(assignment, provenance_docs: set[int]) -> str:
    if assignment.source_document_id and assignment.source_document is not None:
        base = assignment.source_document.current_name
    elif assignment.source_import_row_id and assignment.source_import_row is not None:
        base = assignment.source_import_row.batch.source_file_name
    else:
        base = "manuell"
    others = provenance_docs - ({assignment.source_document_id} if assignment.source_document_id else set())
    return f"{base} u. a." if others else base


def _empty_row(columns: list[str]) -> dict:
    return dict.fromkeys(columns)


# ---------------------------------------------------------------- Eigentuemerliste
def build_owner_list(obj: ManagedObject, *, today: date | None = None) -> ListData:
    today = today or timezone.localdate()
    columns = _columns("lists.owner_columns", OWNER_MIN_COLUMNS)
    units = sorted(Unit.active.filter(object=obj, status="active"), key=_unit_sort_key)
    assignments = list(
        OwnerUnitAssignment.active.filter(unit__object=obj)
        .select_related("owner", "unit", "source_document", "source_import_row__batch")
        .order_by("unit_id", "valid_from")
    )
    owner_ids = {a.owner_id for a in assignments}
    prov_rows = list(FieldProvenance.objects.filter(entity_type="owner", entity_id__in=owner_ids))
    prov_status: dict[int, list[str]] = {}
    prov_docs: dict[int, set[int]] = {}
    provenance_sheet: list[dict] = []
    for p in prov_rows:
        prov_status.setdefault(p.entity_id, []).append(p.status)
        if p.source_document_id:
            prov_docs.setdefault(p.entity_id, set()).add(p.source_document_id)
    by_unit: dict[int, list[OwnerUnitAssignment]] = {}
    for a in assignments:
        by_unit.setdefault(a.unit_id, []).append(a)
    current_rows: list[dict] = []
    history_rows: list[dict] = []
    for unit in units:
        lst = by_unit.get(unit.pk, [])
        current = [a for a in lst if a.valid_to is None or a.valid_to >= today]
        if not current:
            row = _empty_row(columns)
            row.update(
                {
                    "Einheit": unit.unit_label,
                    "Einheitentyp": unit.get_unit_type_display(),
                    "Miteigentumsanteil": _share_text(unit),
                    "Hausgeld monatlich": unit.house_fee_monthly,
                    "Status": STATUS_INCOMPLETE,
                    "Quelle": "",
                }
            )
            current_rows.append(row)
        for a in sorted(current, key=lambda a: a.owner.last_name or a.owner.company_name or ""):
            current_rows.append(
                _owner_row(columns, unit, a, current, prov_status, prov_docs, provenance_sheet, prov_rows)
            )
    for a in assignments:
        if a.valid_to is not None and a.valid_to < today:
            history_rows.append(_owner_row(columns, a.unit, a, [a], prov_status, prov_docs, None, prov_rows))
    history_rows.sort(key=lambda r: (str(r.get("Einheit") or ""), str(r.get("Eigentumsende") or "")))
    open_rows = _open_rows(obj)
    data = ListData(
        list_type="owner_list",
        title="Eigentümerliste",
        columns=columns,
        current=current_rows,
        history=history_rows,
        open_items=open_rows,
        header=_header(obj, len(current_rows), len(history_rows), len(open_rows)),
        provenance=provenance_sheet,
    )
    data.content_hash = content_hash(data)
    return data


def _owner_row(columns, unit, a, current, prov_status, prov_docs, provenance_sheet, prov_rows) -> dict:
    o = a.owner
    others = [x for x in current if x.pk != a.pk]
    joint = ""
    if others:
        parts = []
        for x in others:
            name = x.owner.company_name or x.owner.last_name or str(x.owner)
            parts.append(f"{name} (Anteil {x.share:f})" if x.share is not None else name)
        joint = "gemeinsam mit " + ", ".join(parts)
    notes = "; ".join(n for n in (o.notes, a.notes) if n)
    mandatory = bool(unit.unit_label) and bool(o.last_name or o.company_name) and a.valid_from is not None
    statuses = [o.data_status, unit.data_status, a.data_status, *prov_status.get(o.pk, [])]
    row = _empty_row(columns)
    row.update(
        {
            "Einheit": unit.unit_label,
            "Einheitentyp": unit.get_unit_type_display(),
            "Anrede": o.salutation,
            "Vorname": o.first_name,
            "Nachname": o.last_name,
            "Firma": o.company_name,
            "Straße und Hausnummer": _address(o.correspondence_street, o.correspondence_house_number),
            "PLZ": o.correspondence_postal_code,
            "Ort": o.correspondence_city,
            "Abweichende Zustelladresse": _delivery(o),
            "Telefon": o.phone,
            "Mobil": o.mobile,
            "E-Mail": o.email,
            "Miteigentumsanteil": _share_text(unit),
            "Eigentumsbeginn": a.valid_from,
            "Eigentumsende": a.valid_to,
            "Hausgeld monatlich": unit.house_fee_monthly,
            "SEPA-Mandat vorhanden": _ja_nein(o.sepa_mandate_present),
            "IBAN (maskiert)": o.iban_masked,
            "Mehrere Eigentümer je Einheit": joint,
            "Bemerkung": notes,
            "Status": _row_status(mandatory, statuses),
            "Quelle": _source(a, prov_docs.get(o.pk, set())),
        }
    )
    if provenance_sheet is not None:
        for p in prov_rows:
            if p.entity_id == o.pk:
                provenance_sheet.append(
                    {
                        "Einheit": unit.unit_label,
                        "Eigentümer": o.company_name or o.last_name or "",
                        "Feld": p.field_name,
                        "Quelle": p.source_document.current_name if p.source_document_id else p.source_kind,
                        "Status": p.status,
                    }
                )
    return row


# ---------------------------------------------------------------- Mieterliste
def build_tenant_list(obj: ManagedObject, *, today: date | None = None) -> ListData:
    today = today or timezone.localdate()
    columns = _columns("lists.tenant_columns", TENANT_MIN_COLUMNS)
    units = sorted(Unit.active.filter(object=obj, status="active"), key=_unit_sort_key)
    if obj.management_type == "weg_with_se":
        units = [u for u in units if u.se_managed]
    tuas = list(
        TenantUnitAssignment.objects.filter(unit__object=obj, deleted_at__isnull=True)
        .select_related("tenant", "unit", "lease", "source_document", "source_import_row__batch")
        .order_by("unit_id", "valid_from")
    )
    by_unit: dict[int, list[TenantUnitAssignment]] = {}
    for t in tuas:
        by_unit.setdefault(t.unit_id, []).append(t)
    current_rows: list[dict] = []
    history_rows: list[dict] = []
    for unit in units:
        lst = by_unit.get(unit.pk, [])
        current = [t for t in lst if t.valid_to is None or t.valid_to >= today]
        if not current:
            row = _empty_row(columns)
            row.update(
                {
                    "Einheit": unit.unit_label,
                    "Einheitentyp": unit.get_unit_type_display(),
                    "Bemerkung": "Leerstand bestätigt" if unit.vacancy_confirmed else "",
                    "Status": STATUS_CONFIRMED if unit.vacancy_confirmed else STATUS_INCOMPLETE,
                    "Quelle": "",
                }
            )
            current_rows.append(row)
        for t in current:
            current_rows.append(_tenant_row(columns, unit, t))
    for t in tuas:
        if t.valid_to is not None and t.valid_to < today:
            history_rows.append(_tenant_row(columns, t.unit, t))
    open_rows = _open_rows(obj)
    data = ListData(
        list_type="tenant_list",
        title="Mieterliste",
        columns=columns,
        current=current_rows,
        history=history_rows,
        open_items=open_rows,
        header=_header(obj, len(current_rows), len(history_rows), len(open_rows)),
    )
    data.content_hash = content_hash(data)
    return data


def _tenant_row(columns, unit, t) -> dict:
    p, lease = t.tenant, t.lease
    adjustment = ""
    if lease is not None:
        adjustment = {"graduated": "ja", "indexed": "ja", "none": "nein"}.get(
            lease.rent_adjustment_type or "", ""
        )
    notes = "; ".join(n for n in (p.notes, lease.notes if lease else None, t.notes) if n)
    start = (
        (lease.start_date if lease and lease.start_date else t.valid_from)
        if (lease or t.valid_from)
        else None
    )
    end = (lease.end_date if lease and lease.end_date else t.valid_to) if (lease or t.valid_to) else None
    mandatory = bool(unit.unit_label) and bool(p.last_name or p.company_name) and start is not None
    statuses = [p.data_status, unit.data_status, t.data_status, lease.data_status if lease else None]
    row = _empty_row(columns)
    row.update(
        {
            "Einheit": unit.unit_label,
            "Einheitentyp": unit.get_unit_type_display(),
            "Anrede": p.salutation,
            "Vorname": p.first_name,
            "Nachname": p.last_name,
            "Firma": p.company_name,
            "Telefon": p.phone,
            "Mobil": p.mobile,
            "E-Mail": p.email,
            "Mietbeginn": start,
            "Mietende": end,
            "Kaltmiete": lease.base_rent if lease else None,
            "Nebenkostenvorauszahlung": lease.utilities_prepayment if lease else None,
            "Heizkostenvorauszahlung": lease.heating_prepayment if lease else None,
            "Gesamtmiete": lease.total_rent if lease else None,
            "Kaution Betrag": lease.deposit_amount if lease else None,
            "Kaution Anlageform": (
                lease.get_deposit_type_display()
                if lease and lease.deposit_type and lease.deposit_type != "unknown"
                else ""
            ),
            "Staffel oder Index": adjustment,
            "Anzahl Personen": lease.persons_count if lease else None,
            "SEPA-Mandat vorhanden": _ja_nein(p.sepa_mandate_present),
            "IBAN (maskiert)": p.iban_masked,
            "Bemerkung": notes,
            "Status": _row_status(mandatory, statuses),
            "Quelle": _source(t, set()),
        }
    )
    return row


# ---------------------------------------------------------------- Offene Punkte, Kopf, Pruefsumme
def _open_rows(obj: ManagedObject) -> list[dict]:
    from apps.requirements.engine import open_items

    rows = []
    for i in open_items(obj):
        rows.append(
            {
                "Einheit": i["unit_label"],
                "Eigentümer": i["owner"],
                "Prüfpunkt": i["check_name"],
                "Jahr": i["period_year"],
                "Status": FINDING_STATUS_DE.get(i["status"], i["status"]),
                "Hinweis": i["hint"],
                "Nachweis vorhanden": i["evidence"],
                "In Nachforderung": "ja" if i["include_in_request"] else "nein",
                "Manuelle Übersteuerung": i["manual"],
                "Angefordert bis": i.get("requested_deadline") or "",
            }
        )
    return rows


def _header(obj: ManagedObject, n_current: int, n_history: int, n_open: int) -> dict:
    return {
        "object_number": obj.object_number,
        "object_name": obj.name or "",
        "management_type": obj.get_management_type_display(),
        "generated_at": timezone.localtime(),
        "rows_current": n_current,
        "rows_history": n_history,
        "rows_open": n_open,
    }


def _json_value(v):
    if isinstance(v, Decimal):
        return f"{v:f}"
    if isinstance(v, date):
        return v.isoformat()
    return v


def content_hash(data: ListData) -> str:
    """Pruefsumme ueber die sortierten Zeilen aller drei Blaetter (H 5.6); Erzeugungszeit zaehlt nicht."""
    payload = {
        "columns": data.columns,
        "current": [[_json_value(r.get(c)) for c in data.columns] for r in data.current],
        "history": [[_json_value(r.get(c)) for c in data.columns] for r in data.history],
        "open": [[_json_value(r.get(c)) for c in OPEN_COLUMNS] for r in data.open_items],
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def all_cell_texts(data: ListData) -> list[str]:
    out = []
    for sheet in (data.current, data.history, data.open_items, data.provenance):
        for r in sheet:
            out.extend(str(v) for v in r.values() if v not in (None, ""))
    return out
