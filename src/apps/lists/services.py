"""Listenerzeugung je Objekt (CR 12a, H 5.6): Daten laden, Pruefsumme, Excel und PDF erzeugen, IBAN-Pruefung ueber
alle Zellwerte und den PDF-Text, Veroeffentlichung ueber publish_list, Protokoll in list_generations, Sperre je
Objekt, Entprellung nach Review-Bestaetigungen; bei Drive-Fehlern bleiben die Dateien lokal und ein Fall entsteht."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from apps.audit.services import record
from apps.config import store
from apps.drive.adapter import DriveError
from apps.lists import data as data_mod
from apps.lists import excel, pdf, publish
from apps.lists.models import GenerationStatus, ListGeneration, TriggerKind
from apps.objects.models import ManagedObject
from apps.pipeline import storage
from objektakte.masking import contains_sensitive

logger = logging.getLogger(__name__)
LOCK_TIMEOUT = 600


class ListError(Exception):
    pass


@dataclass
class ListResult:
    list_type: str
    content_hash: str
    files: dict[str, Path]
    generations: list[ListGeneration] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def status(self) -> str:
        statuses = {g.status for g in self.generations}
        if GenerationStatus.FAILED in statuses:
            return GenerationStatus.FAILED
        if statuses == {GenerationStatus.SKIPPED_UNCHANGED}:
            return GenerationStatus.SKIPPED_UNCHANGED
        return GenerationStatus.DONE


def list_types_for(obj: ManagedObject) -> list[str]:
    """Eigentuemerliste fuer WEG, Mieterliste fuer Mietverwaltung; bei WEG mit Sondereigentumsverwaltung beide."""
    if obj.management_type == "rental":
        return ["tenant_list"]
    if obj.management_type == "weg_with_se":
        return ["owner_list", "tenant_list"]
    return ["owner_list"]


def local_dir(obj: ManagedObject) -> Path:
    return storage.data_dir() / "lists" / str(obj.pk)


def iban_check(data: data_mod.ListData, pdf_path: Path) -> list[str]:
    """Regulaerer Ausdruck auf IBAN-, Konto- und Ausweismuster ueber alle Zellwerte und den PDF-Text (H 5.3 Nr. 8)."""
    hits = [text[:40] for text in data_mod.all_cell_texts(data) if contains_sensitive(text)]
    try:
        import pdfplumber

        with pdfplumber.open(str(pdf_path)) as doc:
            for page in doc.pages:
                if contains_sensitive(page.extract_text() or ""):
                    hits.append(f"PDF Seite {page.page_number}")
    except Exception:  # Textextraktion ist Zusatzpruefung; Zellpruefung bleibt massgeblich
        logger.exception("PDF-Textprüfung der Liste fehlgeschlagen")
    return hits


def generate_lists(
    obj: ManagedObject,
    *,
    trigger: str = TriggerKind.MANUAL,
    user=None,
    drive=None,
    publish_files: bool = True,
) -> list[ListResult]:
    lock = f"lists:{obj.pk}"
    if not cache.add(lock, "1", timeout=LOCK_TIMEOUT):
        cache.set(f"lists:{obj.pk}:rerun", "1", timeout=LOCK_TIMEOUT)
        raise ListError("Listenerzeugung für dieses Objekt läuft bereits")
    try:
        results = [
            _generate_one(
                obj, list_type, trigger=trigger, user=user, drive=drive, publish_files=publish_files
            )
            for list_type in list_types_for(obj)
        ]
    finally:
        cache.delete(lock)
        cache.delete(f"lists:pending:{obj.pk}")
    record(
        "list.generate",
        entity_type="object",
        entity_id=obj.pk,
        object_id=obj.pk,
        actor=user,
        actor_type=None if user else "system",
        after={
            "trigger": trigger,
            "lists": {
                r.list_type: {"status": r.status, "hash": r.content_hash[:12], "error": r.error}
                for r in results
            },
        },
    )
    return results


def _generate_one(obj, list_type, *, trigger, user, drive, publish_files) -> ListResult:
    started = time.perf_counter()
    build = data_mod.build_owner_list if list_type == "owner_list" else data_mod.build_tenant_list
    data = build(obj)
    target = local_dir(obj)
    files = {fmt: target / publish.render_name(obj, list_type, fmt) for fmt in ("xlsx", "pdf")}
    excel.write_xlsx(
        data, files["xlsx"], include_provenance=bool(store.get("lists.include_provenance_sheet", False))
    )
    pdf.write_pdf(data, files["pdf"], font_pt=float(store.get("lists.pdf_table_font_pt", 8)))
    result = ListResult(list_type, data.content_hash, files)
    hits = iban_check(data, files["pdf"])
    now = timezone.now()
    duration = int((time.perf_counter() - started) * 1000)
    if hits:
        for path in files.values():
            path.unlink(missing_ok=True)
        result.error = f"Bankverbindungs- oder Ausweismuster in der Liste ({len(hits)} Treffer), Veröffentlichung abgebrochen"
        _fail(
            obj,
            list_type,
            data,
            trigger,
            user,
            result.error,
            now,
            duration,
            result,
            subtype="list_iban_found",
        )
        return result
    for fmt, path in files.items():
        gen = ListGeneration(
            object=obj,
            list_type=list_type,
            list_format=fmt,
            trigger_kind=trigger,
            triggered_by=user,
            rows_current=len(data.current),
            rows_history=len(data.history),
            rows_open=len(data.open_items),
            content_hash=data.content_hash,
            generated_at=now,
            duration_ms=duration,
            status=GenerationStatus.DONE,
        )
        if publish_files and drive is not None:
            try:
                node, status = publish.publish_list(
                    obj,
                    list_type,
                    fmt,
                    path,
                    data.content_hash,
                    drive=drive,
                    user=user,
                    messages=result.messages,
                )
                gen.drive_node, gen.status = node, status
            except (DriveError, publish.PublishError) as exc:
                gen.status = GenerationStatus.FAILED
                gen.error_message = str(exc)[:2000]
                result.error = str(exc)
                _publish_failed_case(obj, list_type, fmt, str(exc))
        else:
            result.messages.append(f"{path.name}: lokal abgelegt, keine Drive-Verbindung")
        gen.save()
        result.generations.append(gen)
    return result


def _fail(obj, list_type, data, trigger, user, error, now, duration, result, *, subtype) -> None:
    for fmt in ("xlsx", "pdf"):
        gen = ListGeneration.objects.create(
            object=obj,
            list_type=list_type,
            list_format=fmt,
            trigger_kind=trigger,
            triggered_by=user,
            rows_current=len(data.current),
            rows_history=len(data.history),
            rows_open=len(data.open_items),
            content_hash=data.content_hash,
            generated_at=now,
            duration_ms=duration,
            status=GenerationStatus.FAILED,
            error_message=error[:2000],
        )
        result.generations.append(gen)
    from apps.review.models import CaseStatus, CaseType, ReviewCase

    key = f"lists:{obj.pk}:{subtype}"
    if not ReviewCase.objects.filter(
        batch_key=key, status__in=[CaseStatus.OPEN, CaseStatus.IN_PROGRESS]
    ).exists():
        ReviewCase.objects.create(
            object=obj,
            case_type=CaseType.MOVE_PROPOSAL,
            case_subtype=subtype[:32],
            context={"reason": error, "list_type": list_type},
            batch_key=key,
            priority=70,
        )


def _publish_failed_case(obj, list_type, fmt, error: str) -> None:
    from apps.review.models import CaseStatus, CaseType, ReviewCase

    key = f"lists:{obj.pk}:list_publish_failed"
    if ReviewCase.objects.filter(
        batch_key=key, status__in=[CaseStatus.OPEN, CaseStatus.IN_PROGRESS]
    ).exists():
        return
    ReviewCase.objects.create(
        object=obj,
        case_type=CaseType.MOVE_PROPOSAL,
        case_subtype="list_publish_failed",
        context={
            "reason": error[:500],
            "list_type": list_type,
            "format": fmt,
            "local_dir": str(local_dir(obj)),
        },
        batch_key=key,
        priority=70,
    )


def request_generation(
    object_id: int, trigger: str, *, user_id: int | None = None, immediate: bool = False
) -> bool:
    """Entprellte Anforderung (H 5.6 Nr. 2): erst nach lists.debounce_seconds ohne weitere Anforderung laeuft der Job;
    der manuelle Knopf startet sofort und setzt den Zeitgeber zurueck. Rueckgabe True, wenn ein Job geplant wurde."""
    from django.conf import settings

    from apps.lists.tasks import generate_lists_task

    if not immediate and not settings.OBJEKTAKTE.get("LISTS_AUTO_GENERATE", True):
        return False
    debounce = int(store.get("lists.debounce_seconds", 60))
    key = f"lists:pending:{object_id}"
    if immediate:
        cache.delete(key)
        countdown = 0
    else:
        if not cache.add(key, "1", timeout=max(debounce, 1)):
            return False
        countdown = debounce

    def _send() -> None:
        try:
            generate_lists_task.apply_async(args=[object_id, trigger, user_id], countdown=countdown)
        except Exception:  # Broker nicht erreichbar: naechster Ausloeser holt die Erzeugung nach
            logger.exception("Listenerzeugung für Objekt %s konnte nicht angestoßen werden", object_id)

    transaction.on_commit(_send)
    return True


def latest_status(obj: ManagedObject) -> dict:
    """Stand je Liste und Format fuer die Objektansicht (H 5.6 Nr. 4, Listen veraltet seit)."""
    out: dict[str, dict] = {}
    for gen in (
        ListGeneration.objects.filter(object=obj).select_related("drive_node").order_by("-generated_at")
    ):
        out.setdefault(gen.list_type, {}).setdefault(gen.list_format, gen)
    return out


def reset_pending(object_id: int) -> None:
    cache.delete(f"lists:pending:{object_id}")
