"""Messlauf perf_run mit einem kleinen synthetischen Korpus im lokalen Modus (Entwicklungsumgebung); OCR als Fake."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from django.core.management import call_command

from apps.documents.models import Document
from apps.objects.models import ManagedObject

pytestmark = pytest.mark.django_db
GENERATOR = Path(__file__).resolve().parents[2] / "performance" / "corpus_generator.py"


def test_generator_und_perf_run_lokal(seeded, data_dir, tmp_path, fake_ocr, drive):
    from .conftest import reconcile

    messobjekt = ManagedObject.objects.create(
        object_number="700",
        name="Messobjekt",
        management_type="weg",
        is_test=True,
        city="Musterstadt",
        street="Testallee",
        house_number="100",
    )
    reconcile(messobjekt, drive)
    corpus = tmp_path / "korpus"
    subprocess.run(
        [
            sys.executable,
            str(GENERATOR),
            "--out",
            str(corpus),
            "--pages",
            "10",
            "--scan-share",
            "0.5",
            "--dpi",
            "80",
            "--gesamt-share",
            "1.0",
            "--gesamt-units",
            "3",
        ],
        check=True,
        capture_output=True,
    )
    manifest = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["pages_scan"] == 5 and manifest["pages_digital"] == 5
    gesamt = [d for d in manifest["documents"] if d["segments"]]
    assert gesamt, "Gesamtabrechnung mit eingebetteten Einzelabrechnungen erwartet"
    assert gesamt[0]["segments"][0]["page_from"] == 2
    out = tmp_path / "protokoll.json"
    call_command("perf_run", corpus=str(corpus), object="700", local=True, out=str(out))
    report = json.loads(out.read_text(encoding="utf-8"))
    summary = report["zusammenfassung"]
    assert summary["status"] == "done"
    assert summary["dateien"] == len(manifest["documents"])
    assert summary["seiten_gesamt"] == 10 and summary["seiten_ocr"] == 5 and summary["seiten_textebene"] == 5
    assert summary["platte_bytes"]["ocr_cache"] > 0 and summary["platte_bytes"]["previews"] > 0
    assert {r["job_type"] for r in report["je_jobtyp"]} >= {
        "hash",
        "analyze_pages",
        "ocr_chunk",
        "merge_pages",
    }
    obj = ManagedObject.objects.get(object_number="700")
    assert obj.is_test
    assert Document.objects.filter(object=obj, status__in=["review", "classified", "filed"]).count() == len(
        manifest["documents"]
    )
