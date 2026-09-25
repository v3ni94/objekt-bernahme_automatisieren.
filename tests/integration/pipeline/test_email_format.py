"""E-Mails (.eml, .msg) laufen seit dem 25.09.2026 als Textseite durch die Kette; Bestandsfaelle "nicht
unterstuetztes Format" werden mit formate_wiederaufnehmen erledigt und neu verarbeitet."""

from __future__ import annotations

from email.message import EmailMessage
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.documents import ingest
from apps.documents.models import Document, DocumentPage
from apps.review.models import CaseStatus, CaseType, ReviewCase

pytestmark = pytest.mark.django_db


def _eml() -> bytes:
    msg = EmailMessage()
    msg["From"] = "Firma Beispiel <service@example.test>"
    msg["To"] = "verwaltung@example.test"
    msg["Subject"] = "Wartungsprotokoll Aufzug Musterstraße 49"
    msg["Date"] = "Mon, 03 Mar 2025 09:15:00 +0100"
    msg.set_content("Guten Tag,\n\nanbei das Wartungsprotokoll fuer den Aufzug.\n\nMit freundlichen Grüßen")
    msg.add_attachment(b"%PDF-1.4 test", maintype="application", subtype="pdf", filename="Protokoll.pdf")
    return bytes(msg)


def test_email_upload_wird_als_textseite_gelesen(objekt, stammdaten, run_all):
    doc, _ = ingest.ingest_upload(objekt, filename="Wartung.eml", data=_eml())
    run_all(objekt)
    doc.refresh_from_db()
    assert doc.page_count == 1 and doc.origin_kind == "digital" and doc.sha256
    assert doc.status in ("review", "classified", "filed")
    assert not ReviewCase.objects.filter(document=doc, case_subtype="unsupported_format").exists()
    seite = DocumentPage.objects.get(document=doc, page_no=1)
    assert "Betreff: Wartungsprotokoll Aufzug" in seite.text_content
    assert "Anhänge: Protokoll.pdf" in seite.text_content


def _altfall(objekt, doc: Document) -> ReviewCase:
    """Zustand vor dem 25.09.2026 nachstellen: Dokument in der Pruefung mit offenem Fall."""
    doc.status = "review"
    doc.save(update_fields=["status", "updated_at"])
    return ReviewCase.objects.create(
        object=objekt,
        case_type=CaseType.UNCLEAR,
        case_subtype="unsupported_format",
        document=doc,
        batch_key=f"unsupported:{doc.pk}",
        priority=80,
        context={"mime_type": doc.mime_type, "name": doc.current_name},
    )


def test_formate_wiederaufnehmen(objekt, stammdaten, run_all):
    mail, _ = ingest.ingest_upload(objekt, filename="Nachricht.eml", data=_eml())
    fall = _altfall(objekt, mail)
    zip_doc = Document.objects.create(
        object=objekt,
        size_bytes=4,
        mime_type="application/zip",
        original_name="Archiv.zip",
        current_name="Archiv.zip",
        source="upload",
        status="review",
        first_seen_at=timezone.now(),
    )
    zip_fall = _altfall(objekt, zip_doc)
    out = StringIO()
    call_command("formate_wiederaufnehmen", stdout=out)
    text = out.getvalue()
    assert "Faelle mit inzwischen bekanntem Format: 1" in text and "Nach Format: email 1" in text
    assert "Nach Quelle: Upload 1" in text and "Weiterhin nicht verarbeitbar: 1 (.zip 1)" in text
    assert "Vorschau: 1 Faelle" in text and "Archiv" not in text and "Nachricht" not in text
    mail.refresh_from_db()
    assert mail.status == "review"  # Vorschau aendert nichts
    out = StringIO()
    call_command("formate_wiederaufnehmen", "--objekt", objekt.object_number, "--echt", stdout=out)
    assert "Wieder aufgenommen: 1 Dokumente" in out.getvalue()
    mail.refresh_from_db()
    fall.refresh_from_db()
    zip_fall.refresh_from_db()
    assert mail.status == "registered" and mail.page_count is None
    assert fall.status == CaseStatus.RESOLVED and fall.resolution["action"] == "reprocess_format"
    assert zip_fall.status == CaseStatus.OPEN
    assert AuditEvent.objects.filter(action="pipeline.format_reprocess", entity_id=mail.pk).exists()
    ingest.ensure_run(objekt, documents=[mail])
    run_all(objekt)
    mail.refresh_from_db()
    assert mail.page_count == 1 and mail.status in ("review", "classified", "filed")
    assert not ReviewCase.objects.filter(
        document=mail, case_subtype="unsupported_format", status=CaseStatus.OPEN
    ).exists()
    out = StringIO()
    call_command("formate_wiederaufnehmen", "--echt", stdout=out)
    assert "Keine offenen Faelle mit inzwischen bekanntem Format." in out.getvalue()
