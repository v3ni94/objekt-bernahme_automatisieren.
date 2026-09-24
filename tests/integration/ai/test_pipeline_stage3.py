"""Stufe 3 in der Kette: classify -> classify_ai -> decide mit Fake-Anbietern; Uebereinstimmung hebt die Konfidenz,
Widerspruch mit hoher Konfidenz uebernimmt die KI-Kategorie (decided_by stage3, Stichprobe), Ausfall beider Anbieter
ergibt 06/01_Unklar mit Fall und Grund KI nicht verfuegbar (B-28); Ausweiskopie und Kategorie 01 gehen nie an Stufe 3;
Nachklassifikationslauf; Statusseite zeigt Kosten."""

from __future__ import annotations

import json

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.urls import reverse
from tests.integration.classification.conftest import make_document

from apps.ai import services as ai_services
from apps.ai.fakes import FakeClassificationProvider
from apps.ai.models import AiCall
from apps.ai.provider import PriceList, ProviderConfig
from apps.ai.router import Router
from apps.config import store
from apps.documents.models import DocumentClassification
from apps.objects.models import ManagedObject
from apps.pipeline.models import JobType, ProcessingJob
from apps.pipeline.runs import start_run
from apps.review.models import ReviewCase

pytestmark = pytest.mark.django_db
H623 = "Objekt 623 Düsseldorf, Joachimstraße 49\n"
UNKLAR = {
    "filename": "Schreiben_unklar.pdf",
    "pages": [
        H623
        + "Sehr geehrte Damen und Herren, anbei die Unterlagen wie besprochen zur Wohnung. Mit freundlichen Grüßen"
    ],
}


def enable_ai(admin_user):
    providers = store.get("ai.providers")
    for name in providers:
        providers[name]["enabled"] = True
        providers[name]["model"] = f"{name}-testmodell"
    store.set("ai.providers", providers, user=admin_user)


@pytest.fixture
def fake_router(monkeypatch):
    holder = {}

    def _make(primary_script=None, fallback_script=None, answer=None):
        p = FakeClassificationProvider("openai", script=primary_script or [], answer=answer)
        f = FakeClassificationProvider("anthropic", script=fallback_script or [], answer=answer)
        r = Router(
            {"openai": p, "anthropic": f},
            configs={
                "openai": ProviderConfig("openai", True, "openai-testmodell"),
                "anthropic": ProviderConfig("anthropic", True, "anthropic-testmodell"),
            },
            price_list=PriceList("t", {}),
            order=["openai", "anthropic"],
        )
        monkeypatch.setattr(ai_services, "default_router", lambda: r)
        holder["p"], holder["f"] = p, f
        return r

    return _make


def _answer(category, subfolder=None, dtype=None, confidence=0.95, object_related=True, year=None):
    return {
        "object_related": object_related,
        "category": category,
        "subfolder": subfolder,
        "document_type": dtype,
        "period": {"year": year, "from": None, "to": None, "document_date": None},
        "mentioned_units": [],
        "mentioned_parties": [],
        "confidence": confidence,
        "reasoning": "Testantwort",
    }


def test_ki_entscheidet_bei_unklarem_dokument(
    welt, fake_oauth, run_all, admin_user, fake_router, monkeypatch
):
    enable_ai(admin_user)
    fake_router(answer=_answer("02", None, "gebaeudeversicherung", 0.95))
    monkeypatch.setattr(ai_services, "sample_for_review", lambda: True)
    obj = welt["objects"]["623"]
    doc = make_document(obj, UNKLAR)
    start_run(obj)
    run_all(obj)
    doc.refresh_from_db()
    assert ProcessingJob.objects.filter(document=doc, job_type=JobType.CLASSIFY_AI, status="done").exists()
    assert doc.final_decided_by == "stage3" and doc.category_id == "02" and doc.status == "filed"
    s3 = DocumentClassification.objects.get(document=doc, stage=3, is_final=False)  # Anbieterzeile
    assert (
        s3.provider == "openai"
        and s3.category_id == "02"
        and s3.ai_call is not None
        and float(s3.confidence) == 0.95
    )
    final = DocumentClassification.objects.get(document=doc, is_final=True)
    assert final.category_id == "02" and final.document_type.code == "gebaeudeversicherung"
    assert final.provider == "openai" and "Stufe 3 entscheidet" in final.reasoning
    # Stichprobe (F17): Ablage erfolgt, der Fall haelt die Ablage nicht auf
    case = ReviewCase.objects.get(document=doc, case_subtype="ai_sample")
    assert (
        case.case_type == "move_proposal" and case.status == "open" and case.context["provider"] == "openai"
    )
    assert AiCall.objects.filter(document=doc, status="ok").count() == 1


def test_ausfall_beider_anbieter_unklar_mit_fall(welt, fake_oauth, run_all, admin_user, fake_router):
    enable_ai(admin_user)
    fake_router(["timeout", "5xx"], ["5xx", "timeout"])
    obj = welt["objects"]["623"]
    doc = make_document(obj, UNKLAR)
    start_run(obj)
    run_all(obj)
    doc.refresh_from_db()
    assert doc.category_id == "06" and doc.subfolder.code == "01" and doc.status == "review"
    case = ReviewCase.objects.get(document=doc)
    assert case.case_type == "unclear" and case.context["stage3_status"] == "provider_error"
    assert "KI nicht verfügbar" in case.context["reason"]
    assert (
        AiCall.objects.filter(document=doc).count() == 4
        and AiCall.objects.filter(document=doc, fallback_used=True).count() == 2
    )
    # Nachklassifikationslauf reiht das Dokument erneut ein, solange kein Mensch entschieden hat
    store.set("ai.reclassify_enabled", True, user=admin_user)
    fake_router(answer=_answer("05", "08", "schriftverkehr", 0.95))
    call_command("ai_reclassify", object="623")
    run_all(obj)
    doc.refresh_from_db()
    assert doc.final_decided_by == "stage3" and doc.category_id == "05"


def test_nachklassifikation_holt_faelle_ohne_stufe3_aufruf_nach(
    welt, fake_oauth, run_all, admin_user, fake_router
):
    """Bestand vor der Freischaltung (20.09.2026): kein Anbieter freigegeben, classify ruft die Stufe 3 gar nicht auf,
    der Fall traegt stage3_status None. Nach der Freischaltung muss ai_reclassify genau diese Faelle erneut einreihen."""
    obj = welt["objects"]["623"]
    doc = make_document(obj, UNKLAR)
    start_run(obj)
    run_all(obj)
    doc.refresh_from_db()
    assert doc.category_id == "06" and doc.subfolder.code == "01" and doc.status == "review"
    assert not ProcessingJob.objects.filter(document=doc, job_type=JobType.CLASSIFY_AI).exists()
    case = ReviewCase.objects.get(document=doc)
    assert case.case_subtype == "below_threshold" and case.context.get("stage3_status") is None
    assert AiCall.objects.filter(document=doc).count() == 0
    enable_ai(admin_user)
    fake_router(answer=_answer("05", "08", "schriftverkehr", 0.95))
    call_command("ai_reclassify", object="623", force=True)
    case.refresh_from_db()
    assert case.status == "dismissed" and case.resolution["decision"] == "reclassify"
    run_all(obj)
    doc.refresh_from_db()
    assert doc.final_decided_by == "stage3" and doc.category_id == "05"
    assert AiCall.objects.filter(document=doc, status="ok").count() == 1


def test_uebereinstimmung_hebt_konfidenz_und_sperren(welt, fake_oauth, run_all, admin_user, fake_router):
    enable_ai(admin_user)
    r = fake_router(answer=_answer("05", "09", "mahnung", 0.8, year=2025))
    obj = welt["objects"]["623"]
    # t_ai_call ueber der Regelkonfidenz 0,90 (E 7.2: t_ai_call ist konfigurierbar): weiche Regel ohne NER-Stuetze
    # geht an Stufe 3, die Uebereinstimmung hebt die Konfidenz ueber t_auto
    store.set("classification.threshold_stage3_call", 0.95, user=admin_user)
    doc = make_document(
        obj,
        {
            "filename": "Mahnung.pdf",
            "pages": [
                H623
                + "Letzte Mahnung: Das Hausgeld ist trotz Zahlungserinnerung nicht eingegangen. Mahngebühr 5,00 EUR."
            ],
        },
    )
    start_run(obj)
    run_all(obj)
    doc.refresh_from_db()
    final = DocumentClassification.objects.get(document=doc, is_final=True)
    assert final.category_id == "05" and "Stufe 3 stimmt zu" in final.reasoning
    assert len(r.providers["openai"].sent) == 1
    # Ausweiskopie: kein Aufruf
    doc2 = make_document(
        obj,
        {
            "filename": "Ausweis.pdf",
            "pages": [H623 + "Kopie Personalausweis Nr. L01X00T47 des Eigentümers, Vorderseite"],
        },
    )
    start_run(obj)
    run_all(obj)
    assert not AiCall.objects.filter(document=doc2).exists()
    case = ReviewCase.objects.filter(document=doc2).first()
    assert case is not None and case.context.get("stage3_status") == "skipped"
    assert len(r.providers["openai"].sent) == 1


def test_statusseite_zeigt_stufe3(welt, fake_oauth, client_as, admin_user):
    client = client_as(admin_user)
    resp = client.get(reverse("status_page"))
    assert resp.status_code == 200
    html = resp.content.decode()
    assert "Stufe 3 (externe KI)" in html and "deaktiviert (F17, AVV)" in html
    assert json.dumps(resp.context["processing"]["ai"]["order"]) == '["openai", "anthropic"]'


H624 = "Objekt 624\n"
UNKLAR624 = {"filename": "Schreiben_unklar_624.pdf", "pages": [H624 + UNKLAR["pages"][0].split("\n", 1)[1]]}


def test_nachklassifikation_ausnahmen_begrenzung_zusammenfassung(
    welt, fake_oauth, run_all, admin_user, fake_router
):
    """23.09.2026: Sammelpfade (133, 216) ausnehmen, Piloten begrenzen, je Objekt zusammenfassen statt 12.500 Zeilen."""
    from io import StringIO

    a, b = welt["objects"]["623"], welt["objects"]["624"]
    doc_a = make_document(a, UNKLAR)
    doc_b = make_document(b, UNKLAR624)
    for obj in (a, b):
        start_run(obj)
        run_all(obj)
    for doc in (doc_a, doc_b):
        doc.refresh_from_db()
        assert doc.status == "review" and doc.category_id == "06"
    enable_ai(admin_user)
    fake_router(answer=_answer("05", "08", "schriftverkehr", 0.95))

    out = StringIO()
    call_command("ai_reclassify", force=True, dry_run=True, stdout=out)
    text = out.getvalue()
    assert "Objekt 623: 1" in text and "Objekt 624: 1" in text and "2 Dokumente gefunden" in text
    assert "würde neu klassifizieren" not in text  # Einzelzeilen nur mit --details

    out = StringIO()
    call_command("ai_reclassify", force=True, dry_run=True, details=True, limit=1, stdout=out)
    text = out.getvalue()
    assert text.count("würde neu klassifizieren") == 1 and "Begrenzung 1 erreicht" in text

    out = StringIO()
    call_command("ai_reclassify", force=True, ohne="624", stdout=out)
    text = out.getvalue()
    assert "Objekt 623: 1" in text and "Objekt 624" not in text and "Ausgenommen: 624" in text
    assert "1 Dokumente erneut eingereiht" in text
    assert ReviewCase.objects.get(document=doc_a).status == "dismissed"
    assert ReviewCase.objects.get(document=doc_b).status == "open"

    with pytest.raises(Exception, match="Ziffern"):
        call_command("ai_reclassify", force=True, dry_run=True, ohne="624,abc")


def test_kein_objektbezug_ohne_anschrift_wird_verworfen(welt, fake_oauth, run_all, admin_user, fake_router):
    """24.09.2026: 4.303 Dokumente lagen als „kein Objektbezug“ in 06, obwohl der Auftrag keine Objektanschrift
    enthielt (963 davon in Objekten ohne erfasste Strasse). Der Auftrag traegt jetzt die Objektangaben; ohne bekannte
    Anschrift wird object_related false verworfen und die Kategorie der Antwort uebernommen."""
    enable_ai(admin_user)
    r = fake_router(answer=_answer("02", None, "gebaeudeversicherung", 0.95, object_related=False))
    obj = welt["objects"]["624"]
    ManagedObject.objects.filter(pk=obj.pk).update(street="", house_number="")
    obj.refresh_from_db()
    doc = make_document(obj, UNKLAR624)
    start_run(obj)
    run_all(obj)
    doc.refresh_from_db()
    auftrag = r.providers["openai"].sent[0]["user"]
    assert '"anschrift_bekannt": false' in auftrag and '"objektnummer": "624"' in auftrag
    assert doc.final_decided_by == "stage3" and doc.category_id == "02" and doc.status == "filed"
    s3 = DocumentClassification.objects.get(document=doc, stage=3, is_final=False)
    assert (
        s3.category_id == "02" and s3.scope_decision is None and s3.features["object_related_ignored"] is True
    )
    assert not ReviewCase.objects.filter(document=doc, case_subtype="manual_check").exists()
    # Hinweisfall: die Ablage ist erfolgt, die Entscheidung bleibt im Pruefcenter sichtbar
    hinweis = ReviewCase.objects.get(document=doc, case_subtype="ai_object_unverified")
    assert hinweis.case_type == "move_proposal" and hinweis.status == "open"
    assert "Anschrift" in hinweis.context["reason"]


def test_kein_objektbezug_ohne_anschrift_bleibt_bei_fremder_objektnummer(
    welt, fake_oauth, run_all, admin_user, fake_router
):
    """Nennt der Text eine fremde Objektnummer, steht die Aussage der KI nicht allein: das false gilt auch ohne
    erfasste Anschrift, das Dokument bleibt in 06/04 (Fremdobjekt)."""
    enable_ai(admin_user)
    store.set(
        "classification.threshold_stage3_call", 0.95, user=admin_user
    )  # sonst entscheidet Stufe 1 allein
    r = fake_router(answer=_answer("02", None, "gebaeudeversicherung", 0.95, object_related=False))
    obj = welt["objects"]["624"]
    ManagedObject.objects.filter(pk=obj.pk).update(street="", house_number="")
    obj.refresh_from_db()
    doc = make_document(
        obj, {"filename": "fremd.pdf", "pages": [UNKLAR["pages"][0]]}
    )  # Kopf nennt Objekt 623
    start_run(obj)
    run_all(obj)
    doc.refresh_from_db()
    assert '"anschrift_bekannt": false' in r.providers["openai"].sent[0]["user"]
    assert doc.category_id == "06" and doc.subfolder.code == "04"
    s3 = DocumentClassification.objects.get(document=doc, stage=3, is_final=False)
    assert s3.category_id == "06" and s3.scope_decision == "unclear"
    assert s3.features["object_related_ignored"] is False
    assert not ReviewCase.objects.filter(document=doc, case_subtype="ai_object_unverified").exists()


def test_kein_objektbezug_mit_anschrift_bleibt_sonstiges_und_nachklassifikation_manual_check(
    welt, fake_oauth, run_all, admin_user, fake_router
):
    """Mit bekannter Anschrift gilt object_related false weiter (06, Fall manual_check). Diese Faelle holt
    ai_reclassify nur mit --unterfall manual_check --alle nach (24.09.2026), der Standardlauf laesst sie liegen."""
    from io import StringIO

    enable_ai(admin_user)
    r = fake_router(answer=_answer("02", None, "gebaeudeversicherung", 0.95, object_related=False))
    obj = welt["objects"]["623"]
    doc = make_document(obj, UNKLAR)
    start_run(obj)
    run_all(obj)
    doc.refresh_from_db()
    auftrag = r.providers["openai"].sent[0]["user"]
    assert '"anschrift_bekannt": true' in auftrag and "Joachimstraße 49" in auftrag
    assert doc.category_id == "06" and doc.status == "review"
    s3 = DocumentClassification.objects.get(document=doc, stage=3, is_final=False)
    assert s3.category_id == "06" and s3.scope_decision == "unclear"
    assert s3.features["object_related_ignored"] is False
    case = ReviewCase.objects.get(document=doc, status="open")
    assert case.case_subtype == "manual_check"

    out = StringIO()
    call_command("ai_reclassify", object="623", force=True, stdout=out)  # Standard: nur below_threshold
    assert "0 Dokumente erneut eingereiht" in out.getvalue()
    case.refresh_from_db()
    assert case.status == "open"
    with pytest.raises(CommandError, match="erfordert --alle"):
        call_command("ai_reclassify", object="623", force=True, unterfall="manual_check")
    with pytest.raises(CommandError, match="erlaubt"):
        call_command("ai_reclassify", object="623", force=True, unterfall="duplicate", alle=True)

    fake_router(answer=_answer("05", "08", "schriftverkehr", 0.95))
    out = StringIO()
    call_command(
        "ai_reclassify",
        object="623",
        force=True,
        unterfall="below_threshold,manual_check",
        alle=True,
        stdout=out,
    )
    assert "1 Dokumente erneut eingereiht" in out.getvalue()
    case.refresh_from_db()
    assert case.status == "dismissed" and case.resolution["unterfall"] == "manual_check"
    run_all(obj)
    doc.refresh_from_db()
    assert (
        doc.final_decided_by == "stage3" and doc.category_id == "05"
    )  # ohne Eigentuemer: Pruefung, nicht 06


def test_unsichere_ki_antwort_06_verdraengt_lokalen_kandidaten_nicht(
    welt, fake_oauth, run_all, admin_user, fake_router
):
    """24.09.2026: antwortet die KI unsicher mit 06 (Objektbezug ja), bleibt der Regelkandidat (hier 03 Beleg mit
    0,8) als Vorschlag erhalten: Fall below_threshold mit Kandidat statt manual_check ohne Vorschlag."""
    enable_ai(admin_user)
    fake_router(answer=_answer("06", "01", None, 0.9, object_related=True))
    obj = welt["objects"]["623"]
    text = (
        H623 + "Rechnung Nr. 4711 des Hausmeisterdienstes für die Treppenhausreinigung, "
        "Rechnungsbetrag 250,00 EUR, zahlbar bis 30.09.2026"
    )
    doc = make_document(obj, {"filename": "Rechnung_4711.pdf", "pages": [text]})
    start_run(obj)
    run_all(obj)
    doc.refresh_from_db()
    assert doc.status == "review" and doc.category_id == "06"  # physisch 06/01, unter der Schwelle
    case = ReviewCase.objects.get(document=doc, status="open")
    assert case.case_subtype == "below_threshold"
    assert case.candidates and case.candidates[0]["category"] == "03"
    assert "ohne eindeutige Zuordnung" in case.context["reason"]
    assert not ReviewCase.objects.filter(document=doc, case_subtype="manual_check").exists()
    final = DocumentClassification.objects.get(document=doc, is_final=True)
    assert final.category_id == "06" and doc.final_decided_by != "stage3"


def test_ki_vorschlag_unter_schwelle_wird_ziel_im_pruefcenter(
    welt, fake_oauth, run_all, admin_user, fake_router
):
    """24.09.2026 (Vorgabe GF): die KI nennt immer den besten Platz; unter 0,85 liegt das Dokument in 06/01 und der
    Vorschlag ist Ziel im Pruefcenter und in der Sammelaktion, mit Stufe-3-Kandidat im Fall."""
    from apps.review.services import bulk_rows, proposal_target

    enable_ai(admin_user)
    fake_router(answer=_answer("02", None, "gebaeudeversicherung", 0.7))
    obj = welt["objects"]["623"]
    doc = make_document(obj, UNKLAR)
    start_run(obj)
    run_all(obj)
    doc.refresh_from_db()
    assert doc.category_id == "06" and doc.subfolder.code == "01" and doc.status == "review"
    case = ReviewCase.objects.get(document=doc, status="open")
    assert case.case_subtype == "below_threshold"
    assert case.context["intended"] == {
        "category": "02",
        "subfolder": None,
        "document_type": "gebaeudeversicherung",
    }
    assert case.candidates and case.candidates[0]["stage"] == 3 and case.candidates[0]["category"] == "02"
    assert "schlaegt vor" in case.context["reason"]
    ziel = proposal_target(case)
    assert ziel.category == "02" and ziel.document_type == "gebaeudeversicherung"
    row = bulk_rows([case.pk])[0]
    assert not [e for e in row.errors if "Kandidaten" in e]


def test_ki_ab_85_prozent_legt_ab(welt, fake_oauth, run_all, admin_user, fake_router):
    enable_ai(admin_user)
    fake_router(answer=_answer("02", None, "gebaeudeversicherung", 0.86))
    obj = welt["objects"]["623"]
    doc = make_document(obj, UNKLAR)
    start_run(obj)
    run_all(obj)
    doc.refresh_from_db()
    assert doc.final_decided_by == "stage3" and doc.category_id == "02" and doc.status == "filed"


def test_widerspruch_der_ki_senkt_hinweisregel_unter_die_schwelle(
    welt, fake_oauth, run_all, admin_user, fake_router
):
    """Hinweisregel 03 Beleg (0,8) gegen KI 02/08 mit 0,7: kein Ueberschreiben, aber Abzug malus_disagree, das
    Dokument geht mit beiden Kandidaten in die Pruefung statt ohne Bestaetigung nach 03."""
    enable_ai(admin_user)
    fake_router(answer=_answer("02", "08", "gebaeudeversicherung", 0.7))
    obj = welt["objects"]["623"]
    text = (
        H623 + "Rechnung Nr. 4711 des Hausmeisterdienstes für die Treppenhausreinigung, "
        "Rechnungsbetrag 250,00 EUR, zahlbar bis 30.09.2026"
    )
    doc = make_document(obj, {"filename": "Rechnung_4711.pdf", "pages": [text]})
    start_run(obj)
    run_all(obj)
    doc.refresh_from_db()
    assert doc.category_id == "06" and doc.status == "review"
    case = ReviewCase.objects.get(document=doc, status="open")
    assert case.case_subtype == "below_threshold" and "widerspricht" in case.context["reason"]
    assert [c["category"] for c in case.candidates] == ["03", "02"]
    assert case.context["intended"]["category"] == "03" and float(case.context["confidence"]) < 0.85
