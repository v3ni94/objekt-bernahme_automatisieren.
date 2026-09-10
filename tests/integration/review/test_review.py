"""Review Center (M7, H 2): Liste mit Filtern, Zaehlern und Keyset-Paginierung; Detailansicht; Aktionen in einer
Transaktion mit review_decisions, review_cases, audit_events und Trainingsdatum; historische Zuordnung mit Warnung;
Seitenbild ohne Sitzung 302 oder 403; Verwerfen von Objektfaellen nur Admin; Wiedereroeffnen, Dublette, Aufteilen,
Uebernahme; gespeicherte Sichten; kein Drive-Aufruf in einer Anfrage."""

from __future__ import annotations

import json

import pytest
from django.urls import reverse

from apps.audit.models import AuditEvent
from apps.documents.models import Document, DocumentClassification, DocumentOwnerLink, TrainingSample
from apps.drive.adapter import RecordingDriveAdapter
from apps.parties.models import OwnerUnitAssignment
from apps.pipeline.models import JobType, ProcessingJob
from apps.review import hooks, services
from apps.review.models import ReviewCase, ReviewDecision, ReviewSavedFilter

from .helpers import H623, make_case_document

pytestmark = pytest.mark.django_db

EIGENTUEMERKONTO = {
    "filename": "Eigentuemerkonto_WE05.pdf",
    "pages": [
        H623
        + "Kontoauszug Eigentümerkonto WE05\nBuchungen: Hausgeld, Sonderumlage. Saldo 0,00 EUR. Name: [Stempel]"
    ],
}
MAHNUNG_0325 = {
    "filename": "Mahnung_Hausgeld_2025-03_WE05.pdf",
    "pages": [
        H623
        + "Zahlungserinnerung vom 15.04.2025\nFür die Einheit WE05 ist das Hausgeld 03/2025 in Höhe von 245,00 EUR nicht eingegangen."
    ],
}
UNKLAR = {
    "filename": "Schreiben_unklar.pdf",
    "pages": [H623 + "Sehr geehrte Damen und Herren, anbei die Unterlagen. Mit freundlichen Grüßen"],
}


def test_liste_filter_zaehler_paginierung(welt, fake_oauth, run_all, client_as, admin_user):
    for i in range(3):
        make_case_document(welt, run_all, {"filename": f"unklar_{i}.pdf", "pages": UNKLAR["pages"]})
    doc, case = make_case_document(welt, run_all, EIGENTUEMERKONTO)
    client = client_as(admin_user)
    resp = client.get(reverse("review_list"))
    assert resp.status_code == 200
    html = resp.content.decode()
    assert "Eigentuemerkonto_WE05.pdf" in html and "unklar_0.pdf" in html
    assert (
        resp.context["counters"]["total"] == 4
        and resp.context["counters"]["by_type"]["owner_candidates"] == 1
    )
    resp = client.get(reverse("review_list"), {"fallart": "owner_candidates"})
    assert [c.pk for c in resp.context["cases"]] == [case.pk]
    resp = client.get(reverse("review_list"), {"q": "unklar_1"})
    assert len(resp.context["cases"]) == 1
    resp = client.get(reverse("review_list"), {"ziel": "05"})
    assert case.pk in [c.pk for c in resp.context["cases"]]
    # Keyset-Paginierung
    rows, cursor = services.page(services.ListFilters(), size=2)
    assert len(rows) == 2 and cursor
    rows2, cursor2 = services.page(services.ListFilters(), cursor=cursor, size=2)
    assert len(rows2) == 2 and cursor2 is None
    assert {r.pk for r in rows} & {r.pk for r in rows2} == set()
    assert [r.priority for r in rows + rows2] == sorted(r.priority for r in rows + rows2)
    # Sicht speichern und anwenden
    resp = client.post(
        reverse("review_saved_filter"),
        {"name": "Eigentümerfälle", "fallart": "owner_candidates", "status": ["open"]},
    )
    assert resp.status_code == 302
    saved = ReviewSavedFilter.objects.get(user=admin_user, name="Eigentümerfälle")
    assert saved.filters["case_type"] == "owner_candidates"
    resp = client.get(reverse("review_apply_filter", args=[saved.pk]))
    assert resp.status_code == 302 and "fallart=owner_candidates" in resp["Location"]
    assert AuditEvent.objects.filter(action="review.filter_saved").exists()


def test_detail_und_bestaetigen_mit_kandidat(welt, fake_oauth, run_all, client_as, admin_user):
    doc, case = make_case_document(welt, run_all, EIGENTUEMERKONTO)
    assert case.case_type == "owner_candidates" and len(case.candidates) == 2
    client = client_as(admin_user)
    resp = client.get(reverse("review_detail", args=[case.pk]))
    assert resp.status_code == 200
    html = resp.content.decode()
    assert "Altmann" in html and "Neumann" in html and "Kandidaten" in html
    altmann = next(k for k in case.candidates if "Altmann" in k["owner"])
    drive = welt["drive"]
    ops_before = len(drive.ops)
    resp = client.post(
        reverse("review_action", args=[case.pk]),
        {
            "action": "confirm",
            "category": "05",
            "subfolder": "04",
            "document_type": "eigentuemerkonto",
            "owner_id": altmann["owner_id"],
            "unit_id": altmann["unit_id"],
            "assignment_id": altmann["assignment_id"],
            "query": "",
            "serie": "0",
        },
    )
    assert resp.status_code == 302
    assert len(drive.ops) == ops_before, "kein Drive-Aufruf in der Anfrage"
    case.refresh_from_db()
    doc.refresh_from_db()
    assert case.status == "resolved" and case.resolved_by == admin_user
    assert (
        doc.status == "classified"
        and doc.category_id == "05"
        and doc.subfolder.code == "04"
        and doc.final_decided_by == "human"
    )
    final = DocumentClassification.objects.get(document=doc, is_final=True)
    assert final.stage == 4 and final.provider == "human" and final.decided_by == admin_user
    link = DocumentOwnerLink.objects.get(document=doc, status="confirmed")
    assert (
        link.owner_id == altmann["owner_id"]
        and link.assignment_id == altmann["assignment_id"]
        and link.owner_file.folder_name.startswith("WE05_Altmann")
    )
    decision = ReviewDecision.objects.get(review_case=case)
    assert decision.decision_type in ("correct", "assign_owner") and decision.system_was_correct is False
    assert decision.features_snapshot and decision.text_hashes and decision.label_category_id == "05"
    sample = TrainingSample.objects.get(review_decision=decision)
    assert sample.label_source == "review_decision" and float(sample.weight) == 1.0
    assert AuditEvent.objects.filter(
        action="review.reclassify", entity_id=case.pk, user_id=admin_user.pk
    ).exists()
    job = ProcessingJob.objects.get(document=doc, job_type=JobType.FILE_TO_DRIVE, status="pending")
    assert job.payload["category"] == "05" and job.payload["owner_file_id"] == link.owner_file_id
    # Verschiebung laeuft als Job, danach filed
    run_all(welt["objects"]["623"])
    doc.refresh_from_db()
    assert doc.status == "filed" and doc.drive_node.owner_file_id == link.owner_file_id


def test_historische_zuordnung_warnung(welt, fake_oauth, run_all, client_as, admin_user):
    doc, case = make_case_document(welt, run_all, MAHNUNG_0325)
    # Die Pipeline ordnet automatisch Altmann zu (kein Fall); wir erzeugen einen Fall zum Test des Formulars
    if case is None:
        case = ReviewCase.objects.create(
            object=doc.object,
            case_type="owner_candidates",
            case_subtype="test",
            document=doc,
            context={"reason": "Test"},
        )
    neumann = welt["owners"]["neumann"]
    target = services.Target(
        category="05",
        subfolder="09",
        document_type="zahlungserinnerung",
        owner_id=neumann.pk,
        unit_id=welt["units"][("623", "WE05")].pk,
        period_from=doc.period_from,
        period_to=doc.period_to,
    )
    errors, warnings = services.validate(case, target)
    assert errors == []
    assert warnings and "Altmann" in warnings[0]
    decision = services.apply_decision(case, admin_user, target)
    assert decision.after_state["warnings"] and "Altmann" in decision.after_state["warnings"][0]
    assert (
        "Altmann"
        in AuditEvent.objects.get(action="review.reclassify", entity_id=case.pk).after_state["warnings"][0]
    )


def test_validierung_pflichtfelder_und_neue_zuordnung(welt, fake_oauth, run_all, admin_user):
    doc, case = make_case_document(welt, run_all, EIGENTUEMERKONTO)
    errors, _ = services.validate(case, services.Target(category="05", document_type="eigentuemerkonto"))
    assert any("Eigentümer" in e for e in errors) and any("Einheit" in e for e in errors)
    errors, _ = services.validate(case, services.Target(category="06", subfolder="01"))
    assert any("Begründung" in e for e in errors)
    errors, _ = services.validate(
        case,
        services.Target(
            category="05",
            subfolder="05",
            document_type="einzelabrechnung",
            owner_unknown=True,
            unit_unknown=True,
        ),
    )
    assert any("Jahr" in e for e in errors)
    # Eigentuemer ohne Zuordnung zur Einheit: Fehler ohne Eigentumsbeginn, sonst neue Zuordnung
    mustermann = welt["owners"]["mustermann"]
    we05 = welt["units"][("623", "WE05")]
    target = services.Target(
        category="05",
        subfolder="04",
        document_type="eigentuemerkonto",
        owner_id=mustermann.pk,
        unit_id=we05.pk,
    )
    errors, _ = services.validate(case, target)
    assert any("neue Zuordnung" in e for e in errors)
    from datetime import date

    target.new_assignment_from = date(2026, 9, 1)
    decision = services.apply_decision(case, admin_user, target)
    assignment = OwnerUnitAssignment.active.get(owner=mustermann, unit=we05)
    assert (
        assignment.valid_from == date(2026, 9, 1)
        and assignment.data_status == "confirmed"
        and assignment.source_document_id == doc.pk
    )
    assert decision.label_assignment_id == assignment.pk


def test_zurueckstellen_zuweisen_verwerfen_wiedereroeffnen(
    welt, fake_oauth, run_all, client_as, admin_user, clerk_user
):
    doc, case = make_case_document(welt, run_all, UNKLAR)
    client = client_as(clerk_user)
    resp = client.post(
        reverse("review_action", args=[case.pk]),
        {"action": "defer", "days": "3", "reason": "Rückfrage", "query": ""},
    )
    assert resp.status_code == 302
    case.refresh_from_db()
    assert case.snoozed_until is not None
    assert case.pk not in [c.pk for c in services.page(services.ListFilters())[0]]
    assert case.pk in [c.pk for c in services.page(services.ListFilters(include_snoozed=True))[0]]
    client.post(reverse("review_action", args=[case.pk]), {"action": "assign", "assignee": "", "query": ""})
    case.refresh_from_db()
    assert case.assigned_to == clerk_user and case.status == "in_progress"
    # Objektfall nur Admin verwerfen (B-17)
    objcase = ReviewCase.objects.create(
        object=doc.object,
        case_type="drive_structure",
        case_subtype="candidate_in_trash",
        context={"reason": "Test"},
    )
    resp = client.post(
        reverse("review_action", args=[objcase.pk]),
        {"action": "dismiss", "reason": "kein Kandidat", "query": ""},
    )
    assert resp.status_code == 403
    resp = client_as(admin_user).post(
        reverse("review_action", args=[objcase.pk]),
        {"action": "dismiss", "reason": "kein Kandidat", "query": ""},
    )
    assert resp.status_code == 302
    objcase.refresh_from_db()
    assert (
        objcase.status == "dismissed"
        and AuditEvent.objects.filter(action="review.dismiss_object_case", entity_id=objcase.pk).exists()
    )
    # Verwerfen ohne Grund scheitert, mit Grund gelingt; Wiedereroeffnen
    resp = client.post(
        reverse("review_action", args=[case.pk]), {"action": "dismiss", "reason": "", "query": ""}
    )
    case.refresh_from_db()
    assert case.status == "in_progress"
    client.post(
        reverse("review_action", args=[case.pk]), {"action": "dismiss", "reason": "Werbung", "query": ""}
    )
    case.refresh_from_db()
    assert (
        case.status == "dismissed"
        and ReviewDecision.objects.filter(review_case=case, decision_type="reject").exists()
    )
    client.post(
        reverse("review_action", args=[case.pk]), {"action": "reopen", "reason": "doch relevant", "query": ""}
    )
    case.refresh_from_db()
    assert (
        case.status == "open"
        and ReviewDecision.objects.filter(review_case=case, decision_type="revert").exists()
    )
    assert AuditEvent.objects.filter(action="review.reopen", entity_id=case.pk).exists()


def test_dublette_aufteilen_uebernahme(welt, fake_oauth, run_all, client_as, admin_user):
    original, _ = make_case_document(
        welt,
        run_all,
        {
            "filename": "Einzelabrechnung_2025_WE03.pdf",
            "pages": [
                H623
                + "Einzelabrechnung 2025 für Einheit WE03\nEigentümer: Max Mustermann. Abrechnungsjahr 2025. Abrechnungsergebnis: Nachzahlung 312,40 EUR."
            ],
        },
    )
    doc, case = make_case_document(welt, run_all, UNKLAR)
    client = client_as(admin_user)
    resp = client.post(
        reverse("review_action", args=[case.pk]), {"action": "merge", "original_id": original.pk, "query": ""}
    )
    assert resp.status_code == 302
    doc.refresh_from_db()
    assert doc.status == "duplicate" and doc.duplicate_of_id == original.pk and doc.subfolder.code == "03"
    assert ProcessingJob.objects.filter(
        document=doc, job_type=JobType.FILE_TO_DRIVE, payload__subfolder="03"
    ).exists()
    # Aufteilen: Gesamtdokument mit zwei Bereichen
    pages = [
        H623 + "Sammelmappe Hausgeldabrechnungen 2025",
        H623 + "Einzelabrechnung 2025 WE03 Mustermann",
        H623 + "Einzelabrechnung 2025 WE07 Beispiel",
    ]
    master, mcase = make_case_document(welt, run_all, {"filename": "Sammelmappe.pdf", "pages": pages})
    if mcase is None:
        mcase = ReviewCase.objects.create(
            object=master.object, case_type="unclear", document=master, context={"reason": "Test"}
        )
    segments = [
        {
            "page_from": 2,
            "page_to": 2,
            "category": "05",
            "subfolder": "05",
            "document_type": "einzelabrechnung",
            "period_year": "2025",
            "unit_id": str(welt["units"][("623", "WE03")].pk),
            "owner_id": str(welt["owners"]["mustermann"].pk),
        },
        {
            "page_from": 3,
            "page_to": 3,
            "category": "05",
            "subfolder": "05",
            "document_type": "einzelabrechnung",
            "period_year": "2025",
            "unit_id": str(welt["units"][("623", "WE07")].pk),
            "owner_id": str(welt["owners"]["beispiel"].pk),
        },
    ]
    resp = client.post(
        reverse("review_action", args=[mcase.pk]),
        {"action": "split", "segments": json.dumps(segments), "query": ""},
    )
    assert resp.status_code == 302, resp.content
    master.refresh_from_db()
    links = list(
        DocumentOwnerLink.objects.filter(
            document=master, link_kind="page_range", status="confirmed"
        ).order_by("page_from")
    )
    assert [(link.page_from, link.owner.last_name) for link in links] == [(2, "Mustermann"), (3, "Beispiel")]
    assert (
        master.is_master_with_segments
        and ReviewDecision.objects.filter(document=master, decision_type="split").count() == 2
    )
    with pytest.raises(services.ReviewError):
        services.split(
            mcase,
            admin_user,
            [
                {"page_from": 1, "page_to": 3, "category": "05"},
                {"page_from": 3, "page_to": 3, "category": "05"},
            ],
        )
    # Uebernahme in anderes Objekt
    fremd, fcase = make_case_document(
        welt,
        run_all,
        {
            "filename": "Rechnung_Dachdecker.pdf",
            "pages": [
                "Dachdeckerei Muster GmbH\nRechnung Nr. 2026-0815\nObjekt: Beispielweg 2, Musterstadt. Betrag 1.845,20 EUR."
            ],
        },
    )
    assert fcase.proposed_action["object_number"] == "631"
    resp = client.post(
        reverse("review_action", args=[fcase.pk]),
        {
            "action": "transfer",
            "target_object": welt["objects"]["631"].pk,
            "reason": "Fremdobjekt",
            "query": "",
        },
    )
    assert resp.status_code == 302
    fremd.refresh_from_db()
    fcase.refresh_from_db()
    assert (
        fremd.status == "moved_out"
        and fcase.status == "resolved"
        and fcase.resolution["object_id"] == welt["objects"]["631"].pk
    )
    assert Document.objects.filter(object=welt["objects"]["631"], source="moved_in").exists()


def test_seitenbild_ohne_sitzung_und_akte_ohne_recht(
    welt, fake_oauth, run_all, client, client_as, clerk_user
):
    doc, case = make_case_document(
        welt,
        run_all,
        {
            "filename": "SEPA_Mandat_Mustermann.pdf",
            "pages": [
                H623
                + "SEPA-Lastschriftmandat\nZahlungspflichtiger: Max Mustermann, Einheit WE03. IBAN: DE89 3704 0044 0532 0130 00"
            ],
        },
    )
    assert doc.category_id == "05"
    resp = client.get(reverse("document_preview", args=[doc.pk, 1]))
    assert resp.status_code in (302, 401, 403)
    from apps.accounts.permissions import user_has_permission

    c = client_as(clerk_user)
    resp = c.get(reverse("document_detail", args=[doc.pk]))
    assert resp.status_code == (200 if user_has_permission(clerk_user, "owner_files.read") else 403)


def test_eigentuemersuche_und_einheiten(welt, fake_oauth, client_as, admin_user):
    obj = welt["objects"]["623"]
    client = client_as(admin_user)
    resp = client.get(reverse("review_owner_search", args=[obj.pk]), {"q": "must"})
    data = resp.json()["results"]
    assert data and data[0]["name"].endswith("Mustermann") and data[0]["in_object"]
    assert data[0]["units"][0]["unit"] == "WE03"
    resp = client.get(reverse("review_owner_search", args=[obj.pk]), {"q": "erst"})
    names = [r["name"] for r in resp.json()["results"]]
    assert any("Erste" in n for n in names)  # Objekt 624, gekennzeichnet
    assert any(not r["in_object"] for r in resp.json()["results"])
    assert services.owner_suggestions("m", obj) == []
    resp = client.get(reverse("review_units", args=[obj.pk]))
    assert len(resp.json()["results"]) == 13


def test_nachlauf_entprellt(welt, fake_oauth, run_all, admin_user):
    calls = []
    hooks.register(lambda object_id, key: calls.append((object_id, key)))
    obj = welt["objects"]["623"]
    hooks.reset_debounce(obj.pk)
    assert hooks.request_regeneration(obj.pk) is True
    assert hooks.request_regeneration(obj.pk) is False
    assert hooks.request_regeneration(obj.pk, bulk_key="gruppe-1") is True
    hooks.reset_debounce(obj.pk)
    hooks.reset_debounce(obj.pk, "gruppe-1")


def test_recording_adapter_keine_schreibzugriffe_in_anfragen(
    welt, fake_oauth, run_all, client_as, admin_user, monkeypatch
):
    doc, case = make_case_document(welt, run_all, EIGENTUEMERKONTO)
    recording = RecordingDriveAdapter(welt["drive"])
    from apps.drive import oauth

    monkeypatch.setattr(oauth, "get_adapter", lambda: recording)
    client = client_as(admin_user)
    client.get(reverse("review_list"))
    client.get(reverse("review_detail", args=[case.pk]))
    altmann = next(k for k in case.candidates if "Altmann" in k["owner"])
    client.post(
        reverse("review_action", args=[case.pk]),
        {
            "action": "confirm",
            "category": "05",
            "subfolder": "04",
            "document_type": "eigentuemerkonto",
            "owner_id": altmann["owner_id"],
            "unit_id": altmann["unit_id"],
            "assignment_id": altmann["assignment_id"],
            "query": "",
        },
    )
    assert recording.write_calls == []
