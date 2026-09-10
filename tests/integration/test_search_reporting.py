"""M12: Suche (WE 14 und WE14 treffen dieselbe Einheit, Umlautvarianten, Volltexttreffer mit Seitenangabe, keine IBAN
im Index, Rechte je Rolle), Reporting (KPI brutto, aktuell und bereinigt nachrechenbar, Review-Alter, CSV), Statusseite
vollstaendig und unter zwei Sekunden, Alarmierung nur bei Freigabe mit Wiederholsperre und Entwarnung."""

from __future__ import annotations

import hashlib
import time
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.documents.models import (
    Document,
    DocumentClassification,
    DocumentOwnerLink,
    DocumentPage,
    DocumentType,
)
from apps.objects.models import ManagedObject, Unit
from apps.parties.models import Owner, OwnerUnitAssignment
from apps.reporting import services as reporting
from apps.review.models import ReviewCase
from apps.search import services as search
from apps.status import alerts, checks

pytestmark = pytest.mark.django_db
_n = {"i": 0}


@pytest.fixture
def objekt(seeded):
    return ManagedObject.objects.create(
        object_number="623",
        name="Musterstadt, Musterweg 1",
        management_type="weg",
        status="takeover",
        is_test=True,
    )


def make_doc(obj, type_code="einzelabrechnung", status="filed", name=None, pages=(), category=None):
    dt = DocumentType.objects.get(code=type_code)
    _n["i"] += 1
    doc = Document.objects.create(
        object=obj,
        sha256=hashlib.sha256(f"{obj.pk}-{_n['i']}".encode()).hexdigest(),
        size_bytes=100,
        mime_type="application/pdf",
        original_name=name or f"{type_code}_{_n['i']}.pdf",
        current_name=name or f"{type_code}_{_n['i']}.pdf",
        source="upload",
        status=status,
        first_seen_at=timezone.now(),
        document_type=dt,
        category=dt.category if category is None else category,
        subfolder=dt.subfolder,
        period_year=2025,
    )
    for i, text in enumerate(pages, start=1):
        DocumentPage.objects.create(
            document=doc,
            page_no=i,
            text_source="text_layer",
            is_scan=False,
            text_content=text,
            char_count=len(text),
        )
    return doc


def make_unit(obj, label):
    from apps.objects.units import normalize_label

    return Unit.objects.create(
        object=obj, unit_label=label, unit_label_normalized=normalize_label(label), unit_type="apartment"
    )


def test_einheit_und_umlautvarianten(objekt, admin_user):
    we14 = make_unit(objekt, "WE 14")
    make_unit(objekt, "WE 1")
    owner = Owner.objects.create(
        type="natural_person",
        first_name="Anna",
        last_name="Müllerbeispiel",
        search_name="MUELLERBEISPIEL ANNA",
    )
    OwnerUnitAssignment.objects.create(
        owner=owner, unit=we14, valid_from=date(2020, 1, 1), data_status="confirmed"
    )
    r1 = search.SearchIndex.query(search.Filters(unit="WE14"), admin_user)
    r2 = search.SearchIndex.query(search.Filters(unit="WE 14"), admin_user)
    assert [h.title for h in r1.units] == [h.title for h in r2.units] == ["WE 14 (Wohnung)"]
    assert r1.units[0].extra["owners"] == [str(owner)]
    for needle in ("Müllerbeispiel", "Muellerbeispiel", "muellerb"):
        r = search.SearchIndex.query(search.Filters(owner=needle), admin_user)
        assert [h.title for h in r.owners] == [str(owner)], needle
        assert "WE 14" in r.owners[0].subtitle and "01.01.2020" in r.owners[0].subtitle
    assert search.umlaut_variants("Grundstuecksverkehr") == [
        "grundstücksverkehr",
        "Grundstuecksverkehr".lower(),
    ] or set(search.umlaut_variants("Grundstuecksverkehr")) == {"Grundstuecksverkehr", "grundstücksverkehr"}
    expr, short = search.fulltext_expression("OG Grundstücksverkehr", 3)
    assert (
        short == ["OG"]
        and expr.startswith("+(")
        and "grundstuecksverkehr" in expr
        and "Grundstücksverkehr" in expr
    )
    assert search.SearchIndex.query(search.Filters(), admin_user).total == 0


@pytest.mark.django_db(transaction=True)
def test_volltext_mit_seitenangabe_und_iban_pruefung(seeded, admin_user):
    obj = ManagedObject.objects.create(
        object_number="624", name="Musterstadt, Beispielweg 2", management_type="weg", is_test=True
    )
    doc = make_doc(
        obj,
        pages=[
            "Einzelabrechnung 2025 Deckblatt",
            "Position Grundstücksverkehr und Dachsanierung, Konto [IBAN_****1234]",
        ],
    )
    make_doc(obj, pages=["Wirtschaftsplan ohne Bezug"])
    r = search.SearchIndex.query(search.Filters(text="Grundstuecksverkehr", object_number="624"), admin_user)
    assert [h.title for h in r.documents] == [doc.current_name] and r.documents[0].page_no == 2
    assert "Grundstücksverkehr" in r.documents[0].snippet and r.documents[0].object_number == "624"
    r2 = search.SearchIndex.query(search.Filters(text="Dachsanierung Grundstücksverkehr"), admin_user)
    assert len(r2.documents) == 1 and r2.documents[0].extra["period_year"] == 2025
    assert search.SearchIndex.query(search.Filters(text="Kellerfenster"), admin_user).documents == []
    assert search.iban_index_check() == 0  # maskierter Text: keine IBAN im Volltextindex (CR 10)


def test_rechte_und_filter(objekt, admin_user, clerk_user):
    we = make_unit(objekt, "WE 3")
    owner = Owner.objects.create(
        type="natural_person", first_name="Max", last_name="Mustermann", search_name="MUSTERMANN MAX"
    )
    a = OwnerUnitAssignment.objects.create(
        owner=owner, unit=we, valid_from=date(2020, 1, 1), data_status="confirmed"
    )
    doc = make_doc(objekt, "einzelabrechnung")
    DocumentOwnerLink.objects.create(
        document=doc,
        link_kind="whole_document",
        owner=owner,
        unit=we,
        assignment=a,
        document_type=doc.document_type,
        subfolder=doc.subfolder,
        period_year=2025,
        confidence=1,
        status="confirmed",
    )
    ReviewCase.objects.create(object=objekt, case_type="unclear", document=doc, context={}, batch_key="t1")
    f = search.Filters(
        owner="Mustermann", year=2025, document_type="einzelabrechnung", category="05", status="final"
    )
    r = search.SearchIndex.query(f, clerk_user)
    assert [h.title for h in r.documents] == [doc.current_name] and r.documents[0].extra["review_open"]
    assert r.documents[0].extra["spans"] == [] and "Einzelabrechnung" in r.documents[0].subtitle
    leser = Role.objects.create(code="leser", name="Leser", permissions=["status.read"], is_system=False)
    user = User.objects.create_user(
        "leser@example.test", "Startpasswort-12x", role=leser, display_name="Nur Lesen"
    )
    r = search.SearchIndex.query(f, user)
    assert r.documents == [] and r.owners == []  # Eigentuemerakte nur mit owner_files.read (CR 10)
    r = search.SearchIndex.query(search.Filters(year=2024), admin_user)
    assert r.documents == []


def test_kpi_nachrechenbar_und_csv(objekt, admin_user):
    for i in range(10):
        doc = make_doc(objekt, "einzelabrechnung" if i else "kontoauszug", status="filed")
        if i == 0:
            doc.category_id, doc.status = "06", "review"
            doc.save()
            DocumentClassification.objects.create(
                document=doc,
                stage=1,
                provider="rules",
                category_id="03",
                confidence=Decimal("0.7"),
                is_final=True,
                features={"kpi_misc_adjusted": True},
            )
    old_case = ReviewCase.objects.create(object=objekt, case_type="unclear", context={}, batch_key="alt")
    ReviewCase.objects.filter(pk=old_case.pk).update(created_at=timezone.now() - timedelta(days=30))
    ReviewCase.objects.create(object=objekt, case_type="owner_candidates", context={}, batch_key="neu")
    k = reporting.object_kpi(objekt, today=timezone.localdate())
    assert k.docs_total == 10 and k.docs_classified == 10 and k.misc_current == 1
    assert k.misc_share_current == 10.0 and k.misc_share_adjusted == 10.0 and k.misc_share_run is None
    assert k.review_open == 2 and k.review_by_type == {"unclear": 1, "owner_candidates": 1}
    assert k.review_age_max > 10 and k.review_age_warning and k.completeness_status == "not_evaluated"
    assert reporting.business_days(date(2026, 6, 1), date(2026, 6, 8)) == 5
    rows = reporting.overview(today=timezone.localdate())
    csv_text = reporting.overview_csv(rows)
    lines = csv_text.strip().split("\r\n")
    assert lines[0].startswith("Objekt;Bezeichnung;") and len(lines) == 2
    assert lines[1].startswith(
        "623;Musterstadt, Musterweg 1;WEG;in Übernahme;10;10;;10,00;10,00;not_evaluated;"
    )


def test_statusseite_und_berichte(objekt, admin_user, client_as, settings, tmp_path):
    settings.OBJEKTAKTE = {**settings.OBJEKTAKTE, "DATA_DIR": tmp_path}
    client = client_as(admin_user)
    started = time.perf_counter()
    resp = client.get(reverse("status_page"))
    assert resp.status_code == 200 and time.perf_counter() - started < 2.0
    html = resp.content.decode()
    for section in (
        "Review",
        "Google Drive",
        "Speicher",
        "Konfiguration",
        "Alarmbedingungen",
        "Stufe 3 (externe KI)",
        "Verarbeitung je Objekt",
    ):
        assert section in html, section
    assert "ALERTS_ENABLED" in html
    resp = client.get(reverse("report_overview"))
    assert resp.status_code == 200 and "623" in resp.content.decode()
    resp = client.get(reverse("report_overview"), {"format": "csv"})
    assert (
        resp.status_code == 200 and resp["Content-Type"].startswith("text/csv") and b"Objekt;" in resp.content
    )
    resp = client.get(reverse("report_object", args=[objekt.pk]))
    assert resp.status_code == 200 and "Anteil 06_Sonstiges" in resp.content.decode()
    resp = client.get(reverse("search"), {"einheit": "WE 1"})
    assert resp.status_code == 200 and "Suchwörter ab" in resp.content.decode()


def test_alarmierung_nur_bei_freigabe(seeded, settings, monkeypatch):
    monkeypatch.setattr(checks, "backup_status", lambda: {"ok": False, "status": "failed", "age_h": 40.0})
    monkeypatch.setattr(
        checks,
        "heartbeats",
        lambda: {"worker": {"ok": True, "age_s": 5}, "worker-io": {"ok": True, "age_s": 5}},
    )
    monkeypatch.setattr(checks, "oauth_status", lambda: {"ok": True, "status": "active"})
    settings.OBJEKTAKTE = {
        **settings.OBJEKTAKTE,
        "ALERTS_ENABLED": False,
        "ALERT_EMAIL_TO": "alarm@example.test",
    }
    result = alerts.check_alerts(check_certificate=False)
    assert (
        "backup" in result["active"]
        and result["sent"] == []
        and not result["enabled"]
        and len(mail.outbox) == 0
    )
    settings.OBJEKTAKTE = {**settings.OBJEKTAKTE, "ALERTS_ENABLED": True}
    result = alerts.check_alerts(check_certificate=False)
    assert (
        result["sent"] == ["backup"] and len(mail.outbox) == 1 and "Warnung backup" in mail.outbox[0].subject
    )
    assert mail.outbox[0].to == ["alarm@example.test"] and "Schwelle 26 h" in mail.outbox[0].body
    result = alerts.check_alerts(check_certificate=False)
    assert result["sent"] == [] and len(mail.outbox) == 1  # hoechstens einmal je sechs Stunden
    monkeypatch.setattr(checks, "backup_status", lambda: {"ok": True, "status": "ok", "age_h": 1.0})
    result = alerts.check_alerts(check_certificate=False)
    assert (
        result["cleared"] == ["backup"]
        and len(mail.outbox) == 2
        and "Entwarnung backup" in mail.outbox[1].subject
    )
    assert alerts._certificate("localhost").active is False
