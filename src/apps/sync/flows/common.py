"""Gemeinsame Bausteine der Abgleichsablaeufe: Fehlerabbildung des Paperless-Clients auf Operationsausgaenge,
Verknuepfungen, Konflikt- und Zuordnungsfaelle im Review Center."""

from __future__ import annotations

from django.utils import timezone

from apps.review.models import CaseStatus, CaseType, ReviewCase
from apps.sync.models import ExternalLink, LinkRole, LinkState, SyncSystem
from apps.sync.operations import Block, Defer, Retry


def map_paperless_error(exc: Exception):
    """Wandelt Client-Fehler in Operationsausgaenge um (Retry, Defer, Block); andere Ausnahmen laufen weiter."""
    from apps.sync.paperless import errors as e

    if isinstance(exc, e.PaperlessRateLimited):
        return Retry(f"Ratenbegrenzung: {exc}", seconds=int(getattr(exc, "retry_after", 0) or 60))
    if isinstance(exc, e.PaperlessUnavailable):
        return Retry(f"Paperless nicht erreichbar: {exc}")
    if isinstance(exc, e.PaperlessAuthError):
        return Block(f"Zugriff verweigert (Token oder Rechte prüfen): {exc}")
    if isinstance(exc, e.PaperlessUnsupported):
        return Block(f"vom Server nicht unterstützt: {exc}")
    if isinstance(exc, e.PaperlessNotFound):
        return Block(f"in Paperless nicht gefunden: {exc}")
    if isinstance(exc, e.PaperlessError):
        return Retry(f"Paperless-Fehler: {exc}")
    return None


def raise_mapped(exc: Exception):
    mapped = map_paperless_error(exc)
    if mapped is not None:
        raise mapped from exc
    raise exc


def client_or_defer():
    from apps.sync import services

    client = services.get_client()
    if client is None:
        raise Defer("Paperless nicht konfiguriert oder Hauptschalter aus", seconds=600)
    return client


def link_for(doc, system: str, role: str = LinkRole.ORIGINAL) -> ExternalLink | None:
    return ExternalLink.objects.filter(document=doc, system=system, role=role).first()


def upsert_link(
    doc, *, system: str, external_id: str, role: str = LinkRole.ORIGINAL, **fields
) -> ExternalLink:
    """Legt die Verknuepfung an oder aktualisiert sie; eine fremde Zeile mit derselben externen Identitaet wird
    nicht stillschweigend uebernommen (Eindeutigkeit je System, ID und Rolle)."""
    fields.setdefault("last_seen_at", timezone.now())
    link = ExternalLink.objects.filter(system=system, external_id=external_id, role=role).first()
    if link is not None and link.document_id != doc.pk:
        raise Block(
            f"{system}:{external_id} ist bereits mit Dokument {link.document_id} verknüpft (Dokument {doc.pk})"
        )
    if link is None:
        link = ExternalLink.objects.filter(document=doc, system=system, role=role).first()
    if link is None:
        return ExternalLink.objects.create(
            document=doc, system=system, role=role, external_id=external_id, **fields
        )
    for k, v in fields.items():
        setattr(link, k, v)
    link.external_id = external_id
    link.save()
    return link


def open_case(
    obj,
    *,
    case_type: str,
    subtype: str,
    key: str,
    document=None,
    context: dict | None = None,
    candidates: list | None = None,
    proposed_action: dict | None = None,
    priority: int = 70,
) -> ReviewCase | None:
    """Ein offener Fall je Schluessel (batch_key), wie _review_once in der Pipeline."""
    if ReviewCase.objects.filter(
        batch_key=key, status__in=[CaseStatus.OPEN, CaseStatus.IN_PROGRESS]
    ).exists():
        return None
    return ReviewCase.objects.create(
        object=obj,
        case_type=case_type,
        case_subtype=subtype[:32],
        document=document,
        batch_key=key[:120],
        priority=priority,
        context=context or {},
        candidates=candidates,
        proposed_action=proposed_action,
    )


def conflict(doc, subtype: str, key_part: str, context: dict) -> ReviewCase | None:
    """Abgleichskonflikt als Review-Fall; nie eine automatische Aufloesung nach dem neuesten Zeitstempel."""
    return open_case(
        doc.object,
        case_type=CaseType.SYNC_CONFLICT,
        subtype=subtype,
        key=f"sync_conflict:{subtype}:{doc.pk}:{key_part}",
        document=doc,
        context=context,
        priority=60,
    )


def mark_link(link: ExternalLink, state: str, reason: str | None = None, **fields) -> None:
    fields.update({"state": state, "state_reason": (reason or "")[:255] or None})
    ExternalLink.objects.filter(pk=link.pk).update(**fields)


PAPERLESS = SyncSystem.PAPERLESS
DRIVE = SyncSystem.DRIVE
SYNCED = LinkState.SYNCED
