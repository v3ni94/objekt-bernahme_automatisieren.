"""Sollbestand der Stammakte (Checkliste 12.09.2026): Pruefpositionen der Kategorie stammakte werden generisch aus den
Seeds bewertet (Nachweisarten), zaehlen nicht in den Erfuellungsgrad, stehen zunaechst nicht in der Nachforderung und
lassen sich mit den Statusangaben der Checkliste uebersteuern."""

from __future__ import annotations

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.documents.models import Document, DocumentSubfolder, DocumentType
from apps.requirements import engine
from apps.requirements.models import CompletenessCheck, CompletenessFinding, RequestTextBlock

pytestmark = pytest.mark.django_db


def _dok(obj, code: str, status: str = "filed") -> Document:
    dtype = DocumentType.objects.get(code=code)
    return Document.objects.create(
        object=obj,
        sha256=f"{code:x<64}"[:64],
        size_bytes=10,
        mime_type="application/pdf",
        original_name=f"{code}.pdf",
        current_name=f"{code}.pdf",
        source="upload",
        status=status,
        category_id="02",
        subfolder=dtype.subfolder,
        document_type=dtype,
        first_seen_at=timezone.now(),
    )


def test_unterordner_der_stammakte_im_katalog(seeded):
    subs = list(DocumentSubfolder.objects.filter(category_id="02").order_by("sort_order"))
    assert [s.code for s in subs] == [f"{i:02d}" for i in range(1, 16)]
    assert subs[0].folder_name == "01_Objektstammdaten_und_Einheiten"
    assert subs[14].folder_name == "15_Übernahme_Fehlunterlagen_und_offene_Vorgänge"
    # vorhandene Arten der Stammakte haengen jetzt an ihrem Unterordner
    assert DocumentType.objects.get(code="energieausweis").subfolder.code == "04"
    assert DocumentType.objects.get(code="versammlungsprotokoll").subfolder.code == "06"
    assert DocumentType.objects.get(code="gebaeudeversicherung").subfolder.code == "08"
    assert DocumentType.objects.get(code="dienstleistervertrag").subfolder.code == "10"


def test_katalogposition_generisch_bewertet(objekt, admin_user):
    block = RequestTextBlock.objects.create(
        code="line.sa_test_energie", title="Test", sort_order=990, text="Energieausweis des Objekts{hinweis}"
    )
    CompletenessCheck.objects.create(
        code="sa_test_energie",
        name="Energieausweis",
        description="Energieausweis mit Ausstellungsdatum und Gültigkeit",
        scope_type="object",
        management_types=None,
        evidence_document_types=["energieausweis"],
        category="stammakte",
        blocking=False,
        sort_order=390,
        request_text_block=block,
    )
    engine.evaluate_object(objekt, trigger="test")
    f = CompletenessFinding.objects.get(object=objekt, check_code="sa_test_energie")
    assert f.status == "missing" and f.include_in_request is False
    assert f.details["recommendation"] is True and "Sollbestand" in f.details["hint"]
    summe = engine.summary(objekt)
    assert summe["catalog"]["missing"] >= 1
    # Empfehlungen zaehlen nicht in den Erfuellungsgrad
    kern = summe["counts"]
    assert kern["missing"] + kern["partial"] + kern["fulfilled"] == sum(
        1
        for x in CompletenessFinding.objects.filter(object=objekt)
        if not (x.details or {}).get("recommendation") and not (x.details or {}).get("consequential")
    )
    # Nachforderung: Position nicht enthalten, solange nicht angehakt
    assert not any(
        i["check_code"] == "sa_test_energie" and i["include_in_request"] for i in engine.open_items(objekt)
    )
    # Dokument in Pruefung: teilweise; bestaetigt: erfuellt mit Nachweis
    doc = _dok(objekt, "energieausweis", status="review")
    engine.evaluate_object(objekt, trigger="test")
    f.refresh_from_db()
    assert f.status == "partial" and f.evidence_document_id == doc.pk
    Document.objects.filter(pk=doc.pk).update(status="filed")
    engine.evaluate_object(objekt, trigger="test")
    f.refresh_from_db()
    assert f.status == "fulfilled" and f.evidence_document_id == doc.pk
    # Uebersteuerung mit den Statusangaben der Checkliste: teilweise ist jetzt zulaessig
    engine.set_manual(f, admin_user, status="partial", reason="Aktualität ungeklärt")
    f.refresh_from_db()
    assert engine.effective_status(f) == "partial" and f.manual_reason == "Aktualität ungeklärt"
    with pytest.raises(ValueError):
        engine.set_manual(f, admin_user, status="missing", reason="x")


def test_seite_zeigt_sollbestand_und_statusangaben(client_as, admin_user, objekt):
    engine.evaluate_object(objekt, trigger="test")
    resp = client_as(admin_user).get(reverse("completeness", args=[objekt.pk]))
    assert resp.status_code == 200
    inhalt = resp.content.decode()
    assert "Sollbestand Stammakte" in inhalt
    assert "vorhanden, aber unvollständig oder Aktualität ungeklärt" in inhalt
    assert 'value="not_applicable">nicht anwendbar' in inhalt
