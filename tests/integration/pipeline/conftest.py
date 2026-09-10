"""Fixtures fuer die Pipeline-Tests: eigenes Datenverzeichnis, Testobjekt mit Stammdaten, synthetische PDFs."""

from __future__ import annotations

import random
from datetime import date
from pathlib import Path

import pytest
from django.conf import settings

from apps.objects.models import ManagedObject, Unit
from apps.parties import services as party_services
from apps.parties.models import Owner

TEST_IBAN = "DE89 3704 0044 0532 0130 00"  # veroeffentlichte Beispiel-IBAN, kein reales Konto


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    path = tmp_path / "data"
    path.mkdir()
    monkeypatch.setitem(settings.OBJEKTAKTE, "DATA_DIR", path)
    monkeypatch.setitem(settings.OBJEKTAKTE, "DISK_RESERVE_GB", 0)
    return path


@pytest.fixture
def objekt(seeded, data_dir):
    return ManagedObject.objects.create(
        object_number="623",
        name="Musterstadt, Musterstraße 49",
        street="Musterstraße",
        house_number="49",
        city="Musterstadt",
        management_type="weg",
        is_test=True,
    )


@pytest.fixture
def stammdaten(objekt):
    we1 = Unit.objects.create(
        object=objekt, unit_label="WE 1", unit_label_normalized="WE1", unit_number="1", unit_type="apartment"
    )
    we14 = Unit.objects.create(
        object=objekt,
        unit_label="WE 14",
        unit_label_normalized="WE14",
        unit_number="14",
        unit_type="apartment",
    )
    owner = Owner.objects.create(
        type="natural_person",
        first_name="Erika",
        last_name="Mustermann",
        search_name="MUSTERMANN ERIKA",
        **party_services.iban_fields(TEST_IBAN),
    )
    party_services.create_assignment(owner=owner, unit=we14, valid_from=date(2020, 1, 1), valid_to=None)
    other = ManagedObject.objects.create(
        object_number="624",
        name="Beispielhausen, Beispielweg 7",
        street="Beispielweg",
        house_number="7",
        city="Beispielhausen",
        management_type="weg",
        is_test=True,
    )
    return {"we1": we1, "we14": we14, "owner": owner, "other": other}


def write_pdf(path: Path, pages: list[list[str]]) -> Path:
    from corpus_generator import write_digital_pdf

    write_digital_pdf(path, pages)
    return path


def write_scan(path: Path, pages: list[list[str]], dpi: int = 120) -> Path:
    from corpus_generator import write_digital_pdf, write_scan_pdf

    digital = path.with_suffix(".digital.pdf")
    write_digital_pdf(digital, pages)
    write_scan_pdf(digital, path, dpi, False, random.Random(1))
    digital.unlink()
    return path


def page_lines(marker: str, no: int, total: int, extra: list[str] | None = None) -> list[str]:
    lines = [
        "Objekt 623 Musterstadt, Musterstraße 49",
        f"Testdokument {marker}",
        "",
        f"Dies ist die Seite {marker} des synthetischen Testdokuments für die Pipeline.",
        "Alle Namen und Zahlen sind erfunden. Keine realen Personen, keine realen Konten.",
    ]
    lines.extend(extra or [])
    lines.append(f"Seite {no} von {total}")
    return lines


@pytest.fixture
def pdf_factory(tmp_path):
    def _make(name: str, texts: list[list[str]], scan: bool = False) -> Path:
        path = tmp_path / name
        return write_scan(path, texts) if scan else write_pdf(path, texts)

    return _make


@pytest.fixture
def run_all():
    from apps.pipeline.local import run_pending_jobs

    return run_pending_jobs


@pytest.fixture
def fake_ocr(monkeypatch):
    """Ersetzt ocrmypdf durch einen Fake, der je Seite einen bekannten Text liefert (Kettenlogik ohne Tesseract)."""
    from apps.pipeline import ocr as ocr_mod
    from apps.pipeline import tasks

    calls: list[dict] = []

    def _fake(source_pdf, sha256, pages, chunk_no, work, *, language, hmac_key, heartbeat=None):
        calls.append({"pages": list(pages), "chunk_no": chunk_no})
        chars = 0
        hits = 0
        for i, p in enumerate(pages):
            text = f"FAKE-OCR Seite {p} Block {chunk_no}\nKontoinhaber Erika Mustermann\nIBAN {TEST_IBAN}"
            c, h = ocr_mod.mask_and_cache(sha256, p, text, source="ocr", hmac_key=hmac_key)
            chars += c
            hits += h
            if heartbeat:
                heartbeat(i + 1)
        return ocr_mod.OcrResult(list(pages), chars, hits, 0.01)

    monkeypatch.setattr(tasks.ocr_mod, "ocr_chunk", _fake)
    monkeypatch.setattr(tasks.ocr_mod, "tools_available", lambda: True)
    return calls
