"""E-Mail-Mechanik fuer die Regel 06 Sonstiges (Vorlage E-4, 26.09.2026): Regel email_subject trifft und die Ablage
traegt den bereinigten Betreff als Titel; Absenderabgleich ordnet der Eigentuemer- bzw. Mieterakte zu (Schalter
email.match_sender_to_party); email.auto_misc legt E-Mails ohne Dokumentart ohne Fall nach 06/01 ab, auch
Bestandsdateien mit Umbenennung in Drive; Standard aus erzeugt weiter den Fall below_threshold."""

from __future__ import annotations

import hashlib
from email.message import EmailMessage

import pytest
from django.utils import timezone

from apps.config import store
from apps.documents.models import (
    Document,
    DocumentClassification,
    DocumentOwnerLink,
    DocumentPage,
    DocumentTenantLink,
)
from apps.drive.models import DriveNode as DriveNodeRow
from apps.pipeline import email_text, storage
from apps.pipeline.models import JobType, ProcessingJob
from apps.pipeline.runs import start_run
from apps.review.models import ReviewCase

pytestmark = pytest.mark.django_db
H623 = "Objekt 623 Düsseldorf, Joachimstraße 49\n"


def _eml(*, sender: str, subject: str, body: str) -> bytes:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = "verwaltung@example.test"
    msg["Subject"] = subject
    msg["Date"] = "Mon, 15 Sep 2026 09:15:00 +0200"
    msg.set_content(body)
    return bytes(msg)


def make_email_document(obj, data: bytes, filename: str, *, source="upload", drive=None, parent_id=None):
    """E-Mail im Status ocr_done wie nach analyze_pages und merge_pages: Original im Arbeitsverzeichnis, eine
    maskierte Textseite (Quelle office), Kopfzeilen werden erst in build_context gelesen."""
    from apps.parties.services import _hmac_key
    from apps.pipeline import ocr as ocr_mod

    sha = hashlib.sha256(data).hexdigest()
    drive_file_id = drive_node = None
    if source == "drive_existing":
        drive_file_id = drive.add_file(parent_id, filename, data, "message/rfc822")
        drive_node = DriveNodeRow.objects.filter(drive_file_id=parent_id).first()
    work = storage.work_dir(sha)
    work.mkdir(parents=True, exist_ok=True)
    (work / "original.eml").write_bytes(data)
    (text,) = email_text.extract_email_text(work / "original.eml")
    ocr_mod.mask_and_cache(sha, 1, text, source="office", hmac_key=_hmac_key())
    masked = storage.read_page(sha, 1)[0]
    doc = Document.objects.create(
        object=obj,
        sha256=sha,
        size_bytes=len(data),
        mime_type="message/rfc822",
        original_name=filename,
        current_name=filename,
        source=source,
        drive_file_id=drive_file_id,
        drive_node=drive_node,
        page_count=1,
        origin_kind="digital",
        ocr_cache_key=sha,
        status="ocr_done",
        first_seen_at=timezone.now(),
    )
    DocumentPage.objects.create(
        document=doc,
        page_no=1,
        text_source="text_layer",
        is_scan=False,
        text_content=masked,
        text_hash=hashlib.sha256(masked.encode()).hexdigest(),
        char_count=len(masked),
        word_count=len(masked.split()),
    )
    return doc


def _run(welt, run_all, number="623"):
    run = start_run(welt["objects"][number])
    run_all(welt["objects"][number])
    return run


def _final(doc) -> DocumentClassification:
    return DocumentClassification.objects.get(document=doc, is_final=True)


UNKLAR = H623 + "Hallo,\n\nwie besprochen melde ich mich noch einmal. Viele Grüße"


def test_regel_email_subject_trifft_und_titel_ist_betreff(welt, fake_oauth, run_all):
    obj = welt["objects"]["623"]
    data = _eml(
        sender="Handwerk Beispiel GmbH <buchhaltung@example.test>",
        subject="AW: Rechnung Nr. 2026-0417 / Dachrinne",
        body=H623
        + "Sehr geehrte Damen und Herren,\n\nanbei unsere Rechnung. Rechnungsbetrag 1.234,56 EUR brutto, "
        "zahlbar bis 15.10.2026.\n\nMit freundlichen Grüßen",
    )
    doc = make_email_document(obj, data, "Nachricht 4711.eml")
    _run(welt, run_all)
    doc.refresh_from_db()
    stufe1 = DocumentClassification.objects.get(document=doc, stage=1, provider="rules", is_final=False)
    assert stufe1.reasoning.split(", ")[0] == "R-03-EMAIL-RECHNUNG-001"  # bester Treffer zuerst
    assert float(_final(doc).confidence) == pytest.approx(0.9)
    assert doc.category_id == "03" and doc.document_type.code == "beleg" and doc.status == "filed"
    assert not ReviewCase.objects.filter(document=doc).exists()
    assert (
        doc.current_name == "Rechnung Nr. 2026-0417 Dachrinne.eml"
        and doc.original_name == "Nachricht 4711.eml"
    )
    assert welt["drive"].get(doc.drive_file_id).name == "Rechnung Nr. 2026-0417 Dachrinne.eml"
    # dieselbe Textseite als PDF: die Betreffregel greift nicht, der Beleg bleibt Regel R-03-BELEG-001
    from apps.classification import rules as rules_mod
    from apps.classification.context import build_context

    ctx = build_context(doc)
    assert ctx.email["from_address"] == "buchhaltung@example.test"
    ctx.email = None
    result = rules_mod.evaluate(ctx)
    assert "R-03-EMAIL-RECHNUNG-001" not in result.rule_codes


def test_absender_ist_eigentuemer_ordnet_der_akte_zu(welt, fake_oauth, run_all, admin_user):
    obj = welt["objects"]["623"]
    owner = welt["owners"]["mustermann"]  # WE03
    owner.email = "Max.Mustermann@Example.test"
    owner.save(update_fields=["email", "updated_at"])
    data = _eml(
        sender="Familie M. <max.mustermann@example.test>",
        subject="WG: Frage zum Fahrradkeller",
        body=UNKLAR,
    )
    ohne = make_email_document(obj, data, "frage.eml")
    _run(welt, run_all)
    ohne.refresh_from_db()
    assert ohne.category_id == "06" and ReviewCase.objects.filter(document=ohne).exists()  # Schalter aus
    store.set("email.match_sender_to_party", True, user=admin_user)
    data = _eml(
        sender="Familie M. <max.mustermann@example.test>",
        subject="WG: Frage zum Fahrradkeller, zweite Nachricht",
        body=UNKLAR,
    )
    doc = make_email_document(obj, data, "frage2.eml")
    _run(welt, run_all)
    doc.refresh_from_db()
    assert (
        doc.category_id == "05" and doc.subfolder.code == "08" and doc.document_type.code == "schriftverkehr"
    )
    assert doc.status == "filed" and not ReviewCase.objects.filter(document=doc).exists()
    link = DocumentOwnerLink.objects.get(document=doc)
    assert link.owner_id == owner.pk and link.unit == welt["units"][("623", "WE03")]
    assert link.status == "confirmed" and link.owner_file.folder_name.startswith("WE03")
    final = _final(doc)
    assert final.features["email_sender_match"] == "owner" and final.category_id == "05"
    assert "Absender ist Eigentümer" in final.reasoning
    assert doc.current_name == "Frage zum Fahrradkeller, zweite Nachricht.eml"


def test_absender_eigentuemer_uebersteuert_keinen_harten_regeltreffer(welt, fake_oauth, run_all, admin_user):
    """Gegenpruefung 26.09.2026: der Eigentuemer leitet das Versammlungsprotokoll weiter. Die harte Regel
    R-02-PROTOKOLL-001 bleibt verbindlich (02, kein Fall), der Absendertreffer ist nur Kennzeichen, und es entsteht
    kein Trainingsbeispiel mit dem Label der Eigentuemerkorrespondenz."""
    from apps.documents.models import TrainingSample

    obj = welt["objects"]["623"]
    owner = welt["owners"]["mustermann"]
    owner.email = "we03@example.test"  # ohne Namen, damit kein Segmentvorschlag entsteht
    owner.save(update_fields=["email", "updated_at"])
    store.set("email.match_sender_to_party", True, user=admin_user)
    data = _eml(
        sender="Eigentuemer <we03@example.test>",
        subject="WG: Protokoll ETV 2026",
        body=H623 + "Protokoll der ordentlichen Eigentümerversammlung vom 20.05.2026\n\nBeginn 18:00 Uhr, "
        "Versammlungsleiterin: Verwaltung, Protokollführerin: Verwaltung. Anbei zur Kenntnis.",
    )
    doc = make_email_document(obj, data, "protokoll.eml")
    _run(welt, run_all)
    doc.refresh_from_db()
    assert doc.category_id == "02" and doc.document_type.code == "versammlungsprotokoll"
    assert doc.status == "filed" and not ReviewCase.objects.filter(document=doc).exists()
    final = _final(doc)
    assert final.features["email_sender_match"] == "owner" and float(final.confidence) == 1.0
    stufe1 = DocumentClassification.objects.get(document=doc, stage=1, provider="rules", is_final=False)
    assert "R-02-PROTOKOLL-001" in stufe1.reasoning
    assert TrainingSample.objects.filter(document=doc, label_category_id="02").exists()
    assert not TrainingSample.objects.filter(document=doc, label_category_id="05").exists()
    assert not DocumentOwnerLink.objects.filter(document=doc, status="confirmed").exists()


def test_absender_eigentuemer_ohne_regeltreffer_ruft_keine_stufe_3(
    welt, fake_oauth, run_all, admin_user, monkeypatch
):
    """Gegenpruefung 26.09.2026: mit freigegebenem Anbieter geht die E-Mail eines Eigentuemers ohne Regeltreffer
    nicht an Stufe 3 (kein Aufruf, keine Kosten); der Absenderabgleich entscheidet in decide."""
    from tests.integration.ai.test_pipeline_stage3 import _answer, enable_ai

    from apps.ai import services as ai_services
    from apps.ai.fakes import FakeClassificationProvider
    from apps.ai.models import AiCall
    from apps.ai.provider import PriceList, ProviderConfig
    from apps.ai.router import Router

    obj = welt["objects"]["623"]
    owner = welt["owners"]["mustermann"]
    owner.email = "max.mustermann@example.test"
    owner.save(update_fields=["email", "updated_at"])
    store.set("email.match_sender_to_party", True, user=admin_user)
    enable_ai(admin_user)
    provider = FakeClassificationProvider("openai", answer=_answer("03", None, "beleg", 0.99))
    router = Router(
        {"openai": provider},
        configs={"openai": ProviderConfig("openai", True, "openai-testmodell")},
        price_list=PriceList("t", {}),
        order=["openai"],
    )
    monkeypatch.setattr(ai_services, "default_router", lambda: router)
    data = _eml(sender="Familie M. <max.mustermann@example.test>", subject="WG: Frage", body=UNKLAR)
    doc = make_email_document(obj, data, "frage.eml")
    _run(welt, run_all)
    doc.refresh_from_db()
    assert not ProcessingJob.objects.filter(document=doc, job_type=JobType.CLASSIFY_AI).exists()
    assert not AiCall.objects.filter(document=doc).exists() and provider.sent == []
    assert doc.category_id == "05" and doc.subfolder.code == "08" and doc.status == "filed"
    job = ProcessingJob.objects.get(document=doc, job_type=JobType.DECIDE)
    assert "email.match_sender_to_party" in job.payload["stage3_skipped"]


def test_absender_adresse_mehrerer_eigentuemer_ist_mehrdeutig(welt, fake_oauth, run_all, admin_user):
    """Gesamtreview 26.09.2026: zwei Eigentuemer verschiedener Einheiten (WE03, WE02) fuehren dieselbe Adresse,
    etwa ein Bevollmaechtigter. Keine Ablage ohne Fall in der Akte der kleinsten ID; Kennzeichen owner_ambiguous,
    Entscheidung wie ohne Abgleich (Fall, keine bestaetigte Verknuepfung)."""
    obj = welt["objects"]["623"]
    for key in ("mustermann", "sonder"):
        owner = welt["owners"][key]
        owner.email = "verwaltung.familie@example.test"
        owner.save(update_fields=["email", "updated_at"])
    store.set("email.match_sender_to_party", True, user=admin_user)
    data = _eml(sender="Familie <Verwaltung.Familie@example.test>", subject="WG: Frage", body=UNKLAR)
    doc = make_email_document(obj, data, "frage.eml")
    _run(welt, run_all)
    doc.refresh_from_db()
    final = _final(doc)
    assert final.features["email_sender_match"] == "owner_ambiguous"
    assert "Absender ist Eigentümer" not in (final.reasoning or "")
    assert doc.category_id != "05" and doc.status == "review"
    assert ReviewCase.objects.filter(document=doc).exists()
    assert not DocumentOwnerLink.objects.filter(document=doc, status="confirmed").exists()
    from apps.reporting import services as reporting

    assert reporting.object_kpi(obj).email_sender_matched == 0


def test_absender_eigentuemer_einheit_aus_zuordnung_nicht_aus_text(welt, fake_oauth, run_all, admin_user):
    """Gesamtreview 26.09.2026 (B-09): der Eigentuemer von WE03 schreibt zu einem Schaden in WE07. Die Einheit
    ergibt sich aus seiner Zuordnung; Ablage in der Eigentuemerakte WE03 unter 08 ohne Fall, nicht 06/02 mit
    assignment_unknown."""
    obj = welt["objects"]["623"]
    owner = welt["owners"]["mustermann"]  # WE03
    owner.email = "we03@example.test"
    owner.save(update_fields=["email", "updated_at"])
    store.set("email.match_sender_to_party", True, user=admin_user)
    data = _eml(
        sender="Eigentuemer <we03@example.test>",
        subject="Frage zu WE07",
        body=H623 + "Hallo,\n\nkurze Frage zu WE07, die Nachbarn dort sind nicht erreichbar. Viele Grüße",
    )
    doc = make_email_document(obj, data, "wasser.eml")
    _run(welt, run_all)
    doc.refresh_from_db()
    from apps.classification.context import build_context

    ctx = build_context(doc)
    assert welt["units"][("623", "WE07")].pk in ctx.unit_ids  # die NER erkennt WE07 im Text
    assert doc.category_id == "05" and doc.subfolder.code == "08" and doc.status == "filed"
    assert not ReviewCase.objects.filter(document=doc).exists()
    link = DocumentOwnerLink.objects.get(document=doc)
    assert link.owner_id == owner.pk and link.unit == welt["units"][("623", "WE03")]
    assert link.status == "confirmed" and link.owner_file.folder_name.startswith("WE03")


def test_absender_adresse_mehrerer_mieter_ist_mehrdeutig(welt, fake_oauth, run_all, admin_user):
    """Gesamtreview 26.09.2026: zwei Mieter derselben Adresse im Objekt, Kennzeichen tenant_ambiguous, keine
    bestaetigte Mieterverknuepfung ueber den Absenderabgleich."""
    from datetime import date

    from apps.parties.models import Tenant, TenantUnitAssignment

    obj = welt["objects"]["625"]
    tenant = Tenant.objects.get(last_name="Mieterling-Zwei")
    tenant.email = "wg@example.test"
    tenant.save(update_fields=["email", "updated_at"])
    zweiter = Tenant.objects.create(
        type="natural_person",
        first_name="Mara",
        last_name="Mitbewohner",
        search_name="MITBEWOHNER MARA",
        email="WG@example.test",
    )
    TenantUnitAssignment.objects.create(
        tenant=zweiter, unit=welt["units"][("625", "Wohnung 4")], valid_from=date(2024, 1, 1)
    )
    store.set("email.match_sender_to_party", True, user=admin_user)
    data = _eml(
        sender="WG <wg@example.test>",
        subject="Re: Rückfrage",
        body="Objekt 625\nHallo,\n\nkurze Rückfrage wie besprochen. Viele Grüße",
    )
    doc = make_email_document(obj, data, "rueckfrage.eml")
    _run(welt, run_all, "625")
    doc.refresh_from_db()
    assert _final(doc).features["email_sender_match"] == "tenant_ambiguous"
    assert doc.category_id != "04" and ReviewCase.objects.filter(document=doc).exists()
    assert not DocumentTenantLink.objects.filter(document=doc, status="confirmed").exists()


def test_absender_ist_mieter_ordnet_der_mieterakte_zu(welt, fake_oauth, run_all, admin_user):
    from apps.parties.models import Tenant

    obj = welt["objects"]["625"]  # Mietverwaltung, Mieter Wohnung 4
    tenant = Tenant.objects.get(last_name="Mieterling-Zwei")
    tenant.email = "milo@example.test"
    tenant.save(update_fields=["email", "updated_at"])
    store.set("email.match_sender_to_party", True, user=admin_user)
    data = _eml(
        sender="M. <MILO@example.test>",
        subject="Re: Rückfrage",
        body="Objekt 625\nHallo,\n\nkurze Rückfrage wie besprochen. Viele Grüße",
    )
    doc = make_email_document(obj, data, "rueckfrage.eml")
    _run(welt, run_all, "625")
    doc.refresh_from_db()
    assert doc.category_id == "04" and doc.document_type.code == "mieterkorrespondenz"
    assert not ReviewCase.objects.filter(document=doc).exists()
    link = DocumentTenantLink.objects.get(document=doc)
    assert link.tenant_id == tenant.pk and link.status == "confirmed" and link.tenant_file_id
    assert _final(doc).features["email_sender_match"] == "tenant"


def test_auto_misc_legt_ohne_fall_nach_06_01_ab(welt, fake_oauth, run_all, admin_user):
    obj = welt["objects"]["623"]
    store.set("email.auto_misc", True, user=admin_user)
    data = _eml(sender="Unbekannt <niemand@example.test>", subject="WG: Frage: zum Termin?", body=UNKLAR)
    doc = make_email_document(obj, data, "termin.eml")
    _run(welt, run_all)
    doc.refresh_from_db()
    assert doc.category_id == "06" and doc.subfolder.code == "01" and doc.status == "filed"
    assert doc.final_decided_by == "email_auto"
    assert not ReviewCase.objects.filter(document=doc).exists()
    final = _final(doc)
    assert final.features["email_auto_misc"] is True and final.stage == 1 and final.provider == "rules"
    assert "email.auto_misc" in final.reasoning and final.features["kpi_misc_adjusted"] is True
    assert doc.current_name == "Frage zum Termin.eml"
    assert ProcessingJob.objects.filter(document=doc, job_type=JobType.FILE_TO_DRIVE).exists()
    node = welt["drive"].get(doc.drive_file_id)
    unklar = DriveNodeRow.objects.get(
        object=obj, category_id="06", subfolder__code="01", node_kind="subfolder"
    )
    assert node.name == "Frage zum Termin.eml" and node.parent_id == unklar.drive_file_id
    # Berichte zaehlen die automatische Ablage
    from apps.reporting import services as reporting

    kpi = reporting.object_kpi(obj)
    assert kpi.email_auto_misc == 1
    assert "E-Mails automatisch 06" in reporting.overview_csv([kpi]).split("\r\n")[0]


def test_auto_misc_wartet_bei_ausfall_der_stufe_3(welt, fake_oauth, run_all, admin_user, monkeypatch):
    """Gegenpruefung 26.09.2026: antwortet Stufe 3 nur voruebergehend nicht (Anbieter gestoert), bleibt es beim
    Fall below_threshold mit stage3_status provider_error, damit ai_reclassify das Dokument spaeter einreiht;
    Weg 1 greift erst nach abgeschlossener Stufe 3."""
    from tests.integration.ai.test_pipeline_stage3 import _answer, enable_ai

    from apps.ai import services as ai_services
    from apps.ai.fakes import FakeClassificationProvider
    from apps.ai.provider import PriceList, ProviderConfig
    from apps.ai.router import Router

    def _router(script, answer=None):
        p = FakeClassificationProvider("openai", script=script, answer=answer)
        return Router(
            {"openai": p},
            configs={"openai": ProviderConfig("openai", True, "openai-testmodell")},
            price_list=PriceList("t", {}),
            order=["openai"],
        )

    obj = welt["objects"]["623"]
    store.set("email.auto_misc", True, user=admin_user)
    enable_ai(admin_user)
    router = _router(["timeout", "5xx", "5xx", "5xx"])
    monkeypatch.setattr(ai_services, "default_router", lambda: router)
    data = _eml(sender="Unbekannt <niemand@example.test>", subject="WG: Frage zur Ablage", body=UNKLAR)
    doc = make_email_document(obj, data, "ablage.eml")
    _run(welt, run_all)
    doc.refresh_from_db()
    assert doc.category_id == "06" and doc.status == "review" and doc.final_decided_by == "stage1"
    case = ReviewCase.objects.get(document=doc)
    assert case.case_subtype == "below_threshold" and case.context["stage3_status"] == "provider_error"
    assert _final(doc).features["email_auto_misc"] is False
    # nach Behebung reiht ai_reclassify das Dokument erneut ein
    from django.core.management import call_command

    store.set("ai.reclassify_enabled", True, user=admin_user)
    router = _router([], answer=_answer("05", "08", "schriftverkehr", 0.95))
    call_command("ai_reclassify", object="623")
    run_all(obj)
    doc.refresh_from_db()
    assert doc.final_decided_by == "stage3" and doc.category_id == "05"
    # Stufe 3 abgeschlossen (KI ohne verwertbare Antwort, aber kein Ausfall): Weg 1 legt ohne Fall ab
    router = _router([], answer=_answer("06", None, None, 0.3))
    data = _eml(sender="Unbekannt <niemand@example.test>", subject="WG: Noch eine Frage", body=UNKLAR)
    doc2 = make_email_document(obj, data, "ablage2.eml")
    _run(welt, run_all)
    doc2.refresh_from_db()
    assert doc2.final_decided_by == "email_auto" and not ReviewCase.objects.filter(document=doc2).exists()


def test_betreff_hinweis_fuer_stufe_3_ist_maskiert(welt, fake_oauth, run_all):
    """Gegenpruefung 26.09.2026: Parteinamen im Betreff gehen wie im Dateinamen maskiert an den Anbieter."""
    from apps.ai.services import build_request
    from apps.classification.context import build_context

    obj = welt["objects"]["623"]
    data = _eml(
        sender="Unbekannt <niemand@example.test>", subject="AW: Kündigung Mustermann WE 3", body=UNKLAR
    )
    doc = make_email_document(obj, data, "kuendigung.eml")
    req = build_request(doc, build_context(doc), None)
    assert req.hints["is_email"] is True and req.hints["email_subject"] == "Kündigung [NAME] WE 3"
    assert "Mustermann" not in req.filename_masked


def test_auto_misc_verschiebt_und_benennt_bestandsdatei(welt, fake_oauth, run_all, admin_user):
    obj = welt["objects"]["623"]
    drive = welt["drive"]
    root = DriveNodeRow.objects.get(object=obj, node_kind="object_root").drive_file_id
    store.set("email.auto_misc", True, user=admin_user)
    data = _eml(sender="Unbekannt <niemand@example.test>", subject="AW: Terminabsprache", body=UNKLAR)
    doc = make_email_document(obj, data, "alt.eml", source="drive_existing", drive=drive, parent_id=root)
    _run(welt, run_all)
    doc.refresh_from_db()
    assert doc.status == "filed" and doc.final_decided_by == "email_auto"
    node = drive.get(doc.drive_file_id)
    unklar = DriveNodeRow.objects.get(
        object=obj, category_id="06", subfolder__code="01", node_kind="subfolder"
    )
    assert node.parent_id == unklar.drive_file_id and node.name == "Terminabsprache.eml"
    assert doc.current_name == "Terminabsprache.eml" and doc.original_name == "alt.eml"
    from apps.audit.models import AuditEvent

    assert AuditEvent.objects.filter(action="drive.rename_file", entity_id=doc.pk).exists()


def test_standard_aus_erzeugt_weiter_den_fall(welt, fake_oauth, run_all):
    obj = welt["objects"]["623"]
    assert store.get("email.auto_misc") is False and store.get("email.match_sender_to_party") is False
    data = _eml(sender="Unbekannt <niemand@example.test>", subject="WG: Frage zum Termin", body=UNKLAR)
    doc = make_email_document(obj, data, "termin.eml")
    _run(welt, run_all)
    doc.refresh_from_db()
    assert doc.category_id == "06" and doc.subfolder.code == "01" and doc.status == "review"
    assert doc.final_decided_by == "stage1"
    case = ReviewCase.objects.get(document=doc)
    assert case.case_type == "unclear" and case.case_subtype == "below_threshold"
    assert _final(doc).features["email_auto_misc"] is False
    assert doc.current_name == "Frage zum Termin.eml"  # Titel gilt unabhaengig vom Schalter


def test_email_status_kommando(welt, fake_oauth, run_all, admin_user):
    from io import StringIO

    from django.core.management import call_command

    obj = welt["objects"]["623"]
    store.set("email.auto_misc", True, user=admin_user)
    doc = make_email_document(
        obj, _eml(sender="Unbekannt <niemand@example.test>", subject="Geheimer Betreff", body=UNKLAR), "x.eml"
    )
    _run(welt, run_all)
    out = StringIO()
    call_command("email_status", "--objekt", "623", stdout=out)
    text = out.getvalue()
    assert "Schalter: email.auto_misc=an, email.match_sender_to_party=aus" in text
    assert (
        "E-Mail-Dokumente: 1 (Objekt 623)" in text and "Automatisch nach 06/01 (email.auto_misc): 1" in text
    )
    assert "email_auto=1" in text and "Geheimer" not in text and "niemand" not in text
    assert "x.eml" not in text and str(doc.pk) not in text.replace("623", "")
