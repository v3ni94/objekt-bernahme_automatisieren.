"""Restformate bereinigen (Entscheidungsvorlage E-1, 26.09.2026): Temporaerdateien ohne erkennbaren Inhalt gehen in
den Drive-Papierkorb (nie endgueltig, Dokument als geloescht markiert), Archive und Medien ungelesen nach 06/02
Manuelle Pruefung ueber den Ablagejob. Vorschau aendert nichts, der echte Lauf ist idempotent, Faelle ohne
content_checked bleiben unangetastet, ein Fehler je Dokument bricht den Lauf nicht ab."""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.documents.models import Document
from apps.drive.adapter import DriveChange, TransientError
from apps.drive.folders import ensure_category_folder
from apps.pipeline.models import JobStatus, JobType, ProcessingJob
from apps.review.models import CaseStatus, CaseType, ReviewCase
from apps.sync import drive_changes
from apps.sync.models import ExternalLink, LinkRole, LinkState, SyncSystem

pytestmark = pytest.mark.django_db


def _drive_dokument(
    objekt, drive, name: str, *, mime: str = "application/octet-stream", content=b"\x00\x01"
) -> Document:
    """Bestandsdatei aus Drive im Objektordner, Dokument in der Pruefung (Zustand nach analyze_pages)."""
    file_id = drive.add_file(objekt.drive_root_folder_id, name, content, mime_type=mime)
    return Document.objects.create(
        object=objekt,
        size_bytes=len(content),
        mime_type=mime,
        original_name=name,
        current_name=name,
        source="drive_existing",
        drive_file_id=file_id,
        sha256=f"{abs(hash((objekt.pk, name))):064x}"[:64],
        status="review",
        first_seen_at=timezone.now(),
    )


def _fall(
    objekt,
    doc: Document,
    *,
    content_checked: bool | None = True,
    subtype: str = "unsupported_format",
    **extra,
) -> ReviewCase:
    """Fall wie ihn analyze_pages anlegt: content_recognized=False (Inhaltspruefung ohne Signatur), sofern der Test
    nichts anderes vorgibt; extra ueberschreibt oder ergaenzt Kontextfelder (None entfernt ein Feld)."""
    context = {"mime_type": doc.mime_type, "name": doc.current_name, "kind": "unsupported"}
    if content_checked is not None:
        context["content_checked"] = content_checked
        context["detected_kind"] = "unsupported"
        context["content_recognized"] = False
        context["content_suffix"] = None
        context["note"] = "Format nicht unterstützt, manuelle Prüfung (Inhalt unbekannt)"
    for feld, wert in extra.items():
        if wert is None:
            context.pop(feld, None)
        else:
            context[feld] = wert
    return ReviewCase.objects.create(
        object=objekt,
        case_type=CaseType.UNCLEAR,
        case_subtype=subtype,
        document=doc,
        batch_key=f"{subtype}:{doc.pk}",
        priority=80,
        context=context,
    )


def _lauf(*args) -> str:
    out = StringIO()
    call_command("restformate_bereinigen", *args, stdout=out)
    return out.getvalue()


# ---------------------------------------------------------------- Gruppe temporaer


def test_temporaer_vorschau_aendert_nichts(objekt, stammdaten, drive):
    tmp = _drive_dokument(objekt, drive, "Vertrag_Geheim.tmp")
    _fall(objekt, tmp)
    ohne = _drive_dokument(objekt, drive, "Abrechnung_Geheim")
    _fall(objekt, ohne)
    ungeprueft = _drive_dokument(objekt, drive, "Alt.indir")
    _fall(objekt, ungeprueft, content_checked=None)
    archiv = _drive_dokument(objekt, drive, "Archiv_Geheim.zip", mime="application/zip")
    _fall(objekt, archiv)
    ops_vorher = len(drive.ops)

    text = _lauf("--gruppe", "temporaer")

    assert "Temporaerdateien ohne erkennbaren Inhalt (Papierkorb): 2" in text
    assert "Nach Endung: .tmp 1, (ohne) 1" in text or "Nach Endung: (ohne) 1, .tmp 1" in text
    assert f"Objekt {objekt.object_number}: 2 (" in text
    assert "Inhalt noch ungeprueft (formate_wiederaufnehmen --gruppe temporaer) 1" in text
    assert "andere Endung 1" in text
    assert "Vorschau: 2 Faelle, nichts geaendert" in text and "30 Tagen" in text
    assert "Geheim" not in text and "Alt.indir" not in text
    assert not [op for op in drive.ops[ops_vorher:] if op[0] == "trash"]
    for doc in (tmp, ohne, ungeprueft, archiv):
        doc.refresh_from_db()
        assert doc.deleted_at is None and doc.status == "review"
        assert not drive.get(doc.drive_file_id).trashed
    assert ReviewCase.objects.filter(status=CaseStatus.OPEN, case_subtype="unsupported_format").count() == 4


def test_temporaer_echt_papierkorb_idempotent(objekt, stammdaten, drive):
    tmp = _drive_dokument(objekt, drive, "Vertrag_Geheim.tmp")
    fall_tmp = _fall(objekt, tmp)
    ExternalLink.objects.create(
        document=tmp,
        system=SyncSystem.DRIVE,
        role=LinkRole.ORIGINAL,
        external_id=tmp.drive_file_id,
        state=LinkState.SYNCED,
    )
    ohne = _drive_dokument(objekt, drive, "Abrechnung_Geheim")
    fall_ohne = _fall(objekt, ohne)
    ungeprueft = _drive_dokument(objekt, drive, "Alt.herunterladen")
    fall_ungeprueft = _fall(objekt, ungeprueft, content_checked=False)
    elternordner = drive.get(tmp.drive_file_id).parent_id

    text = _lauf("--gruppe", "temporaer", "--objekt", objekt.object_number, "--echt")

    assert "Ergebnis: in den Papierkorb gelegt 2" in text and "30 Tagen" in text
    assert "Geheim" not in text
    for doc, fall in ((tmp, fall_tmp), (ohne, fall_ohne)):
        doc.refresh_from_db()
        fall.refresh_from_db()
        # Papierkorb sichtbar als trashed=True, Datei existiert weiter (nie endgueltig)
        node = drive.get(doc.drive_file_id)
        assert node is not None and node.trashed
        assert doc.deleted_at is not None and "restformate_bereinigen" in doc.delete_reason
        assert fall.status == CaseStatus.RESOLVED
        assert fall.resolution["action"] == "trashed_temp_file"
        assert fall.resolution["reason"] == "restformate_bereinigen"
        ereignis = AuditEvent.objects.get(action="pipeline.temp_file_trashed", entity_id=doc.pk)
        assert ereignis.object_id == objekt.pk
        assert ereignis.after_state["drive_file_id"] == doc.drive_file_id
        assert ereignis.after_state["parent"] == elternordner
        assert ereignis.after_state["drive"] == "in den Papierkorb gelegt"
    assert not [op for op in drive.ops if op[0] == "delete_permanently"]
    link = ExternalLink.objects.get(document=tmp)
    assert link.state == LinkState.TRASHED and "restformate_bereinigen" in link.state_reason
    # Fall ohne content_checked bleibt unangetastet
    ungeprueft.refresh_from_db()
    fall_ungeprueft.refresh_from_db()
    assert ungeprueft.deleted_at is None and not drive.get(ungeprueft.drive_file_id).trashed
    assert fall_ungeprueft.status == CaseStatus.OPEN
    assert not AuditEvent.objects.filter(
        action="pipeline.temp_file_trashed", entity_id=ungeprueft.pk
    ).exists()

    # zweiter Lauf findet nichts mehr und legt nichts erneut in den Papierkorb
    trash_vorher = len([op for op in drive.ops if op[0] == "trash"])
    text = _lauf("--gruppe", "temporaer", "--echt")
    assert "Temporaerdateien ohne erkennbaren Inhalt (Papierkorb): 0" in text
    assert "Keine offenen Faelle in der Gruppe temporaer." in text
    assert len([op for op in drive.ops if op[0] == "trash"]) == trash_vorher
    assert AuditEvent.objects.filter(action="pipeline.temp_file_trashed").count() == 2


def test_temporaer_papierkorb_erzeugt_keinen_abgleichskonflikt(objekt, stammdaten, drive):
    """Das Drive-Aenderungsprotokoll meldet die Papierkorb-Datei eines als geloescht markierten Dokuments nicht als
    Konflikt (drive_trashed): der Papierkorb ist die gewollte Wirkung des Kommandos."""
    tmp = _drive_dokument(objekt, drive, "Rest.tmp")
    _fall(objekt, tmp)
    ExternalLink.objects.create(
        document=tmp,
        system=SyncSystem.DRIVE,
        role=LinkRole.ORIGINAL,
        external_id=tmp.drive_file_id,
        state=LinkState.SYNCED,
    )
    _lauf("--gruppe", "temporaer", "--echt")
    node = drive.get(tmp.drive_file_id)
    assert node.trashed
    counters = {"trashed_known": 0, "trashed_unknown": 0}
    change = DriveChange(file_id=tmp.drive_file_id, removed=False, time="2026-09-26T10:00:00Z", node=node)
    drive_changes._handle_change(change, drive_changes.Resolver(drive), counters)
    assert counters["trashed_known"] == 1
    assert not ReviewCase.objects.filter(document=tmp, case_type=CaseType.SYNC_CONFLICT).exists()


def test_temporaer_papierkorb_geleert_erzeugt_keinen_abgleichskonflikt(objekt, stammdaten, drive):
    """Leert Google oder ein Nutzer den Papierkorb, meldet das Aenderungsprotokoll removed=True ohne Knoten; fuer ein
    als geloescht markiertes Dokument entsteht kein Konflikt drive_removed, die Verknuepfung wird als bestaetigte
    Loeschung geschlossen (tombstone)."""
    tmp = _drive_dokument(objekt, drive, "Rest.tmp")
    _fall(objekt, tmp)
    ExternalLink.objects.create(
        document=tmp,
        system=SyncSystem.DRIVE,
        role=LinkRole.ORIGINAL,
        external_id=tmp.drive_file_id,
        state=LinkState.SYNCED,
    )
    _lauf("--gruppe", "temporaer", "--echt")
    drive.delete_permanently(tmp.drive_file_id)
    counters = {"removed_known": 0, "removed_unknown": 0}
    change = DriveChange(file_id=tmp.drive_file_id, removed=True, time="2026-10-27T10:00:00Z", node=None)
    drive_changes._handle_change(change, drive_changes.Resolver(drive), counters)
    assert counters["removed_known"] == 1
    assert not ReviewCase.objects.filter(document=tmp, case_type=CaseType.SYNC_CONFLICT).exists()
    link = ExternalLink.objects.get(document=tmp)
    assert link.state == LinkState.TOMBSTONE and "endgueltig" in link.state_reason
    tmp.refresh_from_db()
    assert tmp.deleted_at is not None


def test_temporaer_wiederherstellung_in_drive_belebt_dokument_wieder(objekt, stammdaten, drive):
    """Stellt ein Nutzer die Datei innerhalb der 30 Tage aus dem Papierkorb wieder her, belebt das Aenderungsprotokoll
    das Dokument wieder (Loeschkennzeichen zurueck, Status registriert, Verknuepfung abgeglichen, Job discover) statt
    an der Neuregistrierung (Unique-Constraint object, drive_file_id) zu scheitern."""
    tmp = _drive_dokument(objekt, drive, "Rest.tmp")
    fall = _fall(objekt, tmp)
    ExternalLink.objects.create(
        document=tmp,
        system=SyncSystem.DRIVE,
        role=LinkRole.ORIGINAL,
        external_id=tmp.drive_file_id,
        state=LinkState.SYNCED,
    )
    _lauf("--gruppe", "temporaer", "--echt")
    tmp.refresh_from_db()
    assert tmp.deleted_at is not None
    drive.restore(tmp.drive_file_id)
    node = drive.get(tmp.drive_file_id)
    assert not node.trashed
    counters = {"unchanged_known": 0, "changed_known": 0, "registered": 0}
    change = DriveChange(file_id=tmp.drive_file_id, removed=False, time="2026-09-27T10:00:00Z", node=node)
    drive_changes._handle_change(change, drive_changes.Resolver(drive), counters)
    assert counters["restored"] == 1 and counters["registered"] == 0
    tmp.refresh_from_db()
    fall.refresh_from_db()
    assert tmp.deleted_at is None and tmp.delete_reason is None and tmp.status == "registered"
    assert Document.objects.filter(object=objekt, drive_file_id=tmp.drive_file_id).count() == 1
    assert (
        fall.status == CaseStatus.RESOLVED
    )  # der erledigte Fall bleibt erledigt, die Kette legt bei Bedarf neu an
    link = ExternalLink.objects.get(document=tmp)
    assert link.state == LinkState.SYNCED and "wiederhergestellt" in link.state_reason
    assert link.parent_external_id == node.parent_id
    job = ProcessingJob.objects.get(document=tmp, job_type=JobType.DISCOVER)
    assert job.status == JobStatus.PENDING and job.payload["restored"] is True
    ereignis = AuditEvent.objects.get(action="sync.drive_restored", entity_id=tmp.pk)
    assert (
        ereignis.object_id == objekt.pk and "restformate_bereinigen" in ereignis.before_state["delete_reason"]
    )
    assert not ReviewCase.objects.filter(document=tmp, case_type=CaseType.SYNC_CONFLICT).exists()
    # ein zweiter Durchlauf derselben Aenderung ist ein normaler unveraenderter Treffer
    drive_changes._handle_change(change, drive_changes.Resolver(drive), counters)
    assert counters["restored"] == 1 and counters["unchanged_known"] == 1


def test_temporaer_erkannter_inhalt_bleibt_offen(objekt, stammdaten, drive):
    """content_checked allein reicht nicht: eine erkannte Signatur (Office-Altformat mit gescheiterter Umwandlung,
    Praesentation, ZIP-Archiv, Office-Paket ohne Hauptteil) ist keine Temporaerdatei und bleibt im Pruefcenter;
    Altfaelle ohne content_recognized gelten nur mit der Notiz „(Inhalt unbekannt)" als unerkannt."""
    office = _drive_dokument(objekt, drive, "Protokoll.tmp")
    fall_office = _fall(
        objekt,
        office,
        detected_kind="office_legacy",
        content_recognized=True,
        content_suffix=".doc",
        note="Umwandlung fehlgeschlagen (Zeitueberschreitung)",
    )
    praesentation = _drive_dokument(objekt, drive, "Bericht.tmp")
    fall_praesentation = _fall(
        objekt,
        praesentation,
        content_recognized=True,
        content_suffix=".ppt",
        note="Format nicht unterstützt, manuelle Prüfung (Inhalt: ppt)",
    )
    archiv = _drive_dokument(objekt, drive, "Fotos.tmp")
    fall_archiv = _fall(
        objekt,
        archiv,
        content_recognized=True,
        content_suffix=".zip",
        note="Format nicht unterstützt, manuelle Prüfung (Inhalt: zip)",
    )
    # Altfaelle vor dem Feld content_recognized: Office-Paket ohne Hauptteil („Inhalt: unbekannt") bleibt offen,
    # sniff ohne Ergebnis („Inhalt unbekannt") gehoert zur Gruppe
    paket_alt = _drive_dokument(objekt, drive, "Paket.tmp")
    fall_paket_alt = _fall(
        objekt,
        paket_alt,
        content_recognized=None,
        content_suffix=None,
        note="Format nicht unterstützt, manuelle Prüfung (Inhalt: unbekannt)",
    )
    rest_alt = _drive_dokument(objekt, drive, "Rest.tmp")
    fall_rest_alt = _fall(objekt, rest_alt, content_recognized=None, content_suffix=None)

    text = _lauf("--gruppe", "temporaer", "--echt")

    assert "Temporaerdateien ohne erkennbaren Inhalt (Papierkorb): 1" in text
    assert "Inhalt erkannt: office_legacy (formate_wiederaufnehmen --gruppe office) 1" in text
    assert "Inhalt erkannt: ppt, keine Temporaerdatei 1" in text
    assert "Inhalt erkannt: zip, keine Temporaerdatei 1" in text
    assert "Inhalt erkannt: unbekannt, keine Temporaerdatei 1" in text
    assert "Ergebnis: in den Papierkorb gelegt 1" in text
    for doc, fall in (
        (office, fall_office),
        (praesentation, fall_praesentation),
        (archiv, fall_archiv),
        (paket_alt, fall_paket_alt),
    ):
        doc.refresh_from_db()
        fall.refresh_from_db()
        assert doc.deleted_at is None and fall.status == CaseStatus.OPEN
        assert not drive.get(doc.drive_file_id).trashed
    rest_alt.refresh_from_db()
    fall_rest_alt.refresh_from_db()
    assert rest_alt.deleted_at is not None and fall_rest_alt.status == CaseStatus.RESOLVED
    assert drive.get(rest_alt.drive_file_id).trashed
    assert AuditEvent.objects.filter(action="pipeline.temp_file_trashed").count() == 1


def test_temporaer_drive_datei_fehlt_und_google_dokument(objekt, stammdaten, drive):
    """Fehlt die Drive-Datei schon (endgueltig entfernt), wird nur das Dokument als geloescht markiert; ein
    Google-Dokument mit Temporaer-Endung gehoert nicht zur Gruppe."""
    fehlt = _drive_dokument(objekt, drive, "Weg.tmp")
    fall_fehlt = _fall(objekt, fehlt)
    Document.objects.filter(pk=fehlt.pk).update(drive_file_id="unbekannt-000")
    gdoc = _drive_dokument(objekt, drive, "Notiz", mime="application/vnd.google-apps.document")
    fall_gdoc = _fall(objekt, gdoc)

    text = _lauf("--gruppe", "temporaer", "--echt")

    assert "Temporaerdateien ohne erkennbaren Inhalt (Papierkorb): 1" in text
    assert "Google-Dokument 1" in text
    assert "Ergebnis: Drive-Datei fehlte bereits 1" in text
    assert not [op for op in drive.ops if op[0] == "trash"]
    fehlt.refresh_from_db()
    fall_fehlt.refresh_from_db()
    assert fehlt.deleted_at is not None and fall_fehlt.status == CaseStatus.RESOLVED
    assert fall_fehlt.resolution["drive"] == "Drive-Datei fehlte bereits"
    ereignis = AuditEvent.objects.get(action="pipeline.temp_file_trashed", entity_id=fehlt.pk)
    assert (
        ereignis.after_state["parent"] is None
        and ereignis.after_state["drive"] == "Drive-Datei fehlte bereits"
    )
    gdoc.refresh_from_db()
    fall_gdoc.refresh_from_db()
    assert gdoc.deleted_at is None and fall_gdoc.status == CaseStatus.OPEN
    assert not drive.get(gdoc.drive_file_id).trashed


def test_temporaer_fehler_je_dokument_bricht_lauf_nicht_ab(objekt, stammdaten, drive):
    erstes = _drive_dokument(objekt, drive, "Erstes.tmp")
    fall_erstes = _fall(objekt, erstes)
    zweites = _drive_dokument(objekt, drive, "Zweites.part")
    fall_zweites = _fall(objekt, zweites)
    bereits = _drive_dokument(objekt, drive, "Schon.crdownload")
    fall_bereits = _fall(objekt, bereits)
    drive.set_trashed(bereits.drive_file_id, True)  # von Hand schon im Papierkorb
    drive.inject("trash", TransientError("Drive nicht erreichbar", status=503))

    text = _lauf("--gruppe", "temporaer", "--echt")

    assert f"Fehler bei Dokument {erstes.pk}: TransientError" in text
    assert "Fehler 1" in text and "in den Papierkorb gelegt 1" in text and "bereits im Papierkorb 1" in text
    erstes.refresh_from_db()
    fall_erstes.refresh_from_db()
    assert erstes.deleted_at is None and fall_erstes.status == CaseStatus.OPEN
    assert not drive.get(erstes.drive_file_id).trashed
    zweites.refresh_from_db()
    fall_zweites.refresh_from_db()
    assert zweites.deleted_at is not None and fall_zweites.status == CaseStatus.RESOLVED
    bereits.refresh_from_db()
    fall_bereits.refresh_from_db()
    assert bereits.deleted_at is not None and fall_bereits.resolution["drive"] == "bereits im Papierkorb"

    # naechster Lauf nimmt den fehlgeschlagenen Fall erneut auf
    text = _lauf("--gruppe", "temporaer", "--echt")
    assert "Ergebnis: in den Papierkorb gelegt 1" in text
    erstes.refresh_from_db()
    assert erstes.deleted_at is not None and drive.get(erstes.drive_file_id).trashed


def test_temporaer_ohne_drive_datei_bleibt_in_der_pruefung(objekt, stammdaten, drive):
    upload = Document.objects.create(
        object=objekt,
        size_bytes=2,
        mime_type="application/octet-stream",
        original_name="Upload.tmp",
        current_name="Upload.tmp",
        source="upload",
        status="review",
        first_seen_at=timezone.now(),
    )
    fall = _fall(objekt, upload)
    text = _lauf("--gruppe", "temporaer", "--echt")
    assert "ohne Drive-Datei, bleibt in der Pruefung 1" in text
    upload.refresh_from_db()
    fall.refresh_from_db()
    assert upload.deleted_at is None and fall.status == CaseStatus.OPEN


# ---------------------------------------------------------------- Gruppe archive-medien


def test_archive_medien_vorschau_und_ablage(objekt, stammdaten, drive, run_all):
    archiv = _drive_dokument(objekt, drive, "Fotos_Geheim.zip", mime="application/zip")
    fall_archiv = _fall(objekt, archiv)
    video = _drive_dokument(objekt, drive, "Begehung_Geheim.mp4", mime="video/mp4")
    fall_video = _fall(objekt, video)
    zu_gross = _drive_dokument(objekt, drive, "Riesig.zip", mime="application/zip")
    fall_zu_gross = _fall(objekt, zu_gross)
    _fall(objekt, zu_gross, content_checked=None, subtype="file_too_large")
    tmp = _drive_dokument(objekt, drive, "Rest.tmp")
    fall_tmp = _fall(objekt, tmp)
    anderes = _drive_dokument(stammdaten["other"], drive, "Anderes.mov", mime="video/quicktime")
    fall_anderes = _fall(stammdaten["other"], anderes)

    text = _lauf("--gruppe", "archive-medien")
    assert "Archive und Medien (Ablage nach 06/02 Manuelle Pruefung): 3" in text
    assert ".zip 1" in text and ".mp4 1" in text and ".mov 1" in text
    assert f"Objekt {objekt.object_number}: 2 (" in text
    assert f"Objekt {stammdaten['other'].object_number}: 1 (" in text
    assert "zu gross (Fall file_too_large) 1" in text and "andere Endung 1" in text
    assert "Vorschau: 3 Faelle, nichts geaendert" in text and "Geheim" not in text
    assert not ProcessingJob.objects.filter(job_type=JobType.FILE_TO_DRIVE).exists()
    archiv.refresh_from_db()
    assert archiv.status == "review" and archiv.category_id is None

    text = _lauf("--gruppe", "archive-medien", "--objekt", objekt.object_number, "--echt")
    assert "Ergebnis: zur Ablage nach 06/02 eingereiht 2" in text and "Geheim" not in text
    for doc, fall in ((archiv, fall_archiv), (video, fall_video)):
        doc.refresh_from_db()
        fall.refresh_from_db()
        assert doc.category_id == "06" and doc.subfolder.code == "02" and doc.document_type is None
        assert doc.status == "classified" and doc.deleted_at is None
        assert fall.status == CaseStatus.RESOLVED and fall.resolution["action"] == "filed_manual_check"
        assert fall.resolution["target"] == {"category": "06", "subfolder": "02"}
        job = ProcessingJob.objects.get(document=doc, job_type=JobType.FILE_TO_DRIVE)
        assert job.status == JobStatus.PENDING and job.payload == {"category": "06", "subfolder": "02"}
        ereignis = AuditEvent.objects.get(action="pipeline.manual_check_filed", entity_id=doc.pk)
        assert ereignis.after_state["target"]["subfolder"] == "02"
    # nicht ausgewaehlt: zu gross, andere Endung, anderes Objekt
    for doc, fall in ((zu_gross, fall_zu_gross), (tmp, fall_tmp), (anderes, fall_anderes)):
        doc.refresh_from_db()
        fall.refresh_from_db()
        assert doc.status == "review" and fall.status == CaseStatus.OPEN
        assert not ProcessingJob.objects.filter(document=doc, job_type=JobType.FILE_TO_DRIVE).exists()

    # Ablagejob wie im Bestand: Bestandsdatei wird nach 06/02 verschoben, kein Upload, nichts im Papierkorb
    uploads_vorher = len([op for op in drive.ops if op[0] == "upload"])
    run_all(objekt)
    ziel = ensure_category_folder(objekt, "06", "02", drive=drive)
    for doc in (archiv, video):
        doc.refresh_from_db()
        node = drive.get(doc.drive_file_id)
        assert node.parent_id == ziel.drive_file_id and not node.trashed
        assert doc.status == "filed" and doc.drive_node_id == ziel.pk
    assert len([op for op in drive.ops if op[0] == "upload"]) == uploads_vorher

    # zweiter Lauf: nur noch der Fall des anderen Objekts, im Objekt 623 nichts mehr
    text = _lauf("--gruppe", "archive-medien", "--objekt", objekt.object_number, "--echt")
    assert "Keine offenen Faelle in der Gruppe archive-medien." in text
    assert AuditEvent.objects.filter(action="pipeline.manual_check_filed").count() == 2


def test_archive_medien_ueberschreibt_wartenden_ablagejob(objekt, stammdaten, drive):
    """Ein wartender Ablagejob mit alter Zielangabe (etwa 03/01 aus einer frueheren Klassifikation) wird auf 06/02
    umgestellt statt von enqueue unveraendert wiederverwendet; danach wartet genau ein Job."""
    from apps.pipeline.jobs import enqueue, idempotency_key

    archiv = _drive_dokument(objekt, drive, "Belege.zip", mime="application/zip")
    fall = _fall(objekt, archiv)
    alt, _ = enqueue(
        JobType.FILE_TO_DRIVE,
        objekt,
        key=idempotency_key(JobType.FILE_TO_DRIVE, objekt.pk, archiv.sha256),
        document=archiv,
        payload={"category": "03", "subfolder": "01"},
    )
    assert alt.status == JobStatus.PENDING

    text = _lauf("--gruppe", "archive-medien", "--echt")

    assert "zur Ablage nach 06/02 eingereiht 1" in text
    jobs = list(ProcessingJob.objects.filter(document=archiv, job_type=JobType.FILE_TO_DRIVE))
    assert len(jobs) == 1 and jobs[0].pk == alt.pk
    assert jobs[0].status == JobStatus.PENDING and jobs[0].payload == {"category": "06", "subfolder": "02"}
    fall.refresh_from_db()
    archiv.refresh_from_db()
    assert fall.status == CaseStatus.RESOLVED and archiv.category_id == "06"


def test_archive_medien_limit(objekt, stammdaten, drive):
    for name in ("a.zip", "b.mp3", "c.wav"):
        _fall(objekt, _drive_dokument(objekt, drive, name))
    text = _lauf("--gruppe", "archive-medien", "--limit", "2")
    assert "Archive und Medien (Ablage nach 06/02 Manuelle Pruefung): 2" in text
    assert "Begrenzung 2 erreicht, 1 weitere Faelle nicht ausgewaehlt" in text
    text = _lauf("--gruppe", "archive-medien", "--limit", "2", "--echt")
    assert "zur Ablage nach 06/02 eingereiht 2" in text
    assert ReviewCase.objects.filter(status=CaseStatus.OPEN, case_subtype="unsupported_format").count() == 1
    text = _lauf("--gruppe", "archive-medien", "--echt")
    assert "zur Ablage nach 06/02 eingereiht 1" in text
    assert not ReviewCase.objects.filter(status=CaseStatus.OPEN, case_subtype="unsupported_format").exists()
