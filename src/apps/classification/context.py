"""Eingangsgroessen fuer Regeln und Klassifikator je Dokument (E 2.1): Dateiname, Herkunftsordner, Text der ersten
Seiten plus letzte Seite, erkannte Entitaeten, Verwaltungsart, eigene Firma, Vertragspartner."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from apps.config import store
from apps.documents.models import Document, DocumentEntity, DocumentPage


@dataclass
class PeriodRef:
    """Zeitbezug mit Praezedenz (E 6.2 P4): Abrechnungs- oder Wirtschaftsjahr vor Zeitraum vor Forderungszeitraum
    vor Dokumentdatum."""

    year: int | None = None
    date_from: date | None = None
    date_to: date | None = None
    document_date: date | None = None
    source: str | None = None

    @property
    def has_period(self) -> bool:
        return bool(self.year or self.date_from or self.date_to)


@dataclass
class DocContext:
    document_id: int | None
    object_id: int | None
    management_type: str
    filename: str
    folder_code: str | None  # z. B. "05/03" oder "03", Herkunftsordner in Drive (weicher Hinweis)
    text: str  # maskierter Text der ersten Seiten plus letzte Seite
    head: str  # maskierter Text der ersten Seite
    page_count: int
    pages: dict[int, str] = field(default_factory=dict)
    unit_ids: list[int] = field(default_factory=list)
    unit_labels: list[str] = field(default_factory=list)
    owner_ids: list[int] = field(default_factory=list)  # in Reihenfolge des ersten Auftretens
    owner_candidates: list[str] = field(default_factory=list)  # Namen ohne Stammdatentreffer
    tenant_ids: list[int] = field(default_factory=list)
    period: PeriodRef = field(default_factory=PeriodRef)
    own_object_marker: bool = False
    foreign_object_numbers: list[str] = field(default_factory=list)
    iban_found: bool = False
    id_document_found: bool = False
    own_company_as_agent: bool = False
    contract_partners: list[str] = field(default_factory=list)
    amounts: list[str] = field(default_factory=list)
    entity_pages: dict[str, dict[int, list[int]]] = field(default_factory=dict)  # typ -> seite -> ids

    def has(self, name: str) -> bool:
        """Bedingung entity_required und not_entity (E 2.2)."""
        return {
            "unit": bool(self.unit_ids),
            "owner": bool(self.owner_ids),
            "owner_candidate": bool(self.owner_ids or self.owner_candidates),
            "tenant": bool(self.tenant_ids),
            "period_year": self.period.year is not None,
            "period": self.period.has_period,
            "document_date": self.period.document_date is not None,
            "own_object_marker": self.own_object_marker,
            "foreign_object_marker": bool(self.foreign_object_numbers),
            "own_company_as_agent": self.own_company_as_agent,
            "contract_partner": bool(self.contract_partners),
            "iban": self.iban_found,
            "id_document": self.id_document_found,
            "amount": bool(self.amounts),
        }.get(name, False)

    def metadata(self) -> dict:
        return {
            "period_year": self.period.year,
            "period_from": self.period.date_from.isoformat() if self.period.date_from else None,
            "period_to": self.period.date_to.isoformat() if self.period.date_to else None,
            "document_date": self.period.document_date.isoformat() if self.period.document_date else None,
            "foreign_object_number": self.foreign_object_numbers[0] if self.foreign_object_numbers else None,
            "unit_label": self.unit_labels[0] if self.unit_labels else None,
        }


def _parse_iso(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def period_from_entities(entities: list[DocumentEntity]) -> PeriodRef:
    """Praezedenz E 6.2 P4. Jahr aus Kontext (Abrechnungsjahr, Wirtschaftsjahr, Hausgeld ...), sonst Zeitraum von bis,
    sonst Monat/Jahr (Forderungszeitraum), sonst Dokumentdatum (fruehestes Datum der ersten Seite, sonst erstes)."""
    ref = PeriodRef()
    years = [
        e
        for e in entities
        if e.entity_type == "period" and re.fullmatch(r"20\d{2}", e.value_normalized or "")
    ]
    ranges = [
        e
        for e in entities
        if e.entity_type == "period" and re.fullmatch(r"20\d{2}-20\d{2}", e.value_normalized or "")
    ]
    months = [
        e
        for e in entities
        if e.entity_type == "period" and re.fullmatch(r"20\d{2}-\d{2}", e.value_normalized or "")
    ]
    dates = [e for e in entities if e.entity_type == "date" and e.value_normalized]
    if years:
        ref.year = int(years[0].value_normalized)
        ref.source = "period_year"
    elif ranges:
        a, b = ranges[0].value_normalized.split("-")
        ref.date_from, ref.date_to = date(int(a), 1, 1), date(int(b), 12, 31)
        ref.source = "period_range"
    elif months:
        y, m = months[0].value_normalized.split("-")
        y, m = int(y), int(m)
        last = (date(y + (m // 12), (m % 12) + 1, 1) - date.resolution) if m < 12 else date(y, 12, 31)
        ref.date_from, ref.date_to = date(y, m, 1), last
        ref.source = "claim_period"
    if dates:
        first_page = min(e.page_no for e in dates)
        candidates = sorted(_parse_iso(e.value_normalized) for e in dates if e.page_no == first_page)
        candidates = [c for c in candidates if c]
        ref.document_date = candidates[0] if candidates else None
        if not ref.has_period and ref.document_date:
            ref.source = "document_date"
    # Zeitraum "01.01.2024 bis 31.12.2024" als Datumsbereich, wenn kein Jahr aus Kontext vorliegt
    if not years and not ranges and not months:
        range_entities = [
            e for e in entities if e.entity_type == "period" and (e.value_text or "").count(".") >= 4
        ]
        if range_entities:
            m = re.findall(r"(\d{1,2})\.(\d{1,2})\.(20\d{2})", range_entities[0].value_text or "")
            if len(m) >= 2:
                try:
                    ref.date_from = date(int(m[0][2]), int(m[0][1]), int(m[0][0]))
                    ref.date_to = date(int(m[1][2]), int(m[1][1]), int(m[1][0]))
                    ref.source = "period_range"
                except ValueError:
                    pass
    return ref


def _selected_pages(page_count: int, first_pages: int) -> list[int]:
    if page_count <= 0:
        return []
    pages = list(range(1, min(first_pages, page_count) + 1))
    if page_count > first_pages and page_count not in pages:
        pages.append(page_count)
    return pages


def build_context(doc: Document, *, entities: list[DocumentEntity] | None = None) -> DocContext:
    obj = doc.object
    first_pages = int(store.get("classification.first_pages", 3))
    max_chars = int(store.get("classification.text_max_chars", 4000))
    pages = {
        p.page_no: p.text_content or "" for p in DocumentPage.objects.filter(document=doc).order_by("page_no")
    }
    page_count = max(pages) if pages else 0
    selected = _selected_pages(page_count, first_pages)
    text = "\n\f".join(pages.get(n, "") for n in selected)[: max_chars * 3]
    head = pages.get(1, "")
    if entities is None:
        entities = list(DocumentEntity.objects.filter(document=doc).order_by("page_no", "char_from"))
    folder_code = None
    node = doc.drive_node
    if node is not None and node.category_id:
        folder_code = node.category_id + (f"/{node.subfolder.code}" if node.subfolder_id else "")
    ctx = DocContext(
        document_id=doc.pk,
        object_id=obj.pk,
        management_type=obj.management_type,
        filename=doc.current_name or doc.original_name or "",
        folder_code=folder_code,
        text=text,
        head=head,
        page_count=page_count,
        pages=pages,
    )
    seen_units: list[int] = []
    for e in entities:
        if e.entity_type == "unit_label" and e.matched_unit_id and e.matched_unit_id not in seen_units:
            seen_units.append(e.matched_unit_id)
            ctx.unit_labels.append(e.value_text)
        elif e.entity_type in ("person_name", "company_name"):
            if e.matched_owner_id and e.matched_owner_id not in ctx.owner_ids:
                ctx.owner_ids.append(e.matched_owner_id)
            elif e.matched_tenant_id and e.matched_tenant_id not in ctx.tenant_ids:
                ctx.tenant_ids.append(e.matched_tenant_id)
            elif (
                not e.matched_owner_id
                and not e.matched_tenant_id
                and e.value_text not in ctx.owner_candidates
            ):
                ctx.owner_candidates.append(e.value_text)
        elif e.entity_type == "iban":
            ctx.iban_found = True
            if e.matched_owner_id and e.matched_owner_id not in ctx.owner_ids:
                ctx.owner_ids.append(e.matched_owner_id)
            if e.matched_tenant_id and e.matched_tenant_id not in ctx.tenant_ids:
                ctx.tenant_ids.append(e.matched_tenant_id)
        elif e.entity_type == "id_document_number":
            ctx.id_document_found = True
        elif e.entity_type == "object_number":
            if e.value_normalized == "own":
                ctx.own_object_marker = True
            elif e.value_normalized and e.value_normalized not in ctx.foreign_object_numbers:
                ctx.foreign_object_numbers.append(e.value_normalized)
        elif e.entity_type == "amount":
            ctx.amounts.append(e.value_normalized or e.value_text)
        key = e.entity_type
        ref = e.matched_unit_id or e.matched_owner_id or e.matched_tenant_id
        if ref:
            ctx.entity_pages.setdefault(key, {}).setdefault(e.page_no, []).append(ref)
    ctx.unit_ids = seen_units
    ctx.period = period_from_entities(entities)
    full_text = "\n".join(pages.values())
    own_names = [n for n in (store.get("classification.own_company_names", []) or []) if n]
    ctx.own_company_as_agent = any(_name_in(n, full_text) for n in own_names)
    partners = [n for n in (store.get("classification.contract_partners", []) or []) if n]
    ctx.contract_partners = [n for n in partners if _name_in(n, full_text)]
    if (
        obj.object_number
        and re.search(rf"(?<!\d)0*{re.escape(obj.object_number)}(?!\d)", full_text)
        and re.search(
            r"(?i)objekt(?:nummer|-Nr\.?|nr\.?)?\s*:?\s*0*" + re.escape(obj.object_number), full_text
        )
    ):
        ctx.own_object_marker = True
    return ctx


def _name_in(name: str, text: str) -> bool:
    from apps.drive.naming import transliterate

    pattern = re.escape(transliterate(name).lower()).replace(r"\ ", r"\s+")
    return re.search(pattern, transliterate(text).lower()) is not None
