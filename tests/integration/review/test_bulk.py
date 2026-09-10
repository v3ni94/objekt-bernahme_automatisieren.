"""Massenbearbeitung (H 2.5, M7 Ende-zu-Ende): Gesamtabrechnung 2025 mit 40 Einzelabrechnungen, 37 eindeutig, 2 mit
Kandidaten (Eigentuemerwechsel 2025), 1 mit unbekannter Einheit; Vorschau ohne Schreibwirkung, Konfliktzeilen
ausgenommen, 39 Entscheidungen in einer Aktion, Vorschaupfade gleich Benennungsfunktion, genau eine Listenerzeugung,
Audit vollstaendig."""

from __future__ import annotations

from datetime import date

import pytest
from django.urls import reverse
from tests.integration.classification.conftest import make_document

from apps.audit.models import AuditEvent
from apps.documents.models import DocumentOwnerLink
from apps.drive.naming import OwnerFileNamingConfig, OwnerNameInput, OwnerNamePart, build_owner_folder_name
from apps.objects.models import Unit
from apps.objects.units import normalize_label
from apps.parties import services as party_services
from apps.parties.models import Owner
from apps.pipeline.runs import start_run
from apps.review import hooks, services
from apps.review.models import ReviewCase, ReviewDecision

pytestmark = pytest.mark.django_db
H623 = "Objekt 623 Düsseldorf, Joachimstraße 49\n"


def _owner(first, last, unit, valid_from, valid_to=None):
    owner = Owner.objects.create(
        type="natural_person", first_name=first, last_name=last, search_name=f"{last} {first}".upper()
    )
    party_services.create_assignment(owner=owner, unit=unit, valid_from=valid_from, valid_to=valid_to)
    return owner


@pytest.fixture
def grossobjekt(welt):
    """Objekt 623 um Einheiten WE13 bis WE40 erweitern; WE40 mit Eigentuemerwechsel zum 01.07.2025."""
    obj = welt["objects"]["623"]
    owners = {}
    for n in range(13, 41):
        label = f"WE{n:02d}"
        unit = Unit.objects.create(
            object=obj,
            unit_label=label,
            unit_label_normalized=normalize_label(label),
            unit_number=str(n),
            unit_type="apartment",
        )
        if n == 40:
            _owner("Alt", f"Wechsler{n}", unit, date(2015, 1, 1), date(2025, 6, 30))
            _owner("Neu", f"Nachfolger{n}", unit, date(2025, 7, 1))
        elif n == 39:
            _owner("Erstin", f"Erste{n}", unit, date(2015, 1, 1), date(2025, 3, 31))
            _owner("Zweiter", f"Zweite{n}", unit, date(2025, 4, 1))
        else:
            owners[label] = _owner("Test", f"Eigner{n}", unit, date(2015, 1, 1))
    return obj


def _ea_page(unit_label: str, owner_name: str, page_no: int, total: int) -> str:
    return f"{H623}Einzelabrechnung 2025 für Einheit {unit_label} (Anlage zur Gesamtabrechnung)\nEigentümer: {owner_name}, Einheit {unit_label}. Abrechnungsergebnis: Nachzahlung 120,00 EUR.\nSeite {page_no} von {total}"


def test_vierzig_segmente_sammelaktion(
    grossobjekt, welt, fake_oauth, run_all, client_as, admin_user, django_capture_on_commit_callbacks
):
    obj = grossobjekt
    units = {u.unit_label: u for u in Unit.active.filter(object=obj)}
    labels = [f"WE{n:02d}" for n in range(1, 41)] + ["WE 41"]
    pages = [
        f"{H623}Gesamtabrechnung 2025 der Wohnungseigentümergemeinschaft Joachimstraße 49\nAbrechnungsjahr 2025. Anzahl Einheiten: 40.\nSeite 1 von 42"
    ]
    for i, label in enumerate(labels, start=2):
        # Namen der 39 und 40 bewusst weglassen (nur Einheit erkennbar -> Kandidaten); 41 existiert nicht
        owner_name = "[siehe Anlage]" if label in ("WE39", "WE40", "WE 41") else "Eigentümer laut Liste"
        pages.append(_ea_page(label, owner_name, i, 42))
    master = make_document(obj, {"filename": "Jahresabrechnung_2025_komplett.pdf", "pages": pages})
    calls: list = []
    hooks.register(lambda object_id, key: calls.append((object_id, key)))
    hooks.reset_debounce(obj.pk)
    start_run(obj)
    run_all(obj)
    master.refresh_from_db()
    assert master.category_id == "03" and master.is_master_with_segments
    cases = list(ReviewCase.objects.filter(document=master, status="open").order_by("page_from"))
    assert len(cases) == 41  # 40 Segmente plus Bereich WE 41 (Einheit unbekannt)
    keys = {c.batch_key for c in cases}
    assert len(keys) == 1, (
        keys
    )  # eine Gruppe: Objekt, Masterdokument, 05, 05_Abrechnungen, Einzelabrechnung, 2025
    clear = [c for c in cases if c.case_subtype == "segment_proposed"]
    candidates = [c for c in cases if c.case_subtype in ("multiple_owners", "no_period")]
    unknown = [c for c in cases if c.case_subtype in ("no_owner_no_unit",)]
    assert len(clear) == 38 and len(candidates) == 2 and len(unknown) == 1, [
        (c.page_from, c.case_subtype) for c in cases
    ]
    client = client_as(admin_user)
    # Vorschau ohne Schreibwirkung
    decisions_before = ReviewDecision.objects.count()
    resp = client.get(reverse("review_bulk"), {"batch_key": cases[0].batch_key})
    assert resp.status_code == 200
    rows = resp.context["rows"]
    assert len(rows) == 41 and resp.context["green"] == 38 and resp.context["red"] >= 1
    assert ReviewDecision.objects.count() == decisions_before
    # Vorschaupfade stimmen mit der Benennungsfunktion ueberein
    cfg = OwnerFileNamingConfig.from_settings()
    for row in rows:
        if row.state != "gruen":
            continue
        unit = units[row.unit]
        owner = Owner.objects.get(pk=row.target.owner_id)
        expected = build_owner_folder_name(
            OwnerNameInput(
                file_kind="unit_owner",
                unit_label=unit.unit_label,
                unit_type=unit.unit_type,
                owner_names=(
                    OwnerNamePart(
                        kind=owner.type, last_name=owner.last_name, company_name=owner.company_name
                    ),
                ),
            ),
            cfg,
        )
        assert row.folder_name.split("/")[1] == expected, (row.folder_name, expected)
    # zwei gelbe Zeilen: Kandidaten setzen (Eigentuemer 2025: fuer WE40 zwei Kandidaten, Wahl des Nachfolgers)
    row_overrides = {}
    for c in candidates:
        chosen = c.candidates[-1]
        row_overrides[f"row_{c.pk}_assignment_id"] = str(chosen["assignment_id"])
        row_overrides[f"row_{c.pk}_owner_id"] = str(chosen["owner_id"])
        row_overrides[f"row_{c.pk}_unit_id"] = str(chosen["unit_id"])
    # rote Zeile ausschliessen, 39 ausfuehren
    data = {"case_id": [c.pk for c in cases], "exclude": [unknown[0].pk], **row_overrides}
    with django_capture_on_commit_callbacks(execute=True):
        resp = client.post(reverse("review_bulk_execute"), data)
    assert resp.status_code == 302, resp.content
    decisions = ReviewDecision.objects.filter(document=master, is_bulk=True)
    assert decisions.count() == 40, decisions.count()  # 38 eindeutig plus 2 mit gewaehltem Kandidaten
    bulk_key = decisions.first().bulk_key
    assert decisions.exclude(bulk_key=bulk_key).count() == 0
    assert (
        ReviewCase.objects.filter(document=master, status="open").count() == 1
    )  # die rote Zeile bleibt offen
    assert ReviewCase.objects.get(document=master, status="open").pk == unknown[0].pk
    assert (
        DocumentOwnerLink.objects.filter(document=master, link_kind="page_range", status="confirmed").count()
        >= 40
    )
    # Audit vollstaendig: je Entscheidung eine Zeile plus eine Zeile fuer die Sammelaktion
    assert (
        AuditEvent.objects.filter(
            action__in=["review.confirm", "review.reclassify"], user_id=admin_user.pk
        ).count()
        >= 40
    )
    assert AuditEvent.objects.filter(action="review.bulk_execute").count() == 1
    # genau eine Listenerzeugung fuer die Gruppe (Entprellung ueber bulk_key)
    assert len([c for c in calls if c[1] == bulk_key]) == 1
    # Statusseite der Sammelaktion
    resp = client.get(reverse("review_bulk_status", args=[bulk_key]))
    assert resp.status_code == 200 and "done" in resp.content.decode()
    resp = client.get(reverse("review_bulk_status", args=[bulk_key]), {"format": "json"})
    assert resp.json()["status"] == "done" and resp.json()["ok"] == 40


def test_sammelaktion_blockiert_bei_konflikt(grossobjekt, welt, fake_oauth, run_all, client_as, admin_user):
    obj = grossobjekt
    pages = [
        f"{H623}Gesamtabrechnung 2025 der Wohnungseigentümergemeinschaft Joachimstraße 49\nAbrechnungsjahr 2025.\nSeite 1 von 3",
        _ea_page("WE03", "Max Mustermann", 2, 3),
        _ea_page("WE 41", "[siehe Anlage]", 3, 3),
    ]
    master = make_document(obj, {"filename": "Sammel.pdf", "pages": pages})
    start_run(obj)
    run_all(obj)
    cases = list(ReviewCase.objects.filter(document=master, status="open"))
    client = client_as(admin_user)
    resp = client.post(reverse("review_bulk_execute"), {"case_id": [c.pk for c in cases]})
    assert resp.status_code == 302 and "/review/sammel/" in resp["Location"]
    assert ReviewDecision.objects.filter(document=master).count() == 0
    rows = services.bulk_rows([c.pk for c in cases])
    assert sorted(r.state for r in rows) == ["gruen", "rot"]
    result = services.bulk_execute(
        [c.pk for c in cases], admin_user, exclude=[r.case_id for r in rows if r.errors]
    )
    assert result["total"] == 1 and len(result["ok"]) == 1 and result["failed"] == []
