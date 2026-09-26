"""Datensaetze der CRM-Schnittstelle (Schnittstellenvertrag M29 Stufe 3, Endpunkte 1 bis 5 und Webhook).

Alle Kennzahlen kommen aus den vorhandenen Bausteinen: Vollstaendigkeit aus apps.requirements.engine (summary und
open_items), Stichtagslogik der Zuordnungen aus apps.parties.services, IBAN-Maske aus PartyBase.iban_masked.
Personendaten erscheinen nur maskiert (E-Mail, IBAN); Anschriften und Telefonnummern von Personen gar nicht.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from django.db.models import Count, Prefetch, Q
from django.utils import timezone

from apps.documents.models import Document, DocumentType
from apps.drive.models import DriveNode, NodeKind, NodeStatus
from apps.objects.models import ManagedObject, ObjectStatus, Unit
from apps.parties.models import OwnerUnitAssignment, TenantRole, TenantUnitAssignment
from apps.parties.services import _period_filter
from apps.requirements import engine
from apps.requirements.models import CompletenessCheck, FindingStatus
from apps.review.models import CaseStatus, ReviewCase
from apps.sync.models import ExternalLink, LinkRole, SyncSystem
from apps.sync.services import drive_link

OPEN_CASE_STATUSES = (CaseStatus.OPEN, CaseStatus.IN_PROGRESS)
TENANT_ROLES = (TenantRole.TENANT, TenantRole.CO_TENANT)  # Buergen sind keine Mieter


# ---------------------------------------------------------------- Formate
def iso(dt: datetime | None) -> str | None:
    """ISO 8601 in UTC (Vertrag: Datumswerte ISO 8601, UTC)."""
    if dt is None:
        return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, UTC)
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def iso_date(d: date | None) -> str | None:
    return d.isoformat() if d else None


def drive_folder_url(folder_id: str | None) -> str | None:
    return f"https://drive.google.com/drive/folders/{folder_id}" if folder_id else None


def mask_email(email: str | None) -> str | None:
    """Erstes Zeichen von Name und Domain, Endung lesbar: erika@beispiel.de wird e***@b***.de."""
    if not email or "@" not in email:
        return None
    local, _, domain = email.strip().rpartition("@")
    if not local or not domain:
        return None
    host, dot, tld = domain.rpartition(".")
    if not dot:
        host, tld = domain, ""
    masked_domain = f"{host[:1]}***" + (f".{tld}" if tld else "")
    return f"{local[:1]}***@{masked_domain}"


def iban_masked(party) -> str | None:
    """Maske aus iban_last4 (PartyBase.iban_masked); ein Klartext ist nicht gespeichert (B-18)."""
    return party.iban_masked or None


# ---------------------------------------------------------------- Objekte
def _visible_objects():
    return ManagedObject.objects.filter(is_system_inbox=False, is_test=False)


def list_objects() -> list[ManagedObject]:
    """Aktive und archivierte Objekte, je Objektnummer genau eines: das aktive, sonst das zuletzt archivierte."""
    rows = sorted(
        _visible_objects(),
        key=lambda o: (
            o.object_number_numeric,
            o.deleted_at is not None,
            -(o.deleted_at.timestamp() if o.deleted_at else 0),
        ),
    )
    seen: set[int] = set()
    result = []
    for o in rows:
        if o.object_number_numeric in seen:
            continue
        seen.add(o.object_number_numeric)
        result.append(o)
    return result


def find_object(number: str) -> ManagedObject | None:
    if not number.isdigit() or len(number) > 8:
        return None
    qs = _visible_objects().filter(object_number_numeric=int(number))
    return qs.filter(deleted_at__isnull=True).first() or qs.order_by("-deleted_at").first()


def _root_folders(objects: list[ManagedObject]) -> dict[int, str]:
    """Objektordner aus drive_nodes (persistenter Cache der Folder-IDs), ersatzweise drive_root_folder_id."""
    rows = DriveNode.objects.filter(
        object__in=objects, node_kind=NodeKind.OBJECT_ROOT, status=NodeStatus.ACTIVE
    ).values_list("object_id", "drive_file_id")
    return dict(rows)


def _open_case_counts(objects: list[ManagedObject]) -> dict[int, int]:
    rows = (
        ReviewCase.objects.filter(object__in=objects, status__in=OPEN_CASE_STATUSES)
        .values("object_id")
        .annotate(c=Count("id"))
    )
    return {r["object_id"]: r["c"] for r in rows}


def completeness(obj: ManagedObject) -> dict:
    """Aus engine.summary: required sind die anwendbaren Pruefpunkte (erfuellt, teilweise, fehlt), present die
    erfuellten, missing die uebrigen; Folgepunkte und der Sollbestand der Stammakte zaehlen wie dort nicht mit."""
    s = engine.summary(obj)
    counts = s.get("counts") or {}
    present = int(counts.get(FindingStatus.FULFILLED, 0))
    partial = int(counts.get(FindingStatus.PARTIAL, 0))
    missing = int(counts.get(FindingStatus.MISSING, 0)) + partial
    required = present + missing
    ratio = s.get("ratio")
    return {
        "required": required,
        "present": present,
        "missing": missing,
        "percent": round(float(ratio) * 100, 1) if ratio is not None else 0.0,
    }


def object_summaries(objects: list[ManagedObject]) -> list[dict]:
    roots = _root_folders(objects)
    cases = _open_case_counts(objects)
    return [_object_row(o, roots.get(o.pk), cases.get(o.pk, 0)) for o in objects]


def _object_row(obj: ManagedObject, root_id: str | None, open_cases: int) -> dict:
    folder_id = root_id or obj.drive_root_folder_id or None
    return {
        "number": obj.object_number,
        "name": obj.name,
        "archived": obj.deleted_at is not None or obj.status == ObjectStatus.ARCHIVED,
        "takeover_status": obj.status,
        "open_review_cases": open_cases,
        "completeness": completeness(obj),
        "drive_folder_id": folder_id,
        "drive_folder_url": drive_folder_url(folder_id),
        "updated_at": iso(obj.updated_at),
    }


def _type_folders(checks: dict[str, CompletenessCheck]) -> dict[str, tuple[str | None, str | None]]:
    """Dokumentart zu Hauptordner und Unterordner (Ordnernamen wie in Drive)."""
    codes = {c for chk in checks.values() for c in (chk.evidence_document_types or [])}
    types = DocumentType.objects.filter(code__in=codes).select_related("category", "subfolder")
    return {
        t.code: (
            t.category.folder_name if t.category_id else None,
            t.subfolder.folder_name if t.subfolder_id else None,
        )
        for t in types
    }


def missing_documents(obj: ManagedObject) -> list[dict]:
    """Offene Punkte der Vollstaendigkeitspruefung (engine.open_items: fehlt und teilweise, wie Blatt Offene Punkte
    der Listen). Ordner aus der ersten Nachweis-Dokumentart des Pruefpunkts, sonst die Gruppe des Pruefpunkts."""
    checks = {c.code: c for c in CompletenessCheck.objects.all()}
    folders = _type_folders(checks)
    rows = []
    for item in engine.open_items(obj):
        check = checks.get(item["check_code"])
        evidence = (check.evidence_document_types or []) if check else []
        category, subfolder = folders.get(evidence[0], (None, None)) if evidence else (None, None)
        label = item["check_name"]
        if item["unit_label"]:
            label += f", {item['unit_label']}"
        if item["period_year"]:
            label += f", {item['period_year']}"
        rows.append({"category": category or item["category"], "subfolder": subfolder, "label": label})
    return rows


def open_cases_by_type(obj: ManagedObject) -> dict[str, int]:
    """Schluessel case_type/case_subtype; ohne Unterart bleibt der Teil nach dem Schraegstrich leer."""
    rows = (
        ReviewCase.objects.filter(object=obj, status__in=OPEN_CASE_STATUSES)
        .values("case_type", "case_subtype")
        .annotate(c=Count("id"))
        .order_by("case_type", "case_subtype")
    )
    return {f"{r['case_type']}/{r['case_subtype'] or ''}": r["c"] for r in rows}


def object_detail(obj: ManagedObject) -> dict:
    row = object_summaries([obj])[0]
    row.update(
        {
            "address": {
                "street": obj.street or None,
                "house_number": obj.house_number or None,
                "zip": obj.postal_code or None,
                "city": obj.city or None,
            },
            "missing_documents": missing_documents(obj),
            "open_cases_by_type": open_cases_by_type(obj),
        }
    )
    return row


# ---------------------------------------------------------------- Dokumente
def documents_queryset(obj: ManagedObject):
    """Dokumente des Objekts mit Hash; ohne geloeschte und in ein anderes Objekt uebernommene Zeilen. Dubletten
    tragen keinen eigenen Hash und erscheinen deshalb nicht (sha256 ist im Vertrag Pflicht)."""
    return (
        Document.objects.filter(object=obj, deleted_at__isnull=True, sha256__isnull=False)
        .exclude(sha256="")
        .exclude(status="moved_out")
        .select_related("category", "subfolder", "document_type", "crm_upload")
        .prefetch_related(_paperless_prefetch())
        .order_by("pk")
    )


def _paperless_prefetch() -> Prefetch:
    return Prefetch(
        "sync_links",
        queryset=ExternalLink.objects.filter(system=SyncSystem.PAPERLESS, role=LinkRole.ORIGINAL),
        to_attr="paperless_links",
    )


def filter_documents(qs, *, folder: str | None, since: datetime | None):
    if folder:
        qs = qs.filter(Q(category_id=folder) | Q(category__folder_name=folder))
    if since is not None:
        qs = qs.filter(updated_at__gte=since)
    return qs


def document_row(doc: Document) -> dict:
    return {
        "id": doc.pk,
        "title": doc.current_name,
        "doc_type": doc.document_type.code if doc.document_type_id else None,
        "category": doc.category.folder_name if doc.category_id else None,
        "subfolder": doc.subfolder.folder_name if doc.subfolder_id else None,
        "status": doc.status,
        "drive_file_id": doc.drive_file_id or None,
        "drive_url": drive_link(doc.drive_file_id),
        "sha256": doc.sha256,
        "filed_at": iso(doc.filed_at),
        "mime_type": doc.mime_type or None,
        "size_bytes": doc.size_bytes,
        # Ergaenzung 26.09.2026 (Upload aus dem CRM): Kennung des CRM-Dokuments und Dokument-ID in Paperless
        "crm_document_id": _crm_document_id(doc),
        "paperless_id": _paperless_id(doc),
    }


def _crm_document_id(doc: Document) -> str | None:
    try:
        return doc.crm_upload.crm_document_id
    except Document.crm_upload.RelatedObjectDoesNotExist:
        return None


def _paperless_id(doc: Document) -> int | None:
    links = getattr(doc, "paperless_links", None)
    if links is None:
        links = list(
            ExternalLink.objects.filter(document=doc, system=SyncSystem.PAPERLESS, role=LinkRole.ORIGINAL)
        )
    for link in links:
        if link.external_id and link.external_id.isdigit():
            return int(link.external_id)
    return None


def find_document(pk: int) -> Document | None:
    """Dokument fuer die Statusabfrage; nur Dokumente sichtbarer Objekte, geloeschte eingeschlossen."""
    return (
        Document.objects.filter(pk=pk, object__is_system_inbox=False, object__is_test=False)
        .select_related("object", "category", "subfolder", "document_type", "crm_upload", "duplicate_of")
        .prefetch_related(_paperless_prefetch())
        .first()
    )


def document_status(doc: Document) -> dict:
    """Stand eines Dokuments fuer das CRM (Statusabfrage nach dem Upload): Zeile wie in der Dokumentliste plus
    Objekt, offene Pruefungsfaelle, Loeschung und bei einer Dublette das Original mit seiner Ablage."""
    open_cases = ReviewCase.objects.filter(document=doc, status__in=OPEN_CASE_STATUSES).count()
    row = document_row(doc)
    row.update(
        {
            "object_number": doc.object.object_number,
            "open_review_cases": open_cases,
            "deleted": doc.deleted_at is not None,
            "duplicate_of": document_row(doc.duplicate_of) if doc.duplicate_of_id else None,
        }
    )
    return row


# ---------------------------------------------------------------- Personen
def _units(obj: ManagedObject):
    return Unit.active.filter(object=obj, status="active")


def owners(obj: ManagedObject, *, today: date | None = None) -> list[dict]:
    """Aktuelle Eigentuemer zum Stichtag heute, eine Zeile je Eigentuemer mit seinen Einheiten. share ist der
    Anteil an der Einheit (Zuordnung), wenn er fuer alle Einheiten des Eigentuemers gleich ist, sonst null."""
    today = today or timezone.localdate()
    assignments = (
        OwnerUnitAssignment.active.filter(unit__in=_units(obj), owner__deleted_at__isnull=True)
        .filter(_period_filter("", today, today))
        .select_related("owner", "unit")
        .order_by("owner__search_name", "owner_id", "unit__unit_label_normalized")
    )
    grouped: dict[int, dict] = {}
    for a in assignments:
        entry = grouped.setdefault(a.owner_id, {"owner": a.owner, "units": [], "shares": set()})
        if a.unit.unit_label not in entry["units"]:
            entry["units"].append(a.unit.unit_label)
        entry["shares"].add(a.share)
    rows = []
    for owner_id, entry in grouped.items():
        o = entry["owner"]
        shares = entry["shares"]
        share = next(iter(shares)) if len(shares) == 1 else None
        rows.append(
            {
                "id": owner_id,
                "display_name": o.display_name,
                "unit_labels": entry["units"],
                "share": f"{share.normalize():f}" if share is not None else None,
                "email_masked": mask_email(o.email),
                "iban_masked": iban_masked(o),
            }
        )
    return rows


def tenants(obj: ManagedObject, *, today: date | None = None) -> list[dict]:
    """Aktuelle Mieter und Mitmieter zum Stichtag heute, eine Zeile je Mieter. Bei WEG mit
    Sondereigentumsverwaltung nur Einheiten in eigener Verwaltung (wie die Mieterliste). Mietbeginn und Mietende
    aus dem Mietverhaeltnis, ersatzweise aus der Zuordnung."""
    today = today or timezone.localdate()
    units = _units(obj)
    if obj.management_type == "weg_with_se":
        units = units.filter(se_managed=True)
    assignments = (
        TenantUnitAssignment.active.filter(
            unit__in=units, tenant__deleted_at__isnull=True, role__in=TENANT_ROLES
        )
        .filter(_period_filter("", today, today))
        .select_related("tenant", "unit", "lease")
        .order_by("tenant__search_name", "tenant_id", "unit__unit_label_normalized")
    )
    grouped: dict[int, dict] = {}
    for a in assignments:
        entry = grouped.setdefault(a.tenant_id, {"tenant": a.tenant, "units": [], "starts": [], "ends": []})
        if a.unit.unit_label not in entry["units"]:
            entry["units"].append(a.unit.unit_label)
        lease = a.lease
        entry["starts"].append((lease.start_date if lease else None) or a.valid_from)
        entry["ends"].append((lease.end_date if lease else None) or a.valid_to)
    rows = []
    for tenant_id, entry in grouped.items():
        t = entry["tenant"]
        starts = [d for d in entry["starts"] if d]
        ends = entry["ends"]
        rows.append(
            {
                "id": tenant_id,
                "display_name": t.display_name,
                "unit_labels": entry["units"],
                "lease_start": iso_date(min(starts)) if starts else None,
                "lease_end": iso_date(max(ends)) if ends and all(ends) else None,
                "email_masked": mask_email(t.email),
            }
        )
    return rows
