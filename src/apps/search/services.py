"""Suche nach CR 13 und Fachentwurf H 7.1, 7.2: relationale Filter zuerst (Eigentuemer, Mieter, Einheit, Objekt,
Zeitraum, Dokumentunterart, Hauptordner, Status, Verwaltungsart), Volltext auf document_pages.text_content im booleschen
Modus mit ODER-verknuepften Umlautvarianten, Ergebnisse je Dokument aggregiert (bester Seitentreffer), Rechteprüfung
serverseitig (Eigentuemer- und Mieterakten nur mit owner_files.read beziehungsweise tenant_files.read). Die Schicht
ist hinter SearchIndex.query gekapselt; Suchanfragen mit Personenbezug erscheinen nicht im Log (nur Anzahl und Dauer).
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import date

from django.core.cache import cache
from django.db import connection
from django.db.models import Q

from apps.accounts.permissions import user_has_permission
from apps.documents.models import Document, DocumentOwnerLink
from apps.objects.models import ManagedObject, Unit
from apps.objects.units import normalize_label
from apps.parties.models import Owner, OwnerUnitAssignment, Tenant, TenantUnitAssignment
from apps.review.models import CaseStatus, ReviewCase

logger = logging.getLogger(__name__)
WORD = re.compile(r"[\wäöüÄÖÜß]+", re.UNICODE)
UMLAUTS = (("ae", "ä"), ("oe", "ö"), ("ue", "ü"), ("ss", "ß"))
CONFIRMED_DOC_STATUSES = ("filed", "classified")


@dataclass
class Filters:
    text: str = ""
    owner: str = ""
    tenant: str = ""
    unit: str = ""
    object_number: str = ""
    year: int | None = None
    date_from: date | None = None
    date_to: date | None = None
    document_type: str = ""
    category: str = ""
    status: str = ""  # final | review | ""
    management_type: str = ""

    @classmethod
    def from_params(cls, params) -> Filters:
        def d(key):
            raw = (params.get(key) or "").strip()
            try:
                return date.fromisoformat(raw) if raw else None
            except ValueError:
                return None

        year = (params.get("jahr") or "").strip()
        return cls(
            text=(params.get("text") or "").strip(),
            owner=(params.get("eigentuemer") or "").strip(),
            tenant=(params.get("mieter") or "").strip(),
            unit=(params.get("einheit") or "").strip(),
            object_number=(params.get("objekt") or "").strip(),
            year=int(year) if year.isdigit() else None,
            date_from=d("von"),
            date_to=d("bis"),
            document_type=(params.get("unterart") or "").strip(),
            category=(params.get("kategorie") or "").strip(),
            status=(params.get("status") or "").strip(),
            management_type=(params.get("verwaltungsart") or "").strip(),
        )

    @property
    def is_empty(self) -> bool:
        return not any(
            [
                self.text,
                self.owner,
                self.tenant,
                self.unit,
                self.object_number,
                self.year,
                self.date_from,
                self.date_to,
                self.document_type,
                self.category,
                self.status,
                self.management_type,
            ]
        )

    @property
    def has_relational_filter(self) -> bool:
        return any(
            [
                self.owner,
                self.tenant,
                self.unit,
                self.object_number,
                self.year,
                self.date_from,
                self.date_to,
                self.document_type,
                self.category,
                self.status,
                self.management_type,
            ]
        )


@dataclass
class Hit:
    kind: str
    title: str
    subtitle: str = ""
    url: str = ""
    object_number: str = ""
    page_no: int | None = None
    snippet: str = ""
    extra: dict = field(default_factory=dict)


@dataclass
class SearchResult:
    documents: list[Hit] = field(default_factory=list)
    owners: list[Hit] = field(default_factory=list)
    tenants: list[Hit] = field(default_factory=list)
    units: list[Hit] = field(default_factory=list)
    objects: list[Hit] = field(default_factory=list)
    short_words: list[str] = field(default_factory=list)
    min_token_len: int = 3
    elapsed_ms: int = 0
    truncated: bool = False

    @property
    def total(self) -> int:
        return (
            len(self.documents) + len(self.owners) + len(self.tenants) + len(self.units) + len(self.objects)
        )


# ---------------------------------------------------------------- Volltext
def min_token_size() -> int:
    """Serverparameter innodb_ft_min_token_size; wird in der Oberflaeche angezeigt (H 7.2 Umsetzungshinweise)."""
    cached = cache.get("search:ft_min_token")
    if cached:
        return int(cached)
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT @@innodb_ft_min_token_size")
            value = int(cur.fetchone()[0])
    except Exception:
        value = 3
    cache.set("search:ft_min_token", value, timeout=3600)
    return value


def umlaut_variants(word: str) -> list[str]:
    variants = {word}
    lower = word.lower()
    for ascii_form, umlaut in UMLAUTS:
        if ascii_form in lower:
            variants.add(lower.replace(ascii_form, umlaut))
        if umlaut in lower:
            variants.add(lower.replace(umlaut, ascii_form))
    return sorted(variants)


def fulltext_expression(text: str, minimum: int) -> tuple[str, list[str]]:
    """Boolescher Ausdruck: jedes Suchwort muss vorkommen (+), Umlautvarianten als ODER-Gruppe."""
    groups, short = [], []
    for word in WORD.findall(text):
        variants = [v for v in umlaut_variants(word) if len(v) >= minimum]
        if not variants:
            short.append(word)
            continue
        safe = [re.sub(r"[+\-<>()~*\"@]", "", v) for v in variants]
        groups.append("+(" + " ".join(safe) + ")")
    return " ".join(groups), short


def fulltext_pages(
    expr: str, document_ids: list[int] | None, *, limit: int = 500
) -> dict[int, tuple[int, str]]:
    """Bester Seitentreffer je Dokument (Seite, Ausschnitt) aus dem maskierten Seitentext."""
    if not expr:
        return {}
    sql = "SELECT document_id, page_no, text_content FROM document_pages WHERE MATCH(text_content) AGAINST (%s IN BOOLEAN MODE)"
    params: list = [expr]
    if document_ids is not None:
        if not document_ids:
            return {}
        placeholders = ",".join(["%s"] * len(document_ids))
        sql += f" AND document_id IN ({placeholders})"
        params.extend(document_ids)
    sql += " ORDER BY document_id, page_no LIMIT %s"
    params.append(limit)
    with connection.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    words = [w.lower() for group in re.findall(r"\(([^)]*)\)", expr) for w in group.split()]
    out: dict[int, tuple[int, str]] = {}
    for document_id, page_no, text in rows:
        if document_id in out:
            continue
        out[document_id] = (page_no, snippet(text or "", words))
    return out


def snippet(text: str, words: list[str], width: int = 70) -> str:
    low = text.lower()
    pos = min((low.find(w) for w in words if low.find(w) >= 0), default=-1)
    if pos < 0:
        return text[: 2 * width].replace("\n", " ")
    start, end = max(0, pos - width), min(len(text), pos + width)
    return ("…" if start else "") + text[start:end].replace("\n", " ") + ("…" if end < len(text) else "")


# ---------------------------------------------------------------- Suche
class SearchIndex:
    """Datenbankgestuetzte Suche; austauschbar gegen einen Suchdienst ohne Aenderung der Oberflaeche (H 7.2)."""

    @staticmethod
    def query(filters: Filters, user, *, limit: int = 100) -> SearchResult:
        started = time.perf_counter()
        result = SearchResult(min_token_len=min_token_size())
        if filters.is_empty:
            return result
        can_owner = user_has_permission(user, "owner_files.read")
        can_tenant = user_has_permission(user, "tenant_files.read")
        objects = _objects(filters)
        object_ids = [o.pk for o in objects] if (filters.object_number or filters.management_type) else None
        if filters.object_number or filters.management_type:
            result.objects = [
                Hit(
                    "object",
                    f"Objekt {o.object_number} {o.name or ''}".strip(),
                    o.get_management_type_display(),
                    f"/objekte/{o.pk}/",
                    o.object_number,
                )
                for o in objects[:limit]
            ]
        units = _units(filters, object_ids)
        if filters.unit:
            result.units = [
                Hit(
                    "unit",
                    f"{u.unit_label} ({u.get_unit_type_display()})",
                    f"Objekt {u.object.object_number} {u.object.name or ''}",
                    f"/objekte/{u.object_id}/",
                    u.object.object_number,
                    extra={"owners": _current_owner_names(u)},
                )
                for u in units[:limit]
            ]
        owners = _owners(filters, object_ids) if filters.owner else []
        if filters.owner and can_owner:
            result.owners = [_owner_hit(o) for o in owners[:limit]]
        tenants = _tenants(filters, object_ids) if filters.tenant else []
        if filters.tenant and can_tenant:
            result.tenants = [_tenant_hit(t) for t in tenants[:limit]]
        docs = _documents(filters, object_ids, units, owners, tenants, can_owner, can_tenant)
        if docs is not None:
            result.documents, result.truncated, result.short_words = _document_hits(
                filters, docs, result.min_token_len, limit
            )
        result.elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "Suche: %s Treffer in %s ms", result.total, result.elapsed_ms
        )  # kein Suchtext im Log (H 7.1)
        return result


def _objects(f: Filters):
    qs = ManagedObject.active.all()
    if f.object_number:
        q = f.object_number
        qs = qs.filter(
            Q(object_number__contains=q)
            | Q(name__icontains=q)
            | Q(street__icontains=q)
            | Q(city__icontains=q)
        )
        if q.isdigit():
            qs = ManagedObject.active.filter(
                Q(object_number_numeric=int(q))
                | Q(name__icontains=q)
                | Q(street__icontains=q)
                | Q(city__icontains=q)
            )
    if f.management_type:
        qs = qs.filter(management_type=f.management_type)
    return list(qs.order_by("object_number_numeric")[:200])


def _units(f: Filters, object_ids):
    if not f.unit:
        return []
    norm = normalize_label(f.unit)
    qs = Unit.active.select_related("object").filter(
        Q(unit_label_normalized=norm) | Q(unit_label_normalized__startswith=norm)
    )
    digits = re.sub(r"\D", "", f.unit)
    if digits:
        qs = Unit.active.select_related("object").filter(
            Q(unit_label_normalized=norm) | Q(unit_label_normalized__startswith=norm) | Q(unit_number=digits)
        )
    if object_ids is not None:
        qs = qs.filter(object_id__in=object_ids)
    return list(qs.order_by("object__object_number_numeric", "unit_label_normalized")[:200])


def _party_query(model, text: str, object_ids, *, assignment_model, unit_path: str):
    from apps.drive.naming import transliterate

    needle = transliterate(text).upper()
    qs = model.active.filter(
        Q(search_name__startswith=needle) | Q(last_name__istartswith=text) | Q(company_name__istartswith=text)
    )
    if object_ids is not None:
        ids = assignment_model.objects.filter(**{f"{unit_path}__object_id__in": object_ids}).values_list(
            model.__name__.lower() + "_id", flat=True
        )
        qs = qs.filter(pk__in=ids)
    rows = list(qs.order_by("search_name")[:100])
    if len(rows) < 3:  # unscharf (E 3.2), wenn die Praefixsuche wenig liefert
        from rapidfuzz import fuzz

        candidates = model.active.all()
        if object_ids is not None:
            candidates = candidates.filter(pk__in=ids)
        scored = [(fuzz.partial_ratio(needle, (o.search_name or "").upper()), o) for o in candidates[:2000]]
        for score, o in sorted(scored, key=lambda x: -x[0]):
            if score >= 80 and o not in rows:
                rows.append(o)
            if len(rows) >= 20:
                break
    return rows


def _owners(f: Filters, object_ids):
    return _party_query(Owner, f.owner, object_ids, assignment_model=OwnerUnitAssignment, unit_path="unit")


def _tenants(f: Filters, object_ids):
    return _party_query(Tenant, f.tenant, object_ids, assignment_model=TenantUnitAssignment, unit_path="unit")


def _current_owner_names(unit: Unit) -> list[str]:
    today = date.today()
    return [
        str(a.owner)
        for a in OwnerUnitAssignment.active.filter(unit=unit).select_related("owner")
        if (a.valid_from is None or a.valid_from <= today) and (a.valid_to is None or a.valid_to >= today)
    ]


def _owner_hit(o: Owner) -> Hit:
    assignments = (
        OwnerUnitAssignment.active.filter(owner=o)
        .select_related("unit__object")
        .order_by("unit__object__object_number_numeric", "valid_from")
    )
    lines = [
        f"Objekt {a.unit.object.object_number} {a.unit.unit_label} {a.valid_from.strftime('%d.%m.%Y') if a.valid_from else 'unbekannt'} bis {a.valid_to.strftime('%d.%m.%Y') if a.valid_to else 'heute'}"
        for a in assignments
    ]
    return Hit(
        "owner",
        str(o),
        "; ".join(lines) or "ohne Zuordnung",
        f"/eigentuemer/{o.pk}/",
        extra={"assignments": lines},
    )


def _tenant_hit(t: Tenant) -> Hit:
    assignments = TenantUnitAssignment.objects.filter(tenant=t, deleted_at__isnull=True).select_related(
        "unit__object"
    )
    lines = [f"Objekt {a.unit.object.object_number} {a.unit.unit_label}" for a in assignments]
    return Hit("tenant", str(t), "; ".join(lines) or "ohne Zuordnung", "", extra={"assignments": lines})


def _documents(f: Filters, object_ids, units, owners, tenants, can_owner: bool, can_tenant: bool):
    """Dokument-Abfrage nur, wenn ein dokumentbezogener Filter oder Volltext gesetzt ist; None bedeutet nicht gesucht."""
    wants = any([f.text, f.year, f.date_from, f.date_to, f.document_type, f.category, f.status]) or (
        (f.owner and owners) or (f.unit and units) or (f.tenant and tenants)
    )
    if not wants:
        return None
    qs = (
        Document.objects.filter(deleted_at__isnull=True)
        .exclude(status__in=("moved_out", "duplicate"))
        .select_related("object", "category", "subfolder", "document_type")
    )
    if object_ids is not None:
        qs = qs.filter(object_id__in=object_ids)
    if f.category:
        qs = qs.filter(category_id=f.category)
    if f.document_type:
        qs = qs.filter(document_type__code=f.document_type)
    if f.status == "final":
        qs = qs.filter(status__in=CONFIRMED_DOC_STATUSES)
    elif f.status == "review":
        qs = qs.filter(status="review")
    if f.year:
        qs = qs.filter(
            Q(period_year=f.year)
            | Q(document_date__year=f.year)
            | Q(documentownerlinks__period_year=f.year, documentownerlinks__deleted_at__isnull=True)
        )
    if f.date_from:
        qs = qs.filter(Q(document_date__gte=f.date_from) | Q(period_to__gte=f.date_from))
    if f.date_to:
        qs = qs.filter(Q(document_date__lte=f.date_to) | Q(period_from__lte=f.date_to))
    if f.owner:
        qs = (
            qs.filter(
                documentownerlinks__owner_id__in=[o.pk for o in owners],
                documentownerlinks__deleted_at__isnull=True,
            )
            if owners
            else qs.none()
        )
    if f.unit:
        qs = (
            qs.filter(
                documentownerlinks__unit_id__in=[u.pk for u in units],
                documentownerlinks__deleted_at__isnull=True,
            )
            if units
            else qs.none()
        )
    if f.tenant:
        qs = (
            qs.filter(
                documenttenantlinks__tenant_id__in=[t.pk for t in tenants],
                documenttenantlinks__deleted_at__isnull=True,
            )
            if tenants
            else qs.none()
        )
    # Rechte (CR 10, B-15): Akten nur fuer berechtigte Rollen; nicht klassifizierte Dokumente bleiben sichtbar
    if not can_owner:
        qs = qs.exclude(category_id="05")
    if not can_tenant:
        qs = qs.exclude(category_id="04")
    return qs.distinct()


def _document_hits(f: Filters, qs, minimum: int, limit: int) -> tuple[list[Hit], bool, list[str]]:
    short: list[str] = []
    pages: dict[int, tuple[int, str]] = {}
    if f.text:
        expr, short = fulltext_expression(f.text, minimum)
        if not expr:
            return [], False, short
        ids = list(qs.values_list("pk", flat=True)[:5000]) if f.has_relational_filter else None
        pages = fulltext_pages(expr, ids)
        qs = qs.filter(pk__in=list(pages)) if pages else qs.none()
    docs = list(qs.order_by("object__object_number_numeric", "current_name")[: limit + 1])
    truncated = len(docs) > limit
    docs = docs[:limit]
    doc_ids = [d.pk for d in docs]
    links: dict[int, list[DocumentOwnerLink]] = {}
    for link in DocumentOwnerLink.objects.filter(
        document_id__in=doc_ids, deleted_at__isnull=True
    ).select_related("owner", "unit", "owner_file"):
        links.setdefault(link.document_id, []).append(link)
    open_cases = set(
        ReviewCase.objects.filter(
            document_id__in=doc_ids, status__in=[CaseStatus.OPEN, CaseStatus.IN_PROGRESS]
        ).values_list("document_id", flat=True)
    )
    hits = []
    for d in docs:
        page_no, snip = pages.get(d.pk, (None, ""))
        parts = [d.category.folder_name if d.category_id else "nicht klassifiziert"]
        if d.subfolder_id:
            parts.append(d.subfolder.folder_name)
        if d.document_type_id:
            parts.append(d.document_type.name)
        akten = sorted({link.owner_file.folder_name for link in links.get(d.pk, []) if link.owner_file_id})
        spans = [f"S. {link.page_from} bis {link.page_to}" for link in links.get(d.pk, []) if link.page_from]
        hits.append(
            Hit(
                "document",
                d.current_name,
                " / ".join(parts),
                f"/dokumente/{d.pk}/",
                d.object.object_number,
                page_no=page_no,
                snippet=snip,
                extra={
                    "status": d.get_status_display(),
                    "akten": akten,
                    "spans": spans,
                    "period_year": d.period_year,
                    "drive_url": f"https://drive.google.com/file/d/{d.drive_file_id}/view"
                    if d.drive_file_id
                    else "",
                    "review_open": d.pk in open_cases,
                },
            )
        )
    return hits, truncated, short


def iban_index_check() -> int:
    """Pruefabfrage CR 10: Anzahl Seiten mit IBAN-Muster im Volltextindex; muss 0 sein."""
    with connection.cursor() as cur:
        cur.execute(
            r"SELECT COUNT(*) FROM document_pages WHERE text_content REGEXP 'DE[0-9]{2}[0-9 ]{18,24}'"
        )
        return int(cur.fetchone()[0])
