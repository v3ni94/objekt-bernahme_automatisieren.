"""Importkette (Fachentwurf H 6.1, Umsetzungsplan M3): Annahme, Einlesen, Zuordnung, Erkennung, Uebernahme, Protokoll.

Grundsaetze: Nichts wird stillschweigend uebernommen oder verworfen (CR 15). Stammtabellen aendern sich nur bei der
Uebernahme bestaetigter Zeilen (commit_rows). IBAN erscheint nirgends im Klartext (raw_data maskiert, B-18).
rows_total ist immer gleich der Summe der Statuswerte (refresh_counters).
"""

from __future__ import annotations

import hashlib
import logging
import mimetypes
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from apps.audit.services import record
from apps.config import store
from apps.documents.models import Document
from apps.drive.object_numbers import same_object
from apps.imports import normalize as n
from apps.imports.mapping import header_row_index, mapping_from_proposals, propose_mapping
from apps.imports.matching import OwnerRecord, PersonQuery, match_owner
from apps.imports.models import ImportBatch, ImportRow, RowStatus
from apps.imports.names import split_names
from apps.imports.profiles import PROFILES, choose_profile
from apps.objects.models import ManagedObject, Unit
from apps.objects.units import parse_unit_label
from apps.parties import services as party_services
from apps.parties.models import Lease, Owner, OwnerUnitAssignment, Tenant, TenantUnitAssignment
from apps.review.models import CaseStatus, CaseType, ReviewCase

logger = logging.getLogger(__name__)
PARSER_VERSION = "1"
REJECT_PROPOSAL_REASONS = {"not_a_record", "other_object", "unit_status_not_active"}


class ImportError_(Exception):
    pass


# ---------------------------------------------------------------- Pfade
def transit_dir(obj: ManagedObject, sha256: str) -> Path:
    return Path(settings.OBJEKTAKTE["DATA_DIR"]) / "transit" / str(obj.pk) / "imports" / sha256


def import_dir(batch: ImportBatch) -> Path:
    return Path(settings.OBJEKTAKTE["DATA_DIR"]) / "imports" / str(batch.pk)


def source_path(batch: ImportBatch) -> Path:
    return transit_dir(batch.object, batch.source_sha256) / batch.source_file_name


# ---------------------------------------------------------------- 1 Annahme
def create_batch(
    obj: ManagedObject, *, filename: str, data: bytes, user=None, import_kind: str = "owner_list"
) -> tuple[ImportBatch, bool]:
    """Datei annehmen: SHA-256, Ablage im Transitbereich, documents-Zeile (source import), import_batches (uploaded)."""
    limit = int(store.get("documents.max_download_bytes", 524288000))
    if len(data) > limit:
        raise ImportError_(f"Datei größer als {limit} Byte")
    sha = hashlib.sha256(data).hexdigest()
    existing = ImportBatch.objects.filter(
        object=obj, source_sha256=sha, parser_version=PARSER_VERSION
    ).first()
    if existing:
        return existing, False
    safe_name = Path(filename).name.replace("/", "_") or "liste"
    target = transit_dir(obj, sha)
    target.mkdir(parents=True, exist_ok=True)
    (target / safe_name).write_bytes(data)
    mime = mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
    with transaction.atomic():
        doc, _ = Document.objects.get_or_create(
            object=obj,
            sha256=sha,
            defaults={
                "size_bytes": len(data),
                "mime_type": mime,
                "original_name": safe_name,
                "current_name": safe_name,
                "source": "import",
                "source_path": str(target / safe_name),
                "status": "hashed",
                "first_seen_at": timezone.now(),
            },
        )
        profile, scores = choose_profile(target / safe_name, safe_name)
        batch = ImportBatch.objects.create(
            object=obj,
            import_kind=import_kind,
            source_format=profile.source_format,
            parser_profile=profile.code,
            parser_version=PARSER_VERSION,
            source_document=doc,
            source_sha256=sha,
            source_file_name=safe_name,
            status="uploaded",
            uploaded_by=user,
            column_mapping={"profile_scores": scores},
        )
        record(
            "import.upload",
            entity_type="import_batch",
            entity_id=batch.pk,
            object_id=obj.pk,
            actor=user,
            after={"file": safe_name, "sha256": sha, "profile": profile.code},
        )
    return batch, True


# ---------------------------------------------------------------- 2 und 3 Einlesen und Zuordnungsvorschlag
def parse_batch(
    batch: ImportBatch, *, profile_code: str | None = None, sheet: str | None = None
) -> ImportBatch:
    profile = PROFILES[profile_code or batch.parser_profile]
    path = source_path(batch)
    try:
        table = profile.extract(path, sheet=sheet)
    except Exception as exc:  # Datei unlesbar: Lauf faellt kontrolliert aus, nichts geht verloren
        batch.status = "failed"
        batch.error_message = f"{type(exc).__name__}: {exc}"[:2000]
        batch.save(update_fields=["status", "error_message", "updated_at"])
        raise
    synonyms = store.get("import.column_synonyms", {}) or {}
    conf_min = float(store.get("import.column_confidence_min", 0.8))
    header_idx = header_row_index(table.rows, synonyms)
    header = [c or f"Spalte {i + 1}" for i, c in enumerate(table.rows[header_idx])] if table.rows else []
    data_rows = table.rows[header_idx + 1 :]
    conf_rows = (table.cell_confidence or [])[header_idx + 1 :]
    cell_conf_min = float(store.get("import.ocr_cell_confidence_min", 70))
    proposals = propose_mapping(header, data_rows[:50], synonyms, confidence_min=conf_min)
    mapping = mapping_from_proposals(proposals)
    mapping.update(
        {
            "profile": profile.code,
            "profile_scores": (batch.column_mapping or {}).get("profile_scores", {}),
            "header_index": header_idx,
            "header": header,
            "sheet": table.sheet,
            "meta": table.meta,
            "confidence_min": conf_min,
        }
    )
    hmac_key = party_services._hmac_key()
    with transaction.atomic():
        ImportRow.objects.filter(batch=batch).delete() if batch.status in ("uploaded", "parsed") else None
        rows = []
        for offset, cells in enumerate(data_rows):
            row_no = header_idx + 2 + offset  # 1-basiert wie in der Quelle
            padded = list(cells) + [""] * (len(header) - len(cells))
            raw, masked_hits = _mask_cells(header, padded, hmac_key)
            is_record = any((v or "").strip() for v in padded) and not _looks_like_total(padded)
            low_cells = _low_confidence_cells(
                header, padded, conf_rows[offset] if offset < len(conf_rows) else None, cell_conf_min
            )
            fields = {}
            if masked_hits:
                fields["_masked"] = masked_hits
            if low_cells:
                fields["_low_confidence_cells"] = low_cells
            rows.append(
                ImportRow(
                    batch=batch,
                    row_no=row_no,
                    sub_index=0,
                    raw_data=raw,
                    parsed_fields=fields,
                    status=RowStatus.PARSED if is_record else RowStatus.REJECTED,
                    uncertainty_reasons=(["low_ocr_confidence"] if low_cells else None)
                    if is_record
                    else ["not_a_record"],
                    notes=None if is_record else "automatisch: keine Datenzeile (leer oder Summenzeile)",
                )
            )
        ImportRow.objects.bulk_create(rows)
        batch.parser_profile = profile.code
        batch.source_format = profile.source_format
        batch.column_mapping = mapping
        batch.status = "parsed"
        batch.error_message = None
        batch.save(
            update_fields=[
                "parser_profile",
                "source_format",
                "column_mapping",
                "status",
                "error_message",
                "updated_at",
            ]
        )
        refresh_counters(batch)
    return batch


def _low_confidence_cells(
    header: list[str], cells: list[str], conf: list[float | None] | None, minimum: float
) -> list[dict]:
    """Zellen mit mittlerer Wortkonfidenz unter der Schwelle (ANNAHME A-33, import.ocr_cell_confidence_min); die Zeile
    wird unsicher (FA6), nichts wird verworfen."""
    if not conf:
        return []
    out = []
    for i, c in enumerate(conf):
        if c is not None and c < minimum and i < len(cells) and (cells[i] or "").strip():
            out.append({"column": header[i] if i < len(header) else f"Spalte {i + 1}", "confidence": c})
    return out


def _mask_cells(header: list[str], cells: list[str], hmac_key: bytes) -> tuple[dict, list[dict]]:
    """Rohzellen maskieren (D 9.2); IBAN-Treffer liefern vor der Maskierung letzte vier Stellen und HMAC (H 6.6)."""
    raw: dict = {}
    hits: list[dict] = []
    for i, v in enumerate(cells):
        name = header[i] if i < len(header) else f"Spalte {i + 1}"
        result = n.mask_text(n.clean_cell(v), mode="fulltext", hmac_key=hmac_key)
        raw[name] = result.text
        for h in result.hits:
            if h.kind == "iban" and h.hmac_hex:
                hits.append(
                    {
                        "column": name,
                        "iban_last4": h.last4,
                        "iban_hash": h.hmac_hex,
                        "mod97_valid": h.mod97_valid,
                    }
                )
    return raw, hits


def _looks_like_total(cells: list[str]) -> bool:
    first = next((c for c in cells if (c or "").strip()), "").strip().lower()
    return first in {"summe", "gesamt", "total", "summe:", "gesamt:"}


# ---------------------------------------------------------------- 4 bis 8 Erkennung je Zeile
@dataclass
class ParsedPerson:
    type: str
    first_name: str | None = None
    last_name: str | None = None
    company_name: str | None = None
    salutation: str | None = None
    title: str | None = None
    confidence: float = 1.0
    reasons: list[str] = field(default_factory=list)
    key: str = ""

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


def _cell(row: ImportRow, mapping: dict, target: str) -> str:
    idx = mapping.get("targets", {}).get(target)
    if idx is None:
        return ""
    header = mapping["header"][idx] if idx < len(mapping.get("header", [])) else None
    return (row.raw_data or {}).get(header, "") if header is not None else ""


def _persons_from_row(
    row: ImportRow, mapping: dict, name_target: str, cfg: dict
) -> tuple[list[ParsedPerson], list[str], str | None]:
    """Personen aus Freitext (Namens-Splitter) oder aus getrennten Spalten."""
    reasons: list[str] = []
    raw = _cell(row, mapping, name_target)
    if raw:
        result = split_names(
            raw, legal_form_markers=cfg["legal_form_markers"], thresholds=cfg["name_split_thresholds"]
        )
        if result.entity_type in ("legal_entity", "community"):
            p = ParsedPerson(
                result.entity_type, company_name=result.company_name, confidence=result.confidence
            )
            p.key = f"{p.type}:{n.clean_cell(p.company_name).casefold()}"
            return [p], list(result.reasons), result.correspondence_addition
        persons = []
        for person in result.persons:
            p = ParsedPerson(
                "natural_person",
                person.first_name,
                person.last_name,
                None,
                person.salutation,
                person.title,
                person.confidence,
                list(person.reasons),
            )
            p.key = f"np:{n.clean_cell(p.last_name).casefold()}|{n.clean_cell(p.first_name).casefold()}"
            persons.append(p)
        if result.is_uncertain:
            reasons.append(
                "name_split_ambiguous"
                if "name_split_ambiguous" in result.reasons or "name_tokens_ambiguous" in result.reasons
                else "name_split_low_confidence"
            )
            reasons.extend(r for r in result.reasons if r not in reasons)
        return persons, reasons, result.correspondence_addition
    last = _cell(row, mapping, "last_name")
    first = _cell(row, mapping, "first_name")
    company = _cell(row, mapping, "company_name")
    if company and not last:
        p = ParsedPerson("legal_entity", company_name=company)
        p.key = f"legal_entity:{company.casefold()}"
        return [p], reasons, None
    if last:
        p = ParsedPerson(
            "natural_person", first or None, last, salutation=_cell(row, mapping, "salutation") or None
        )
        p.key = f"np:{last.casefold()}|{(first or '').casefold()}"
        return [p], reasons, None
    return [], ["name_missing"], None


def _config() -> dict:
    return {
        "legal_form_markers": store.get("import.legal_form_markers", []) or [],
        "name_split_thresholds": store.get("import.name_split_thresholds", {"high": 0.9, "medium": 0.6}),
        "type_prefix_mapping": store.get("units.type_prefix_mapping", {}) or {},
        "strip_zeros": bool(store.get("units.normalize_strip_leading_zeros", True)),
        "weights": store.get("import.owner_match_weights", {}) or {},
        "thresholds": store.get("import.owner_match_thresholds", {"auto": 90, "candidate": 78}),
        "active_values": [
            v.lower() for v in (store.get("import.unit_status_active_values", ["aktiv"]) or [])
        ],
        "deposit_synonyms": store.get("import.deposit_type_synonyms", {}) or {},
        "hmac_key": party_services._hmac_key(),
    }


def _owner_records(obj: ManagedObject) -> list[OwnerRecord]:
    in_object: dict[int, set[int]] = {}
    for a in OwnerUnitAssignment.active.filter(unit__object=obj).values("owner_id", "unit_id"):
        in_object.setdefault(a["owner_id"], set()).add(a["unit_id"])
    records = []
    for o in Owner.active.all():
        records.append(
            OwnerRecord(
                id=o.pk,
                type=o.type,
                first_name=o.first_name,
                last_name=o.last_name,
                company_name=o.company_name,
                email=o.email,
                postal_code=o.correspondence_postal_code,
                street=o.correspondence_street,
                iban_hash_hex=o.iban_hash.hex() if o.iban_hash else None,
                unit_ids_in_object=frozenset(in_object.get(o.pk, set())),
                has_assignment_in_object=o.pk in in_object,
                display=str(o),
            )
        )
    return records


def _normalize_shared(row: ImportRow, mapping: dict, cfg: dict) -> tuple[dict, list[str], float]:
    """Felder, die fuer alle Personen einer Quellzeile gelten (Einheit, Adresse, Betraege, Zeitraum)."""
    fields: dict = {}
    reasons: list[str] = []
    conf = 1.0

    def put(
        name: str, value, confidence: float = 1.0, notes: list[str] | None = None, source: str | None = None
    ):
        nonlocal conf
        fields[name] = {
            "value": _json(value),
            "confidence": round(confidence, 2),
            "notes": notes or [],
            "source_column": source,
        }
        if value is not None and value != "":
            conf = min(conf, confidence)

    def col(target: str) -> str | None:
        idx = mapping.get("targets", {}).get(target)
        return mapping["header"][idx] if idx is not None else None

    label = _cell(row, mapping, "unit_label")
    if label:
        parsed = parse_unit_label(label, cfg["type_prefix_mapping"], strip_leading_zeros=cfg["strip_zeros"])
        put(
            "unit_label", parsed.label, 1.0 if parsed.parsed else 0.5, list(parsed.reasons), col("unit_label")
        )
        put("unit_label_normalized", parsed.label_normalized)
        put("unit_number", parsed.number)
        put("unit_type", parsed.unit_type, 1.0 if not parsed.reasons else 0.7)
        if parsed.rest:
            put("location", parsed.rest, 0.8, ["from_unit_label"], col("unit_label"))
        if not parsed.parsed:
            reasons.append("unit_label_unparsed")
    for target in (
        "external_ref",
        "building",
        "notes",
        "mandate_reference",
        "delivery_address_raw",
        "unit_status",
        "object_number",
        "object_name",
        "management_type",
        "salutation",
    ):
        v = _cell(row, mapping, target)
        if v:
            put(target, v, 1.0, None, col(target))
    loc = _cell(row, mapping, "location")
    if loc:
        put("location", loc, 1.0, None, col("location"))
    street_raw = _cell(row, mapping, "street")
    if street_raw:
        r = n.split_street(street_raw)
        hn = _cell(row, mapping, "house_number")
        put("street", r.value[0], r.confidence, r.notes, col("street"))
        put(
            "house_number",
            hn or r.value[1] or None,
            1.0 if hn else r.confidence,
            None,
            col("house_number") or col("street"),
        )
    for target, fn in (
        ("postal_code", n.parse_postal_code),
        ("country", n.country_iso2),
        ("email", n.normalize_email),
    ):
        v = _cell(row, mapping, target)
        if v:
            r = fn(v)
            put(target, r.value, r.confidence, r.notes, col(target))
            if r.notes and any(x.startswith("unparseable") for x in r.notes):
                reasons.append(f"{target}_unparseable")
    city = _cell(row, mapping, "city")
    if city:
        put("city", city, 1.0, None, col("city"))
    for target in ("phone", "mobile"):
        v = _cell(row, mapping, target)
        if v:
            r = n.classify_phone(v)
            kind, value = r.value
            put(kind or target, value, r.confidence, r.notes, col(target))
    for target in ("valid_from", "valid_to", "start_date", "end_date"):
        v = _cell(row, mapping, target)
        if v:
            r = n.parse_date(v)
            put(target, r.value, r.confidence, r.notes, col(target))
            if not r.ok:
                reasons.append("date_unparseable")
    for target in (
        "house_fee_monthly",
        "base_rent",
        "utilities_prepayment",
        "heating_prepayment",
        "deposit_amount",
    ):
        v = _cell(row, mapping, target)
        if v:
            r = n.parse_amount(v)
            put(target, r.value, r.confidence, r.notes, col(target))
            if not r.ok:
                reasons.append("amount_unparseable")
    share = _cell(row, mapping, "co_ownership_share")
    if share:
        r = n.parse_fraction(share)
        put("co_ownership_share", r.value[0], r.confidence, r.notes, col("co_ownership_share"))
        base = _cell(row, mapping, "co_ownership_share_base")
        put(
            "co_ownership_share_base",
            int(n.parse_amount(base).value) if base and n.parse_amount(base).value else r.value[1],
            r.confidence,
        )
    sepa = _cell(row, mapping, "sepa_mandate_present")
    if sepa:
        r = n.parse_bool(sepa)
        put("sepa_mandate_present", r.value, r.confidence, r.notes, col("sepa_mandate_present"))
    iban = _cell(row, mapping, "iban_raw")
    if iban and not iban.startswith("[IBAN"):
        r = n.parse_iban(iban, cfg["hmac_key"])
        if r.value:
            put("iban_last4", r.value["iban_last4"], r.confidence, None, col("iban_raw"))
            put("iban_hash", r.value["iban_hash"], r.confidence)
        else:
            reasons.append("iban_invalid")
    elif iban:
        # raw_data ist maskiert; letzte vier Stellen und HMAC stammen aus der Maskierung beim Einlesen (_masked)
        hit = next(
            (
                h
                for h in ((row.parsed_fields or {}).get("_masked") or [])
                if h.get("column") == col("iban_raw")
            ),
            None,
        )
        if hit and hit.get("mod97_valid", True):
            put("iban_last4", hit["iban_last4"], 0.95, None, col("iban_raw"))
            put("iban_hash", hit["iban_hash"], 0.95)
        elif hit:
            put("iban_last4", hit["iban_last4"], 0.6, ["iban_invalid"], col("iban_raw"))
            reasons.append("iban_invalid")
        else:
            put("iban_last4", iban[-5:-1], 0.6, ["from_masked_raw"], col("iban_raw"))
    dep = _cell(row, mapping, "deposit_type")
    if dep:
        r = n.deposit_type_from(dep, cfg["deposit_synonyms"])
        put("deposit_type", r.value, r.confidence, r.notes, col("deposit_type"))
    adj = _cell(row, mapping, "rent_adjustment_type")
    if adj:
        r = n.rent_adjustment_from(adj)
        put("rent_adjustment_type", r.value, r.confidence, r.notes, col("rent_adjustment_type"))
    persons_count = _cell(row, mapping, "persons_count")
    if persons_count:
        r = n.parse_amount(persons_count)
        put(
            "persons_count",
            int(r.value) if r.value is not None else None,
            r.confidence,
            r.notes,
            col("persons_count"),
        )
    return fields, reasons, conf


def _json(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    return value


def normalize_batch(batch: ImportBatch, user=None) -> ImportBatch:
    """Schritte 4 bis 8: Normalisierung, Namens-Splitter, Einheiten, Abgleich, Konfidenz, Review-Faelle."""
    cfg = _config()
    mapping = batch.column_mapping or {}
    if not mapping.get("targets"):
        raise ImportError_("Spaltenzuordnung fehlt")
    obj = batch.object
    owners = _owner_records(obj)
    units = {u.unit_label_normalized: u for u in Unit.active.filter(object=obj)}
    units_by_ref = {u.external_ref: u for u in Unit.active.filter(object=obj) if u.external_ref}
    current_by_unit: dict[int, list[OwnerUnitAssignment]] = {}
    for a in OwnerUnitAssignment.active.filter(unit__object=obj, valid_to__isnull=True).select_related(
        "owner"
    ):
        current_by_unit.setdefault(a.unit_id, []).append(a)
    with transaction.atomic():
        base_rows = list(ImportRow.objects.filter(batch=batch, sub_index=0).order_by("row_no"))
        ImportRow.objects.filter(batch=batch, sub_index__gt=0).delete()
        ReviewCase.objects.filter(import_row__batch=batch, status=CaseStatus.OPEN).update(
            status=CaseStatus.DISMISSED, resolution={"reason": "neu erkannt"}
        )
        for row in base_rows:
            if row.uncertainty_reasons and "not_a_record" in row.uncertainty_reasons:
                continue
            shared, reasons, conf = _normalize_shared(row, mapping, cfg)
            masked = (row.parsed_fields or {}).get("_masked")
            low_cells = (row.parsed_fields or {}).get("_low_confidence_cells") or []
            if low_cells:
                reasons.append("low_ocr_confidence")
            proposal = "accept"
            # Immoware24-Regeln (H 6.2.1): Objektfilter ueber den Zahlenwert, Status steuert die Uebernahme
            onum = (shared.get("object_number") or {}).get("value")
            if onum and onum.strip().isdigit() and not same_object(onum, obj.object_number):
                reasons.append("other_object")
            status_val = ((shared.get("unit_status") or {}).get("value") or "").strip().lower()
            if status_val and status_val not in cfg["active_values"]:
                reasons.append("unit_status_not_active")
            if any(r in REJECT_PROPOSAL_REASONS for r in reasons):
                proposal = "reject"
            unit = None
            norm_label = (shared.get("unit_label_normalized") or {}).get("value")
            ext = (shared.get("external_ref") or {}).get("value")
            if norm_label and norm_label in units:
                unit = units[norm_label]
            elif ext and ext in units_by_ref:
                unit = units_by_ref[ext]
            persons, name_reasons, addition = _persons_from_row(row, mapping, "owner_name_raw", cfg)
            reasons.extend(name_reasons)
            if addition:
                shared["correspondence_addition"] = {
                    "value": addition,
                    "confidence": 0.9,
                    "notes": [],
                    "source_column": None,
                }
            tenants, tenant_reasons, _ = (
                _persons_from_row(row, mapping, "tenant_name_raw", cfg)
                if mapping.get("targets", {}).get("tenant_name_raw")
                else ([], [], None)
            )
            if not persons and not tenants and proposal == "accept":
                reasons.append("name_missing") if "name_missing" not in reasons else None
            sub_rows: list[ImportRow] = []
            for i, person in enumerate(persons):
                target_row = (
                    row
                    if i == 0
                    else ImportRow(batch=batch, row_no=row.row_no, sub_index=i, raw_data=row.raw_data)
                )
                p_reasons = list(reasons)
                match = None
                q = PersonQuery(
                    person.type,
                    person.first_name,
                    person.last_name,
                    person.company_name,
                    (shared.get("email") or {}).get("value"),
                    (shared.get("postal_code") or {}).get("value"),
                    (shared.get("street") or {}).get("value"),
                    (shared.get("iban_hash") or {}).get("value"),
                    unit.pk if unit else None,
                )
                if proposal == "accept":
                    match = match_owner(q, owners, weights=cfg["weights"], thresholds=cfg["thresholds"])
                    if match.decision == "candidates":
                        p_reasons.append("owner_match_ambiguous")
                    matched_id = match.best.owner_id if match.decision == "auto" else None
                    if unit is not None and current_by_unit.get(unit.pk):
                        others = [a for a in current_by_unit[unit.pk] if a.owner_id != matched_id]
                        if others:
                            p_reasons.append("owner_conflict")
                target_row.parsed_fields = {
                    "shared": shared,
                    "_masked": masked,
                    "_low_confidence_cells": low_cells,
                    "person": person.as_dict(),
                    "proposal": proposal,
                    "match": match.as_dict() if match else None,
                    "options": _options(p_reasons),
                    "unit_id": unit.pk if unit else None,
                    "unit_exists": unit is not None,
                    "current_owner_assignment_ids": [a.pk for a in current_by_unit.get(unit.pk, [])]
                    if unit
                    else [],
                }
                target_row.target_entity_type = "owner_unit_assignment" if norm_label else "owner"
                target_row.matched_unit = unit
                target_row.matched_owner_id = match.best.owner_id if match and match.best else None
                target_row.confidence = Decimal(str(round(min(conf, person.confidence), 4)))
                target_row.uncertainty_reasons = sorted(set(p_reasons)) or None
                target_row.status = RowStatus.UNCERTAIN if p_reasons else RowStatus.PARSED
                sub_rows.append(target_row)
            offset = len(persons)
            for j, tenant in enumerate(tenants):
                target_row = (
                    row
                    if (offset == 0 and j == 0)
                    else ImportRow(
                        batch=batch, row_no=row.row_no, sub_index=offset + j, raw_data=row.raw_data
                    )
                )
                t_reasons = list(reasons) + list(tenant_reasons)
                target_row.parsed_fields = {
                    "shared": shared,
                    "_masked": masked,
                    "_low_confidence_cells": low_cells,
                    "person": tenant.as_dict(),
                    "proposal": proposal,
                    "match": None,
                    "options": [],
                    "unit_id": unit.pk if unit else None,
                    "unit_exists": unit is not None,
                    "role": "tenant" if j == 0 else "co_tenant",
                }
                target_row.target_entity_type = "tenant_unit_assignment"
                target_row.matched_unit = unit
                target_row.confidence = Decimal(str(round(min(conf, tenant.confidence), 4)))
                target_row.uncertainty_reasons = sorted(set(t_reasons)) or None
                target_row.status = RowStatus.UNCERTAIN if t_reasons else RowStatus.PARSED
                sub_rows.append(target_row)
            if not sub_rows:
                row.parsed_fields = {
                    "shared": shared,
                    "person": None,
                    "_masked": masked,
                    "_low_confidence_cells": low_cells,
                    "proposal": "reject" if proposal == "reject" else "accept",
                    "match": None,
                    "options": [],
                    "unit_id": unit.pk if unit else None,
                    "unit_exists": unit is not None,
                }
                row.target_entity_type = "unit" if norm_label else None
                row.matched_unit = unit
                row.confidence = Decimal(str(round(conf, 4)))
                row.uncertainty_reasons = sorted(set(reasons)) or None
                row.status = RowStatus.UNCERTAIN if reasons else RowStatus.PARSED
                sub_rows.append(row)
            for r in sub_rows:
                r.review_case = None
                r.save()
                if r.status == RowStatus.UNCERTAIN:
                    r.review_case = ReviewCase.objects.create(
                        object=obj,
                        case_type=CaseType.IMPORT_ROW_UNCERTAIN,
                        case_subtype=(r.uncertainty_reasons or ["unclear"])[0][:32],
                        import_row=r,
                        candidates=(r.parsed_fields.get("match") or {}).get("candidates"),
                        proposed_action={
                            "proposal": r.parsed_fields.get("proposal"),
                            "options": r.parsed_fields.get("options", []),
                        },
                        context={
                            "row_no": r.row_no,
                            "sub_index": r.sub_index,
                            "person": r.parsed_fields.get("person"),
                            "unit": (shared.get("unit_label") or {}).get("value"),
                        },
                        batch_key=f"import:{batch.pk}",
                        priority=80,
                    )
                    r.save(update_fields=["review_case", "updated_at"])
        batch.status = "in_review"
        batch.save(update_fields=["status", "updated_at"])
        refresh_counters(batch)
        record(
            "import.parse",
            entity_type="import_batch",
            entity_id=batch.pk,
            object_id=obj.pk,
            actor=user,
            after={"rows_total": batch.rows_total, "rows_uncertain": batch.rows_uncertain},
        )
    return batch


def _options(reasons: list[str]) -> list[str]:
    options = ["accept", "reject"]
    if "owner_conflict" in reasons:
        options = ["change_owner", "co_owner", "duplicate", "reject"]
    elif "owner_match_ambiguous" in reasons:
        options = ["use_candidate", "new_owner", "reject"]
    elif any(r.startswith("name_split") or r in ("name_tokens_ambiguous",) for r in reasons):
        options = ["accept", "as_community", "reject"]
    return options


# ---------------------------------------------------------------- 9 Uebernahme
@dataclass
class Decision:
    action: str = "accept"  # accept | reject | use_candidate | new_owner | change_owner | co_owner | duplicate | as_community
    owner_id: int | None = None
    overrides: dict = field(default_factory=dict)
    reason: str | None = None


def commit_rows(batch: ImportBatch, decisions: dict[int, Decision], user=None) -> dict[str, int]:
    """Uebernahme je Zeile in einer eigenen Transaktion; Fehler einer Zeile stoppen die anderen nicht (H 6.7)."""
    result = {"committed": 0, "rejected": 0, "failed": 0, "skipped": 0}
    rows = (
        ImportRow.objects.filter(batch=batch, pk__in=list(decisions))
        .select_related("matched_unit", "review_case")
        .order_by("row_no", "sub_index")
    )
    owner_keys: dict[str, int] = {}
    for row in rows:
        if row.status in (RowStatus.COMMITTED, RowStatus.DUPLICATE):
            result["skipped"] += 1
            continue
        decision = decisions[row.pk]
        try:
            with transaction.atomic():
                if decision.action == "reject":
                    _reject_row(row, decision, user)
                    result["rejected"] += 1
                else:
                    _commit_row(batch, row, decision, user, owner_keys)
                    result["committed"] += 1
        except Exception as exc:
            logger.exception("Importzeile %s fehlgeschlagen", row.pk)
            row.notes = f"Fehler bei Übernahme: {type(exc).__name__}: {exc}"[:500]
            row.status = RowStatus.UNCERTAIN
            row.save(update_fields=["notes", "status", "updated_at"])
            result["failed"] += 1
    refresh_counters(batch)
    open_rows = ImportRow.objects.filter(
        batch=batch, status__in=[RowStatus.PARSED, RowStatus.UNCERTAIN, RowStatus.CONFIRMED]
    ).exists()
    batch.status = (
        "partially_committed" if open_rows else ("committed" if batch.rows_committed else "rejected")
    )
    if batch.status == "committed":
        batch.committed_by = user
        batch.committed_at = timezone.now()
    batch.save(update_fields=["status", "committed_by", "committed_at", "updated_at"])
    record(
        "import.commit",
        entity_type="import_batch",
        entity_id=batch.pk,
        object_id=batch.object_id,
        actor=user,
        after=result,
    )
    after_commit_hooks(batch)
    return result


def _reject_row(row: ImportRow, decision: Decision, user) -> None:
    row.status = RowStatus.REJECTED
    row.notes = decision.reason or row.notes or "im Review abgelehnt"
    row.confirmed_by = user
    row.confirmed_at = timezone.now()
    row.save(update_fields=["status", "notes", "confirmed_by", "confirmed_at", "updated_at"])
    _resolve_case(row, user, {"action": "reject"})


def _resolve_case(row: ImportRow, user, resolution: dict) -> None:
    if row.review_case_id:
        ReviewCase.objects.filter(pk=row.review_case_id).update(
            status=CaseStatus.RESOLVED, resolved_by=user, resolved_at=timezone.now(), resolution=resolution
        )


def _val(shared: dict, name: str):
    return (shared.get(name) or {}).get("value")


def _commit_row(
    batch: ImportBatch, row: ImportRow, decision: Decision, user, owner_keys: dict[str, int]
) -> None:
    obj = batch.object
    pf = row.parsed_fields or {}
    shared = dict(pf.get("shared") or {})
    for k, v in (decision.overrides or {}).items():
        shared[k] = {"value": v, "confidence": 1.0, "notes": ["manual"], "source_column": None}
    person = dict(pf.get("person") or {})
    for k in ("first_name", "last_name", "company_name", "type", "salutation"):
        if k in (decision.overrides or {}):
            person[k] = decision.overrides[k]
    if decision.action == "as_community":
        person = {
            "type": "community",
            "company_name": (row.raw_data or {}).get(_owner_header(batch)) or person.get("last_name"),
            "key": "community",
        }
    targets: list[dict] = []
    now = timezone.now()

    def prov(entity_type: str, entity_id: int, field_name: str, confidence=None):
        party_services.set_provenance(
            entity_type,
            entity_id,
            field_name,
            source_kind="import_row",
            status="confirmed",
            user=user,
            confidence=Decimal(str(confidence)) if confidence is not None else None,
            source_import_row=row,
        )

    # Einheit
    unit = row.matched_unit
    label = _val(shared, "unit_label")
    if unit is None and label:
        # Einheit kann durch eine vorherige Zeile desselben Imports entstanden sein (gleiche Einheit, zweite Person)
        norm = _val(shared, "unit_label_normalized") or label.upper().replace(" ", "")
        unit = Unit.active.filter(object=obj, unit_label_normalized=norm).first()
        ext = _val(shared, "external_ref")
        if unit is None and ext:
            unit = Unit.active.filter(object=obj, external_ref=ext).first()
        if unit is not None:
            row.matched_unit = unit
    if unit is None and label:
        unit = Unit.objects.create(
            object=obj,
            unit_label=label,
            unit_label_normalized=_val(shared, "unit_label_normalized") or label.upper().replace(" ", ""),
            unit_number=_val(shared, "unit_number"),
            unit_type=_val(shared, "unit_type") or "other",
            building=_val(shared, "building"),
            location=_val(shared, "location"),
            external_ref=_val(shared, "external_ref"),
            house_fee_monthly=_dec(_val(shared, "house_fee_monthly")),
            co_ownership_share=_dec(_val(shared, "co_ownership_share")),
            co_ownership_share_base=_val(shared, "co_ownership_share_base"),
            data_status="confirmed",
        )
        for f in (
            "unit_label",
            "unit_type",
            "building",
            "location",
            "external_ref",
            "house_fee_monthly",
            "co_ownership_share",
        ):
            if getattr(unit, f) not in (None, ""):
                prov("unit", unit.pk, f, (shared.get(f) or {}).get("confidence"))
        targets.append({"type": "unit", "id": unit.pk, "created": True})
        record(
            "unit.create",
            entity_type="unit",
            entity_id=unit.pk,
            object_id=obj.pk,
            actor=user,
            after={"unit_label": unit.unit_label, "import_row_id": row.pk},
        )
    elif unit is not None:
        changed = _fill_empty(
            unit,
            {
                "building": _val(shared, "building"),
                "location": _val(shared, "location"),
                "external_ref": _val(shared, "external_ref"),
                "house_fee_monthly": _dec(_val(shared, "house_fee_monthly")),
                "co_ownership_share": _dec(_val(shared, "co_ownership_share")),
                "co_ownership_share_base": _val(shared, "co_ownership_share_base"),
            },
        )
        for f in changed:
            prov("unit", unit.pk, f, (shared.get(f) or {}).get("confidence"))
        if changed:
            record(
                "unit.update",
                entity_type="unit",
                entity_id=unit.pk,
                object_id=obj.pk,
                actor=user,
                after={f: _json(getattr(unit, f)) for f in changed},
            )
        targets.append({"type": "unit", "id": unit.pk, "created": False, "filled": changed})

    entity_type = row.target_entity_type or ""
    if entity_type == "tenant_unit_assignment" and person:
        _commit_tenant(batch, row, unit, shared, person, user, targets, prov)
    elif person and person.get("type"):
        owner = _resolve_owner(row, decision, person, shared, user, owner_keys, prov, targets)
        if unit is not None and owner is not None:
            _commit_assignment(row, decision, unit, owner, shared, user, prov, targets)
    row.status = RowStatus.COMMITTED
    row.committed_at = now
    row.confirmed_by = user
    row.confirmed_at = now
    row.committed_targets = targets
    row.committed_unit = unit
    row.save(
        update_fields=[
            "status",
            "committed_at",
            "confirmed_by",
            "confirmed_at",
            "committed_targets",
            "committed_unit",
            "committed_owner",
            "committed_assignment",
            "committed_tenant",
            "committed_lease",
            "updated_at",
        ]
    )
    _resolve_case(row, user, {"action": decision.action, "targets": targets})
    record(
        "import.row_commit",
        entity_type="import_row",
        entity_id=row.pk,
        object_id=obj.pk,
        actor=user,
        after={"action": decision.action, "targets": targets},
    )


def _owner_header(batch: ImportBatch) -> str | None:
    m = batch.column_mapping or {}
    idx = m.get("targets", {}).get("owner_name_raw")
    return m["header"][idx] if idx is not None else None


def _dec(value):
    if value in (None, ""):
        return None
    return Decimal(str(value))


def _fill_empty(instance, values: dict) -> list[str]:
    changed = []
    for f, v in values.items():
        if v not in (None, "") and getattr(instance, f) in (None, ""):
            setattr(instance, f, v)
            changed.append(f)
    if changed:
        instance.save(update_fields=[*changed, "updated_at"])
    return changed


def _resolve_owner(
    row, decision: Decision, person: dict, shared: dict, user, owner_keys: dict, prov, targets
) -> Owner | None:
    match = (row.parsed_fields or {}).get("match") or {}
    owner: Owner | None = None
    if decision.action in ("use_candidate", "duplicate") and decision.owner_id:
        owner = Owner.active.get(pk=decision.owner_id)
    elif (
        decision.action in ("accept", "change_owner", "co_owner")
        and match.get("decision") == "auto"
        and not decision.overrides
    ):
        owner = Owner.active.filter(pk=match["best"]["owner_id"]).first()
    elif decision.action in ("accept", "change_owner", "co_owner", "as_community") and decision.owner_id:
        owner = Owner.active.filter(pk=decision.owner_id).first()
    key = person.get("key") or ""
    if owner is None and key and key in owner_keys:
        owner = Owner.active.filter(pk=owner_keys[key]).first()
    address = {
        "correspondence_street": _val(shared, "street"),
        "correspondence_house_number": _val(shared, "house_number"),
        "correspondence_postal_code": _val(shared, "postal_code"),
        "correspondence_city": _val(shared, "city"),
        "correspondence_country": _val(shared, "country"),
        "correspondence_addition": _val(shared, "correspondence_addition"),
        "email": _val(shared, "email"),
        "phone": _val(shared, "phone"),
        "mobile": _val(shared, "mobile"),
        "sepa_mandate_present": _val(shared, "sepa_mandate_present"),
        "sepa_mandate_reference": _val(shared, "mandate_reference"),
        "iban_last4": _val(shared, "iban_last4"),
        "notes": _val(shared, "notes"),
    }
    iban_hash_hex = _val(shared, "iban_hash")
    if owner is None:
        owner = Owner.objects.create(
            type=person.get("type") or "natural_person",
            first_name=person.get("first_name"),
            last_name=person.get("last_name"),
            company_name=person.get("company_name"),
            salutation=person.get("salutation"),
            search_name=party_services.search_name(
                type=person.get("type") or "natural_person",
                first_name=person.get("first_name"),
                last_name=person.get("last_name"),
                company_name=person.get("company_name"),
            ),
            iban_hash=bytes.fromhex(iban_hash_hex) if iban_hash_hex else None,
            data_status="confirmed",
            **{k: v for k, v in address.items() if v not in (None, "")},
        )
        for f in ("last_name", "first_name", "company_name", *address):
            if getattr(owner, f, None) not in (None, ""):
                prov(
                    "owner",
                    owner.pk,
                    f if f not in ("last_name", "company_name") else "last_name_or_company",
                    (shared.get(f.replace("correspondence_", "")) or {}).get("confidence"),
                )
        targets.append({"type": "owner", "id": owner.pk, "created": True})
        record(
            "owner.create",
            entity_type="owner",
            entity_id=owner.pk,
            object_id=row.batch.object_id,
            actor=user,
            after={"display": str(owner), "import_row_id": row.pk},
        )
    else:
        filled = _fill_empty(owner, address)
        if iban_hash_hex and owner.iban_hash is None:
            owner.iban_hash = bytes.fromhex(iban_hash_hex)
            owner.save(update_fields=["iban_hash", "updated_at"])
            filled.append("iban_hash")
        for f in filled:
            if f != "iban_hash":
                prov(
                    "owner",
                    owner.pk,
                    f,
                    (shared.get(f.replace("correspondence_", "")) or {}).get("confidence"),
                )
        if filled:
            record(
                "owner.update",
                entity_type="owner",
                entity_id=owner.pk,
                object_id=row.batch.object_id,
                actor=user,
                after={"filled": [f for f in filled if f != "iban_hash"], "import_row_id": row.pk},
            )
        targets.append({"type": "owner", "id": owner.pk, "created": False, "filled": filled})
    if key:
        owner_keys[key] = owner.pk
    row.committed_owner = owner
    return owner


def _commit_assignment(
    row, decision: Decision, unit: Unit, owner: Owner, shared: dict, user, prov, targets
) -> None:
    valid_from = _date(_val(shared, "valid_from"))
    valid_to = _date(_val(shared, "valid_to"))
    existing_same = OwnerUnitAssignment.active.filter(unit=unit, owner=owner).order_by("-valid_from").first()
    if existing_same and party_services.periods_overlap(
        valid_from, valid_to, existing_same.valid_from, existing_same.valid_to
    ):
        targets.append(
            {
                "type": "owner_unit_assignment",
                "id": existing_same.pk,
                "created": False,
                "note": "bestehende Zuordnung",
            }
        )
        row.committed_assignment = existing_same
        return
    current_others = list(
        OwnerUnitAssignment.active.filter(unit=unit, valid_to__isnull=True)
        .exclude(owner=owner)
        .select_related("source_import_row")
    )
    same_list = bool(current_others) and all(
        a.source_import_row_id and a.source_import_row.batch_id == row.batch_id for a in current_others
    )
    if same_list and decision.action in ("accept", "use_candidate", "new_owner"):
        # Mehrfacheigentum innerhalb derselben Liste (z. B. "Mustermann, Erika & Max"): kein Konflikt (H 6.7)
        current_others = []
    if current_others:
        if decision.action == "change_owner":
            end = (valid_from - timedelta(days=1)) if valid_from else None
            if end is None:
                raise ImportError_("Eigentümerwechsel ohne Eigentumsbeginn: Datum in der Zeile ergänzen")
            for a in current_others:
                party_services.end_assignment(a, end, user=user)
                record(
                    "assignment.end",
                    entity_type="owner_unit_assignment",
                    entity_id=a.pk,
                    object_id=unit.object_id,
                    actor=user,
                    reason="Eigentümerwechsel aus Import",
                    after={"valid_to": end.isoformat()},
                )
                targets.append({"type": "owner_unit_assignment", "id": a.pk, "ended": end.isoformat()})
        elif decision.action == "duplicate":
            targets.append(
                {
                    "type": "owner_unit_assignment",
                    "id": current_others[0].pk,
                    "created": False,
                    "note": "Dublette, keine neue Zuordnung",
                }
            )
            row.committed_assignment = current_others[0]
            return
        elif decision.action != "co_owner":
            raise ImportError_(
                "Einheit hat bereits einen aktuellen Eigentümer: Entscheidung Wechsel, Mehrfacheigentum oder Dublette nötig"
            )
    share = (
        _dec(_val(shared, "co_ownership_share_owner")) if _val(shared, "co_ownership_share_owner") else None
    )
    res = party_services.create_assignment(
        owner=owner,
        unit=unit,
        valid_from=valid_from,
        valid_to=valid_to,
        share=share,
        user=user,
        confirmed=True,
        source_import_row=row,
    )
    a = res.assignment
    if valid_from:
        prov("owner_unit_assignment", a.pk, "valid_from", (shared.get("valid_from") or {}).get("confidence"))
    record(
        "assignment.create",
        entity_type="owner_unit_assignment",
        entity_id=a.pk,
        object_id=unit.object_id,
        actor=user,
        after={
            "owner_id": owner.pk,
            "unit_id": unit.pk,
            "valid_from": _json(valid_from),
            "valid_to": _json(valid_to),
            "import_row_id": row.pk,
        },
    )
    targets.append({"type": "owner_unit_assignment", "id": a.pk, "created": True})
    row.committed_assignment = a


def _commit_tenant(batch, row, unit, shared, person, user, targets, prov) -> None:
    tenant = Tenant.objects.create(
        type=person.get("type")
        if person.get("type") in ("natural_person", "legal_entity")
        else "natural_person",
        first_name=person.get("first_name"),
        last_name=person.get("last_name"),
        company_name=person.get("company_name"),
        search_name=party_services.search_name(
            type=person.get("type") or "natural_person",
            first_name=person.get("first_name"),
            last_name=person.get("last_name"),
            company_name=person.get("company_name"),
        ),
        data_status="confirmed",
    )
    prov("tenant", tenant.pk, "last_name_or_company")
    targets.append({"type": "tenant", "id": tenant.pk, "created": True})
    row.committed_tenant = tenant
    lease = None
    rent = _val(shared, "base_rent")
    if unit is not None:
        # H 6.9: ein Mietverhaeltnis je Quellzeile; Mitmieter derselben Zeile teilen es
        lease = Lease.objects.filter(
            source_import_row__batch=batch,
            source_import_row__row_no=row.row_no,
            deleted_at__isnull=True,
        ).first()
        if lease is None:
            immoware = batch.parser_profile == "immoware24_export"
            notes = (
                f"Miete lt. Export: {rent} EUR (Bedeutung Kaltmiete oder Gesamtmiete offen, F11)"
                if rent and immoware
                else None
            )
            lease = Lease.objects.create(
                object=batch.object,
                start_date=_date(_val(shared, "start_date")),
                end_date=_date(_val(shared, "end_date")),
                base_rent=None if immoware else _dec(rent),
                utilities_prepayment=_dec(_val(shared, "utilities_prepayment")),
                heating_prepayment=_dec(_val(shared, "heating_prepayment")),
                deposit_amount=_dec(_val(shared, "deposit_amount")),
                deposit_type=_val(shared, "deposit_type") or "unknown",
                rent_adjustment_type=_val(shared, "rent_adjustment_type") or "unknown",
                persons_count=_val(shared, "persons_count"),
                notes=notes,
                data_status="confirmed",
                source_import_row=row,
            )
            targets.append({"type": "lease", "id": lease.pk, "created": True})
        else:
            targets.append({"type": "lease", "id": lease.pk, "created": False})
        row.committed_lease = lease
        a = TenantUnitAssignment.objects.create(
            tenant=tenant,
            unit=unit,
            lease=lease,
            role=(row.parsed_fields or {}).get("role", "tenant"),
            valid_from=_date(_val(shared, "start_date")),
            valid_to=_date(_val(shared, "end_date")),
            data_status="confirmed",
            confirmed_by=user,
            confirmed_at=timezone.now(),
            source_import_row=row,
        )
        targets.append({"type": "tenant_unit_assignment", "id": a.pk, "created": True})


def _date(value):
    if not value:
        return None
    return date.fromisoformat(value) if isinstance(value, str) else value


def refresh_counters(batch: ImportBatch) -> ImportBatch:
    counts = {
        r["status"]: r["c"]
        for r in ImportRow.objects.filter(batch=batch).values("status").annotate(c=Count("id"))
    }
    batch.rows_total = sum(counts.values())
    batch.rows_uncertain = counts.get(RowStatus.UNCERTAIN, 0)
    batch.rows_confirmed = counts.get(RowStatus.CONFIRMED, 0)
    batch.rows_rejected = counts.get(RowStatus.REJECTED, 0)
    batch.rows_committed = counts.get(RowStatus.COMMITTED, 0)
    batch.save(
        update_fields=[
            "rows_total",
            "rows_uncertain",
            "rows_confirmed",
            "rows_rejected",
            "rows_committed",
            "updated_at",
        ]
    )
    assert batch.rows_total == sum(counts.values())
    return batch


def status_sum_matches(batch: ImportBatch) -> bool:
    counts = {
        r["status"]: r["c"]
        for r in ImportRow.objects.filter(batch=batch).values("status").annotate(c=Count("id"))
    }
    return batch.rows_total == sum(counts.values())


def after_commit_hooks(batch: ImportBatch) -> None:
    """Nach Uebernahme: Vollstaendigkeit des Objekts neu bewerten (H 6.7, H 3.5); Listen folgen in M11."""
    from apps.lists.services import request_generation
    from apps.requirements.tasks import trigger_evaluation

    logger.info("Import %s uebernommen; Vollstaendigkeit und Listen werden neu erzeugt", batch.pk)
    trigger_evaluation(batch.object_id, "import_commit")
    request_generation(batch.object_id, "import_commit")
