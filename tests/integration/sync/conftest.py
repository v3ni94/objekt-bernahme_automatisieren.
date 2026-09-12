"""Fixtures der Synchronisationstests: Fake-Paperless anstelle des HTTP-Clients, Fake-Drive anstelle der
Google-Verbindung, Testobjekte mit Ordnerstruktur, Konfiguration im Pilotmodus. Kein Netz, keine echten
Zugangsdaten (Token sind erkennbare Testwerte)."""

from __future__ import annotations

import io

import pytest
from django.conf import settings

from apps.config import store
from apps.objects.models import ManagedObject
from apps.sync import operations, services
from apps.sync.models import OperationStatus, SyncOperation
from apps.sync.paperless.fake import FakePaperless

PAPERLESS_URL = "https://paperless.example.test"
TEST_TOKEN = "test-token-nicht-echt"  # noqa: S105  Testwert, kein Geheimnis
WEBHOOK_TOKEN = "webhook-testwert-nicht-echt"  # noqa: S105  Testwert, kein Geheimnis

LINES_623 = [
    "Objekt 623 Musterstadt, Musterstraße 49",
    "Rechnung Nr. 2026-0815 vom 03.09.2026",
    "Wartung der Heizungsanlage im Haus Musterstraße 49, 12345 Musterstadt",
    "Leistungszeitraum August 2026, Rechnungsbetrag 238,00 EUR",
    "Alle Angaben in diesem Testdokument sind erfunden; keine realen Personen oder Konten.",
    "Zahlbar innerhalb von 14 Tagen ohne Abzug.",
]


def pdf_bytes(lines: list[str] | None = None, *, pages: int = 1) -> bytes:
    """Kleines digitales PDF mit Textebene (reportlab), fuer OCR-freie Verarbeitung."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    for page in range(pages):
        c.setFont("Helvetica", 10)
        y = A4[1] - 50
        for line in lines or LINES_623:
            c.drawString(40, y, line)
            y -= 14
        c.drawString(40, y - 14, f"Seite {page + 1} von {pages}")
        c.showPage()
    c.save()
    return buf.getvalue()


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    path = tmp_path / "data"
    path.mkdir()
    monkeypatch.setitem(settings.OBJEKTAKTE, "DATA_DIR", path)
    monkeypatch.setitem(settings.OBJEKTAKTE, "DISK_RESERVE_GB", 0)
    return path


@pytest.fixture
def drive(seeded, monkeypatch, admin_user):
    """Fake-Drive als Google-Verbindung; die Wurzel gilt als bestaetigt."""
    from apps.drive import oauth
    from apps.drive.adapter import InMemoryDriveAdapter

    adapter = InMemoryDriveAdapter()
    monkeypatch.setattr(oauth, "get_adapter", lambda: adapter)
    store.set("drive.root_folder_id", adapter.root_id, user=admin_user, reason="Test")
    return adapter


def reconcile(obj, drive):
    from apps.drive.reconcile import DriveConfig, reconcile_object

    reconcile_object(
        obj, drive=drive, dry_run=False, cfg=DriveConfig.from_settings(root_folder_id=drive.root_id)
    )
    obj.refresh_from_db()
    return obj


@pytest.fixture
def objekt(seeded, data_dir, drive):
    obj = ManagedObject.objects.create(
        object_number="623",
        name="Musterstadt, Musterstraße 49",
        street="Musterstraße",
        house_number="49",
        city="Musterstadt",
        postal_code="12345",
        management_type="weg",
        is_test=True,
    )
    return reconcile(obj, drive)


@pytest.fixture
def anderes_objekt(objekt, drive):
    obj = ManagedObject.objects.create(
        object_number="624",
        name="Beispielhausen, Beispielweg 7",
        street="Beispielweg",
        house_number="7",
        city="Beispielhausen",
        postal_code="54321",
        management_type="weg",
        is_test=True,
    )
    return reconcile(obj, drive)


@pytest.fixture
def paperless(seeded, monkeypatch, admin_user, objekt):
    """Fake-Paperless als Client; Hauptschalter an, Modus pilot mit Objekt 623, Tag und Felder eingerichtet."""
    fake = FakePaperless()
    monkeypatch.setitem(settings.OBJEKTAKTE, "PAPERLESS_TOKEN", TEST_TOKEN)
    monkeypatch.setitem(settings.OBJEKTAKTE, "PAPERLESS_WEBHOOK_TOKEN", WEBHOOK_TOKEN)
    monkeypatch.setattr(services, "get_client", lambda: fake)
    store.set("paperless.base_url", PAPERLESS_URL, user=admin_user, reason="Test")
    store.set("paperless.enabled", True, user=admin_user, reason="Test")
    store.set("paperless.mode", "pilot", user=admin_user, reason="Test")
    store.set("paperless.pilot_object_numbers", [objekt.object_number], user=admin_user, reason="Test")
    from apps.sync.paperless import setup

    state = setup.check(user=admin_user, create=True)
    assert state.ok, state.message
    fake.reset_calls()
    return fake


@pytest.fixture
def eingang(paperless, drive, admin_user):
    """Eingangsobjekt und Eingangsordner in Drive eingerichtet."""
    from apps.sync import inbox

    inbox.ensure_inbox_folder(drive, user=admin_user)
    return inbox.get_inbox_object()


@pytest.fixture
def run_all():
    from apps.pipeline.local import run_pending_jobs

    return run_pending_jobs


def run_ops(*, include_deferred: bool = True) -> list[dict]:
    """Fuehrt alle wartenden Operationen lokal aus; Wartezeiten (Defer, Retry) werden auf faellig gesetzt."""
    if include_deferred:
        SyncOperation.objects.filter(status=OperationStatus.PENDING).update(next_attempt_at=None)
    return operations.run_pending()


@pytest.fixture
def ops():
    return run_ops
