"""Unscharfer Eigentuemerabgleich (H 6.6): Nachname allein reicht nie, Signale, Kandidatenband, objektuebergreifend nie automatisch."""

from __future__ import annotations

from apps.imports.matching import MatchResult, OwnerRecord, PersonQuery, match_owner, score_owner


def owner(id, last, first=None, **kw):
    d = dict(
        type="natural_person",
        first_name=first,
        last_name=last,
        company_name=None,
        email=None,
        postal_code=None,
        street=None,
        iban_hash_hex=None,
        display=f"{first or ''} {last}".strip(),
    )
    d.update(kw)
    return OwnerRecord(id=id, **d)


def test_nachname_allein_reicht_nicht():
    owners = [
        owner(1, "Mustermann", "Erika", has_assignment_in_object=True, unit_ids_in_object=frozenset({7}))
    ]
    r = match_owner(PersonQuery("natural_person", last_name="Mustermann"), owners)
    assert r.decision == "candidates"  # 100 Punkte Nachname, aber kein Signal


def test_vorname_und_einheit_ergeben_treffer():
    owners = [
        owner(1, "Mustermann", "Erika", has_assignment_in_object=True, unit_ids_in_object=frozenset({7}))
    ]
    r = match_owner(
        PersonQuery("natural_person", first_name="Erika", last_name="Mustermann", unit_id=7), owners
    )
    assert (
        r.decision == "auto"
        and r.best.owner_id == 1
        and {"same_unit", "same_first_name"} <= set(r.best.signals)
    )


def test_objektuebergreifend_nie_automatisch():
    owners = [owner(1, "Mustermann", "Erika", has_assignment_in_object=False, email="e@example.test")]
    r = match_owner(
        PersonQuery("natural_person", first_name="Erika", last_name="Mustermann", email="e@example.test"),
        owners,
    )
    assert r.decision == "candidates" and r.best.owner_id == 1


def test_doppelte_namen_ergeben_kandidatenliste():
    owners = [
        owner(1, "Mustermann", "Erika", has_assignment_in_object=True, unit_ids_in_object=frozenset({7})),
        owner(2, "Mustermann", "Erika", has_assignment_in_object=True, unit_ids_in_object=frozenset({8})),
    ]
    r = match_owner(
        PersonQuery("natural_person", first_name="Erika", last_name="Mustermann", unit_id=7), owners
    )
    assert r.decision == "candidates" and len(r.candidates) == 2


def test_neuer_eigentuemer_und_ocr_toleranz():
    owners = [owner(1, "Beispiel", "Hans", has_assignment_in_object=True)]
    assert (
        match_owner(
            PersonQuery("natural_person", first_name="Erika", last_name="Mustermann"), owners
        ).decision
        == "new"
    )
    c = score_owner(PersonQuery("natural_person", first_name="Hans", last_name="Beispie1"), owners[0])
    assert c.base >= 85 and "same_first_name" in c.signals


def test_firma_partial_ratio():
    owners = [
        OwnerRecord(
            3,
            "legal_entity",
            None,
            None,
            "Muster Immobilien GmbH",
            None,
            None,
            None,
            None,
            frozenset(),
            True,
            "Muster Immobilien GmbH",
        )
    ]
    r = match_owner(PersonQuery("legal_entity", company_name="Muster Immobilien"), owners)
    assert isinstance(r, MatchResult) and r.best is not None and r.best.base >= 90
    assert r.decision == "candidates"  # kein Zusatzsignal
