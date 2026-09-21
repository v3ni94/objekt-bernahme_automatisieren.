"""paperless_feld_abgleich: Das Feld MHV Objekt wurde in Paperless nach der Uebernahme geleert oder auf ein anderes
Objekt gesetzt (Pilot 82, 22.09.2026: Fahrtkostenabrechnung mit der Objektadresse als Fahrtziel). Vorschau meldet die
Abweichung, --echt uebernimmt das Dokument in das Eingangsobjekt beziehungsweise das andere Objekt."""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command
from tests.integration.sync.conftest import pdf_bytes

from apps.documents import ingest
from apps.documents.models import Document
from apps.sync import services
from apps.sync.flows import assign
from apps.sync.models import AssignmentExample, ExampleKind, ExternalLink, LinkRole, SyncSystem

pytestmark = pytest.mark.django_db


def _feld_id() -> int:
    return int(services.connection_meta()["field_ids"]["object"])


def _remote_id(doc) -> int:
    return int(
        ExternalLink.objects.get(
            document=doc, system=SyncSystem.PAPERLESS, role=LinkRole.ORIGINAL
        ).external_id
    )


def _lauf(*args) -> str:
    out = StringIO()
    call_command("paperless_feld_abgleich", *args, stdout=out)
    return out.getvalue()


def test_geleertes_feld_fuehrt_in_das_eingangsobjekt(
    objekt, anderes_objekt, eingang, paperless, run_all, ops
):
    a, _ = ingest.ingest_upload(objekt, filename="Fahrtkosten.pdf", data=pdf_bytes(["Fahrtkosten Shalomweg"]))
    b, _ = ingest.ingest_upload(objekt, filename="Rechnung.pdf", data=pdf_bytes(["Rechnung Objekt"]))
    c, _ = ingest.ingest_upload(objekt, filename="Fremd.pdf", data=pdf_bytes(["Anderes Objekt"]))
    run_all(objekt)
    ops()
    paperless.process_tasks()
    ops()
    fid = _feld_id()
    # a: Feld geleert; b: unveraendert; c: auf das andere Objekt gesetzt
    paperless.bulk_edit(
        [_remote_id(a)], "modify_custom_fields", {"add_custom_fields": {}, "remove_custom_fields": [fid]}
    )
    paperless.set_custom_field_values(_remote_id(c), {fid: anderes_objekt.object_number})

    vorschau = _lauf(objekt.object_number)
    assert "leer 1" in vorschau and "anderes_objekt 1" in vorschau and "ok 1" in vorschau
    assert f"Dok {a.pk}" in vorschau and f"Dok {c.pk}" in vorschau and f"Dok {b.pk}" not in vorschau
    assert "Vorschau, nichts geändert" in vorschau
    a.refresh_from_db()
    assert a.status != "moved_out"

    echt = _lauf(objekt.object_number, "--echt")
    assert "Übernommen: 2" in echt
    for doc in (a, c):
        doc.refresh_from_db()
        assert doc.status == "moved_out" and doc.duplicate_of_id is not None
    neu_a = Document.objects.get(pk=a.duplicate_of_id)
    neu_c = Document.objects.get(pk=c.duplicate_of_id)
    assert neu_a.object_id == eingang.pk and neu_a.source == "moved_in"
    assert neu_c.object_id == anderes_objekt.pk
    b.refresh_from_db()
    assert b.object_id == objekt.pk and b.status != "moved_out"
    # zweiter Lauf findet nichts mehr: die alten Zeilen sind moved_out, die neuen gehoeren anderen Objekten
    befund = _lauf(objekt.object_number).split("Vorschau")[0]
    assert "ok 1" in befund and "leer 1" not in befund and "anderes_objekt 1" not in befund
    # Lernbeispiele: geleertes Feld = Ablehnung des alten Objekts, andere Nummer = Korrektur
    assert AssignmentExample.objects.filter(
        document=neu_a, kind=ExampleKind.REJECT, previous_object=objekt, proposed_object=objekt
    ).exists()
    assert AssignmentExample.objects.filter(
        document=neu_c, kind=ExampleKind.CORRECT, previous_object=objekt, target_object=anderes_objekt
    ).exists()
    # Die Inhaltszuordnung im Eingang darf das verworfene Objekt wegen desselben Adresstreffers nicht sofort wieder
    # automatisch waehlen: gleicher Text, ohne Ablehnung auto, mit Ablehnung nur Vorschlag
    text = "Fahrtkostenabrechnung\nFahrtziel: Musterstraße 49, 12345 Musterstadt\nObjekt 623"
    kontrolle, _ = assign.build_proposal(b, text=text)
    assert kontrolle.decision == "auto" and kontrolle.chosen.object_id == objekt.pk
    vorschlag, _ = assign.build_proposal(neu_a, text=text)
    assert vorschlag.decision == "review" and vorschlag.chosen.object_id == objekt.pk
    assert any("manuell verworfen" in r for r in vorschlag.reasons)
    assert "automatische Zuordnung" not in vorschlag.reasons


def test_unbekannte_nummer_wird_nur_gemeldet(objekt, eingang, paperless, run_all, ops):
    a, _ = ingest.ingest_upload(objekt, filename="Unbekannt.pdf", data=pdf_bytes(["Objekt 999"]))
    run_all(objekt)
    ops()
    paperless.process_tasks()
    ops()
    paperless.set_custom_field_values(_remote_id(a), {_feld_id(): "999"})
    echt = _lauf(objekt.object_number, "--echt")
    assert "unbekannt 1" in echt and "Übernommen: 0" in echt
    a.refresh_from_db()
    assert a.status != "moved_out"
