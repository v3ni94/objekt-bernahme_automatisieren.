"""Requirement Engine (M10, CR 12): Katalog wortgetreu, Zeitraumlogik (Stichtag 01.07.2026, Kalenderjahr, drei
Jahre ergibt 2023 bis 2026; Abrechnung 2025 vor dem 30.06.2026 noch nicht faellig), Bewertung je Pruefpunkt mit
missing, partial, fulfilled, not_applicable, Folgefehler, Zusammenfassung, Offene Punkte, manuelle Uebersteuerung,
Idempotenz, Mietkatalog, Laufzeit bei 100 Einheiten und vier Jahren (ANNAHME A-34)."""

from __future__ import annotations

import time
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.parties.models import Lease, Tenant, TenantUnitAssignment
from apps.requirements import engine
from apps.requirements.models import CompletenessCheck, CompletenessFinding

from .conftest import TODAY, assign, link, make_doc, make_owner, make_unit

pytestmark = pytest.mark.django_db
CR12 = [
    "vollständige Eigentümerliste",
    "alle Einheiten erfasst",
    "Eigentümer je Einheit bekannt",
    "Eigentümerwechsel erfasst",
    "Anschriften",
    "Kommunikationsdaten",
    "Eigentümerkonten",
    "offene Hausgelder",
    "Guthaben",
    "Einzelabrechnungen (je Jahr im Übernahmezeitraum)",
    "Wirtschaftspläne",
    "SEPA-Mandate (soweit verwendet)",
    "Sonderumlagen je Eigentümer",
    "laufende Zahlungsvereinbarungen",
    "laufende Mahnverfahren",
]


def status_of(obj, code, *, unit=None, assignment=None, year=None):
    f = CompletenessFinding.objects.get(
        object=obj, check_code=code, unit=unit, assignment=assignment, period_year=year
    )
    return f.status, (f.details or {}).get("hint", "")


def test_katalog_wortgetreu_aus_cr12(seeded):
    weg = list(CompletenessCheck.objects.filter(sort_order__lt=200).order_by("sort_order"))
    assert [c.name for c in weg] == CR12
    assert all(c.request_text_block is not None and c.category for c in weg)
    assert CompletenessCheck.objects.filter(sort_order__gte=200).count() == 9  # Mietkatalog (Vorschlag H 3.6)


def test_zeitraumlogik(objekt):
    p = engine.period_for(objekt, TODAY)
    assert p.years == [2023, 2024, 2025, 2026] and p.start == date(2023, 1, 1) and p.end == date(2026, 7, 1)
    assert not p.provisional and p.plan_years == p.years and any("Kalenderjahr" in h for h in p.hints)
    assert engine.statement_due(2025, 1) == date(2026, 6, 30)
    objekt.fiscal_year_start_month, objekt.takeover_from, objekt.takeover_to = (
        7,
        date(2024, 7, 1),
        date(2026, 11, 30),
    )
    p2 = engine.period_for(objekt, TODAY)
    assert p2.years == [2024, 2025, 2026] and p2.provisional and p2.plan_years == [2024, 2025, 2026]
    assert engine.statement_due(2024, 7) == date(2025, 6, 30) + timedelta(days=181)
    objekt.takeover_to = date(2026, 5, 15)  # letztes Quartal des Wirtschaftsjahres ab Juli: Plan Folgejahr
    assert engine.period_for(objekt, TODAY).plan_years == [2024, 2025, 2026]
    objekt.takeover_to = date(2026, 4, 15)
    assert engine.period_for(objekt, TODAY).plan_years == [2024, 2025, 2026]
    objekt.fiscal_year_start_month, objekt.takeover_from, objekt.takeover_to = 1, None, date(2026, 11, 15)
    assert engine.period_for(objekt, TODAY).plan_years == [2023, 2024, 2025, 2026, 2027]


def test_bewertung_weg_katalog(objekt, szenario, admin_user):
    a1, a2 = szenario["assignments"]["a1"], szenario["assignments"]["a2"]
    we01, we02, we03 = (szenario["units"][k] for k in ("WE01", "WE02", "WE03"))
    result = engine.evaluate_object(objekt, today=TODAY, user=admin_user)
    assert (
        result["findings"] > 20
        and result["created"] == result["findings"]
        and result["period"]["years"] == [2023, 2024, 2025, 2026]
    )
    # 1 Eigentuemerliste in Pruefung -> partial; nach Ablage fulfilled
    assert status_of(objekt, "owner_list_complete")[0] == "partial"
    # 2 Einheiten: Anzahl stimmt, MEA nicht erfasst
    assert status_of(objekt, "all_units_known") == (
        "fulfilled",
        "Anzahl stimmt, Miteigentumsanteile nicht vollständig erfasst",
    )
    # 3 Eigentuemer je Einheit
    assert status_of(objekt, "owner_per_unit", unit=we01)[0] == "fulfilled"
    assert status_of(objekt, "owner_per_unit", unit=we02)[0] == "partial"
    assert status_of(objekt, "owner_per_unit", unit=we03)[0] == "missing"
    # 4 Eigentuemerwechsel: WE01 kein Wechsel im Zeitraum, WE02 Beginn unbekannt, WE03 Folgefehler
    assert status_of(objekt, "owner_changes", unit=we01) == (
        "fulfilled",
        "kein Eigentümerwechsel im Übernahmezeitraum",
    )
    assert status_of(objekt, "owner_changes", unit=we02)[0] == "missing"
    f3 = CompletenessFinding.objects.get(object=objekt, check_code="owner_changes", unit=we03)
    assert f3.status == "missing" and f3.details["consequential"]
    # 5, 6 Stammdaten
    assert status_of(objekt, "addresses", unit=we01, assignment=a1)[0] == "fulfilled"
    assert status_of(objekt, "addresses", unit=we02, assignment=a2)[0] == "partial"
    assert status_of(objekt, "contact_data", unit=we01, assignment=a1)[0] == "fulfilled"
    assert status_of(objekt, "contact_data", unit=we02, assignment=a2)[0] == "missing"
    # 7 bis 9
    assert status_of(objekt, "owner_accounts", unit=we01, assignment=a1)[0] == "missing"
    assert (
        status_of(objekt, "open_house_fees")[0] == "missing" and status_of(objekt, "credits")[0] == "missing"
    )
    assert status_of(objekt, "open_house_fees", unit=we01, assignment=a1) == ("fulfilled", "Saldo erfasst")
    assert status_of(objekt, "credits", unit=we02, assignment=a2)[0] == "missing"
    # 10 Einzelabrechnungen: 2023 vorgeschlagen, 2024 bestaetigt, 2025 faellig seit 30.06.2026, 2026 noch nicht
    assert status_of(objekt, "annual_statement_year", unit=we01, assignment=a1, year=2023)[0] == "partial"
    assert status_of(objekt, "annual_statement_year", unit=we01, assignment=a1, year=2024)[0] == "fulfilled"
    assert status_of(objekt, "annual_statement_year", unit=we01, assignment=a1, year=2025)[0] == "missing"
    st, hint = status_of(objekt, "annual_statement_year", unit=we01, assignment=a1, year=2026)
    assert st == "not_applicable" and "noch nicht fällig" in hint and "30.06.2027" in hint
    # 11 Wirtschaftsplaene fehlen fuer alle vier Jahre
    assert all(
        status_of(objekt, "business_plan_year", unit=we01, assignment=a1, year=y)[0] == "missing"
        for y in (2023, 2024, 2025, 2026)
    )
    # 12 SEPA: Nutzung im Objekt offen, Mustermann mit Mandat, Beispiel unbekannt
    assert status_of(objekt, "sepa_mandate") == ("missing", "Klärung SEPA-Nutzung im Objekt")
    assert status_of(objekt, "sepa_mandate", unit=we01, assignment=a1)[0] == "fulfilled"
    assert status_of(objekt, "sepa_mandate", unit=we02, assignment=a2)[0] == "missing"
    # 13 bis 15 Negativnachweis auf Objektebene, je Zuordnung nur positiv
    assert status_of(objekt, "special_levy")[0] == "missing"
    assert not CompletenessFinding.objects.filter(
        object=objekt, check_code="special_levy", assignment=a1
    ).exists()
    assert status_of(objekt, "payment_agreement") == ("missing", engine.NEGATIVE_PROOF_HINT)
    assert status_of(objekt, "payment_agreement", unit=we01, assignment=a1)[0] == "not_applicable"
    assert status_of(objekt, "dunning_procedure")[0] == "missing"
    # Alteigentuemer vor dem Zeitraum erhaelt keine Zuordnungsfindings
    assert not CompletenessFinding.objects.filter(
        object=objekt, assignment=szenario["assignments"]["a_alt"]
    ).exists()
    # Zusammenfassung (H 3.4)
    s = engine.summary(objekt)
    assert s["status"] == "incomplete" and s["blocking_missing"] and s["unit_counts"]["not_evaluable"] == 1
    assert s["units"][we03.pk]["status"] == "not_evaluable" and s["units"][we01.pk]["status"] == "incomplete"
    assert 0 < s["ratio"] < 1 and s["missing_by_category"]["agreements"] == 1
    items = engine.open_items(objekt)
    assert (
        items and items[0]["unit_label"] == "" and all(i["status"] in ("missing", "partial") for i in items)
    )
    assert not any(
        i["check_code"] == "owner_changes" and i["unit_label"] == "WE03" for i in items
    )  # Folgefehler
    assert AuditEvent.objects.filter(action="completeness.evaluate", entity_id=objekt.pk).count() == 1
    # Aenderungen: Liste abgelegt, SEPA-Nutzung erfasst, Sonderumlagen nein, Saldo fuer Beispiel
    liste = szenario["docs"]["liste"]
    liste.status = "filed"
    liste.save(update_fields=["status"])
    objekt.sepa_used, objekt.special_levies_in_period = True, False
    objekt.save()
    engine.evaluate_object(objekt, today=TODAY)
    assert status_of(objekt, "owner_list_complete")[0] == "fulfilled"
    assert (
        status_of(objekt, "sepa_mandate")[0] == "fulfilled"
        and status_of(objekt, "special_levy")[0] == "not_applicable"
    )
    assert status_of(objekt, "special_levy", unit=we01, assignment=a1)[0] == "not_applicable"
    # Vor der Faelligkeit: Abrechnung 2025 noch nicht faellig
    engine.evaluate_object(objekt, today=date(2026, 5, 1))
    assert (
        status_of(objekt, "annual_statement_year", unit=we01, assignment=a1, year=2025)[0] == "not_applicable"
    )


def test_manuelle_uebersteuerung_und_idempotenz(objekt, szenario, admin_user):
    engine.evaluate_object(objekt, today=TODAY)
    first = {
        f.position_key: (f.pk, f.last_evaluated_at) for f in CompletenessFinding.objects.filter(object=objekt)
    }
    r = engine.evaluate_object(objekt, today=TODAY)
    second = {
        f.position_key: (f.pk, f.last_evaluated_at) for f in CompletenessFinding.objects.filter(object=objekt)
    }
    assert set(first) == set(second) and all(first[k][0] == second[k][0] for k in first)
    assert all(second[k][1] > first[k][1] for k in first) and r["changed"] == 0 and r["created"] == 0
    zv = CompletenessFinding.objects.get(object=objekt, check_code="payment_agreement", scope_type="object")
    with pytest.raises(ValueError):
        engine.set_manual(zv, admin_user, status="fulfilled", reason="")
    with pytest.raises(ValueError):
        engine.set_manual(zv, admin_user, status="missing", reason="x")
    engine.set_manual(
        zv, admin_user, status="fulfilled", reason="Negativerklärung der Vorverwaltung vom 15.06.2026"
    )
    assert not any(
        i["check_code"] == "payment_agreement" and i["unit_label"] == "" for i in engine.open_items(objekt)
    )
    engine.evaluate_object(objekt, today=TODAY)  # Uebersteuerung ueberlebt den Lauf
    zv.refresh_from_db()
    assert (
        zv.manual_status == "fulfilled"
        and zv.status == "missing"
        and engine.effective_status(zv) == "fulfilled"
    )
    assert AuditEvent.objects.filter(action="completeness.override", entity_id=zv.pk).exists()
    # Ausschluss aus der Nachforderung ohne Uebersteuerung
    ea = CompletenessFinding.objects.get(
        object=objekt,
        check_code="annual_statement_year",
        period_year=2025,
        assignment=szenario["assignments"]["a1"],
    )
    engine.set_manual(ea, admin_user, status=None, reason=None, include_in_request=False)
    assert not [i for i in engine.open_items(objekt) if i["finding_id"] == ea.pk][0]["include_in_request"]
    # weggefallene Zuordnung: Findings verschwinden, manuelle bleiben
    a2 = szenario["assignments"]["a2"]
    a2.deleted_at = timezone.now()  # Soft Delete wie in der Anwendung; Findings sind geschuetzte Verweise
    a2.save(update_fields=["deleted_at"])
    engine.evaluate_object(objekt, today=TODAY)
    assert not CompletenessFinding.objects.filter(
        object=objekt, assignment=szenario["assignments"]["a2"]
    ).exists()
    assert CompletenessFinding.objects.filter(pk=zv.pk).exists()


def test_eigentuemerwechsel_mit_und_ohne_nachweis(objekt, szenario):
    we01 = szenario["units"]["WE01"]
    a1 = szenario["assignments"]["a1"]
    a1.valid_to = date(2025, 2, 28)
    a1.save()
    neu = make_owner("Neumuster", "Nina", data_status="confirmed")
    wechsel = assign(neu, we01, date(2025, 3, 1))
    engine.evaluate_object(objekt, today=TODAY)
    assert status_of(objekt, "owner_changes", unit=we01)[0] == "partial"
    # Alteigentuemer ueberlappt 2023 bis 2025, Neueigentuemer 2025 und 2026 (beide anwendbar in 2025)
    assert (
        status_of(objekt, "annual_statement_year", unit=we01, assignment=wechsel, year=2024)[0]
        == "not_applicable"
    )
    assert not CompletenessFinding.objects.filter(
        object=objekt, assignment=a1, check_code="addresses"
    ).exists()
    nachweis = make_doc(objekt, "veraeusserungsanzeige")
    wechsel.source_document = nachweis
    wechsel.save()
    engine.evaluate_object(objekt, today=TODAY)
    st, hint = status_of(objekt, "owner_changes", unit=we01)
    assert st == "fulfilled" and "1 Wechsel" in hint
    ea = make_doc(objekt, "einzelabrechnung")
    link(ea, wechsel, period_year=2025)
    engine.evaluate_object(objekt, today=TODAY)
    assert (
        status_of(objekt, "annual_statement_year", unit=we01, assignment=wechsel, year=2025)[0] == "fulfilled"
    )


def test_mietkatalog(objekt):
    objekt.management_type = "rental"
    objekt.sepa_used = False
    objekt.save()
    we01, we02 = make_unit(objekt, "ME01"), make_unit(objekt, "ME02", vacancy_confirmed=True)
    make_unit(objekt, "ME03")
    tenant = Tenant.objects.create(
        type="natural_person",
        first_name="Anna",
        last_name="Beispiel",
        search_name="BEISPIEL ANNA",
        phone="0000",
    )
    lease = Lease.objects.create(
        object=objekt,
        start_date=date(2022, 3, 1),
        base_rent=Decimal("650.00"),
        utilities_prepayment=Decimal("120.00"),
        heating_prepayment=Decimal("80.00"),
        deposit_amount=Decimal("1950.00"),
        deposit_type="unknown",
        rent_adjustment_type="unknown",
        data_status="confirmed",
    )
    TenantUnitAssignment.objects.create(
        tenant=tenant, unit=we01, lease=lease, valid_from=date(2022, 3, 1), data_status="confirmed"
    )
    engine.evaluate_object(objekt, today=TODAY)
    assert status_of(objekt, "tenant_list_complete")[0] == "missing"
    assert status_of(objekt, "tenant_per_unit", unit=we01)[0] == "fulfilled"
    assert status_of(objekt, "tenant_per_unit", unit=we02) == ("fulfilled", "Leerstand bestätigt")
    assert (
        status_of(objekt, "tenant_per_unit", unit=we02.__class__.objects.get(unit_label="ME03"))[0]
        == "missing"
    )
    assert status_of(objekt, "lease_terms", unit=we01)[0] == "fulfilled"
    assert status_of(objekt, "deposit", unit=we01)[0] == "partial"
    assert status_of(objekt, "rent_adjustment", unit=we01)[0] == "missing"
    assert status_of(objekt, "tenant_contact_data", unit=we01)[0] == "fulfilled"
    assert status_of(objekt, "tenant_sepa_mandate", unit=we01)[0] == "not_applicable"
    assert status_of(objekt, "tenant_accounts")[0] == "missing"
    assert not CompletenessFinding.objects.filter(object=objekt, check_code="owner_per_unit").exists()
    s = engine.summary(objekt)
    assert s["status"] == "incomplete" and s["unit_counts"]["not_evaluable"] == 1


def test_laufzeit_100_einheiten_vier_jahre(objekt):
    objekt.expected_unit_count = 100
    objekt.save()
    for i in range(100):
        u = make_unit(objekt, f"WE{i + 1:03d}")
        o = make_owner(f"Mustermann{i}", "Max", data_status="confirmed", email="x@example.test")
        assign(o, u, date(2020, 1, 1))
    started = time.perf_counter()
    r = engine.evaluate_object(objekt, today=TODAY)
    dauer = time.perf_counter() - started
    assert r["findings"] > 1500 and dauer < 5.0, f"{dauer:.2f} s"
    started = time.perf_counter()
    r2 = engine.evaluate_object(objekt, today=TODAY)
    assert r2["created"] == 0 and r2["changed"] == 0 and time.perf_counter() - started < 5.0
