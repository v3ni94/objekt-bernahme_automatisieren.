"""Upload aus dem CRM in die Verarbeitung (Ergaenzung zum Schnittstellenvertrag M29, 26.09.2026).

Das CRM uebergibt eine Datei mit der Kennung seines Dokuments (crm_document_id) und optionalen Hinweisen. Die Datei
laeuft durch dieselbe Annahme wie ein Upload in der Anwendung (apps.documents.ingest): Transitbereich, Status
registered, Verarbeitungslauf des Objekts; Hash, Dublettenpruefung, OCR, Klassifikation und Ablage in Drive (mit
Eigentuemer- und Mieterakten) sowie die Uebertragung nach Paperless folgen der Pipeline und den dortigen Schaltern.
Der Upload ist idempotent ueber crm_document_id: eine Wiederholung liefert das bereits angelegte Dokument.

Schalter sync.crm_uploads_enabled (Vorgabe aus): ohne ihn antwortet der Endpunkt mit 503, das CRM wiederholt spaeter.
"""

from __future__ import annotations

import json
import re

from django.db import IntegrityError, transaction

from apps.audit.services import record
from apps.config import store
from apps.crm_api.models import CrmUpload
from apps.documents import ingest

CRM_DOCUMENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
MAX_HINTS_BYTES = 8192
HINT_LISTS = ("unit_labels", "owner_refs", "tenant_refs", "contact_refs")
HINT_TEXTS = ("ticket_number", "title", "category", "document_type", "uploaded_by")
MAX_LIST_ITEMS = 50
MAX_TEXT = 300


class UploadError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def uploads_enabled() -> bool:
    return bool(store.get("sync.crm_uploads_enabled", False))


def parse_crm_document_id(raw: str | None) -> str:
    value = (raw or "").strip()
    if not CRM_DOCUMENT_ID.match(value):
        raise UploadError(
            400, "crm_document_id fehlt oder ist ungueltig (1 bis 64 Zeichen, Buchstaben, Ziffern, . _ : -)"
        )
    return value


def parse_hints(raw: str | None) -> dict:
    """Hinweise als JSON-Objekt; nur bekannte Schluessel, Listen aus Zeichenketten, begrenzte Laengen."""
    if raw in (None, ""):
        return {}
    if len(raw.encode("utf-8")) > MAX_HINTS_BYTES:
        raise UploadError(400, f"hints groesser als {MAX_HINTS_BYTES} Byte")
    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise UploadError(400, "hints ist kein gueltiges JSON") from exc
    if not isinstance(value, dict):
        raise UploadError(400, "hints muss ein JSON-Objekt sein")
    unknown = sorted(set(value) - set(HINT_LISTS) - set(HINT_TEXTS))
    if unknown:
        raise UploadError(400, f"unbekannte Schluessel in hints: {', '.join(unknown)}")
    hints: dict = {}
    for key in HINT_LISTS:
        items = value.get(key)
        if items is None:
            continue
        if not isinstance(items, list) or not all(isinstance(i, str) for i in items):
            raise UploadError(400, f"hints.{key} muss eine Liste aus Zeichenketten sein")
        cleaned = [i.strip()[:MAX_TEXT] for i in items if i.strip()][:MAX_LIST_ITEMS]
        if cleaned:
            hints[key] = cleaned
    for key in HINT_TEXTS:
        text = value.get(key)
        if text is None:
            continue
        if not isinstance(text, str):
            raise UploadError(400, f"hints.{key} muss eine Zeichenkette sein")
        if text.strip():
            hints[key] = text.strip()[:MAX_TEXT]
    return hints


def existing(crm_document_id: str) -> CrmUpload | None:
    return (
        CrmUpload.objects.filter(crm_document_id=crm_document_id).select_related("document__object").first()
    )


def accept(obj, *, crm_document_id: str, filename: str, data: bytes, hints: dict, token=None, request=None):
    """Legt Dokument und Herkunft an und gibt es in die Verarbeitung. Rueckgabe (upload, angelegt)."""
    found = existing(crm_document_id)
    if found is not None:
        if found.document.object_id != obj.pk:
            raise UploadError(409, "crm_document_id ist bereits einem anderen Objekt zugeordnet")
        return found, False
    if obj.deleted_at is not None:
        raise UploadError(409, "Objekt ist archiviert")
    limit = int(store.get("documents.max_download_bytes", 524288000))
    if len(data) > limit:
        raise UploadError(413, f"Datei größer als {limit} Byte")
    try:
        with transaction.atomic():
            doc = ingest.register_upload(obj, filename=filename, data=data, request=request)
            upload = CrmUpload.objects.create(
                document=doc, crm_document_id=crm_document_id, hints=hints, token=token
            )
            record(
                "crm_api.upload",
                entity_type="document",
                entity_id=doc.pk,
                object_id=obj.pk,
                request=request,
                after={
                    "crm_document_id": crm_document_id,
                    "token_id": getattr(token, "pk", None),
                    "hint_keys": sorted(hints),
                },
            )
    except ingest.IngestError as exc:
        raise UploadError(400, str(exc)) from exc
    except IntegrityError:
        # Gleichzeitige Wiederholung desselben Uploads: der erste Aufruf hat angelegt
        found = existing(crm_document_id)
        if found is None:
            raise
        return found, False
    ingest.ensure_run(obj, documents=[doc])
    return upload, True
