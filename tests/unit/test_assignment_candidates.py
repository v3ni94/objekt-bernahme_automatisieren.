"""Kandidaten, Score, Schwellen und Entscheidung der Objektzuordnung ohne Datenbank (Baustein B)."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from apps.sync.assignment.candidates import (
    HINT_SAME_ADDRESS,
    WEIGHTS,
    apply_rules,
    build_index,
    combine,
    extract_features,
    extract_numbers,
    score_candidates,
)
from apps.sync.assignment.decide import Proposal, Thresholds, decide
from apps.sync.assignment.evidence import Evidence, evidence_to_dicts

OWN = ("Rheinpromenade 13, 40789 Monheim am Rhein",)


@dataclass
class Obj:
    pk: int
    object_number: str
    street: str
    house_number: str
    postal_code: str
    city: str
    management_type: str = "weg"
    drive_root_folder_id: str | None = None
    name: str | None = None


@dataclass
class Rule:
    pk: int
    code: str
    version: int
    conditions: dict
    object_id: int
    is_active: bool = True
    kind: str = "feature_combo"


@pytest.fixture
def index():
    return build_index(
        [
            Obj(1, "100", "Musterweg", "1", "12345", "Musterstadt", drive_root_folder_id="ordner-100"),
            Obj(2, "200", "Hauptstraße", "12a", "40789", "Monheim am Rhein"),
            Obj(3, "300", "Hauptstraße", "12b", "40789", "Monheim am Rhein"),
            Obj(4, "400", "Hauptstraße", "12a", "50667", "Köln"),
            Obj(5, "500", "Hauptstraße", "12a", "40789", "Monheim am Rhein", management_type="rental"),
            Obj(6, "600", "Bahnhofstraße", "7", "40789", "Monheim am Rhein"),
        ]
    )


def run(text, index, **kw) -> tuple[Proposal, list]:
    kw.setdefault("own_addresses", OWN)
    cands = score_candidates(text, index=index, **kw)
    return decide(cands), cands


def by_number(cands, number):
    return next(c for c in cands if c.object_number == number)


def test_index_felder_und_ordner(index):
    assert len(index) == 6
    assert index.by_folder["ordner-100"] == 1
    assert index.by_number[300] == [3]
    assert index.objects[2].address_key == index.objects[5].address_key
    assert index.objects[2].address_key != index.objects[3].address_key


def test_combine_noisy_or_und_gegenbelege():
    assert combine([]) == 0.0
    assert combine([0.5, 0.5]) == pytest.approx(0.75)
    assert combine([0.9, -0.2]) == pytest.approx(0.7)
    assert combine([-0.5]) == 0.0


def test_eindeutige_leistungsadresse_auto(index):
    proposal, cands = run(
        "Rechnung Nr. 1\nLeistungsort: Bahnhofstraße 7, 40789 Monheim am Rhein\nBetrag 100,00 EUR", index
    )
    assert proposal.decision == "auto" and proposal.chosen.object_number == "600"
    ev = by_number(cands, "600").evidence
    assert ev[0].kind == "address" and ev[0].role == "object" and ev[0].page == 1
    assert ev[0].details["strong"] and ev[0].details["postal_match"]


def test_neutrale_adresse_mit_plz_und_ort_auto_ohne_ort_review(index):
    proposal, _ = run("WEG Bahnhofstraße 7\n40789 Monheim am Rhein\nc/o Hausverwaltung", index)
    assert proposal.decision == "auto"
    proposal, _ = run("Betrifft Bahnhofstraße 7, Angebot Treppenhausreinigung", index)
    assert proposal.decision == "review" and proposal.chosen.object_number == "600"


def test_rechnungs_und_lieferantenadresse_objektadresse_gewinnt(index):
    text = (
        "Malerbetrieb Beispiel, Musterweg 1, 12345 Musterstadt, Telefon 01234 5678\n\n"
        "Rechnungsanschrift: WEG Hauptstraße 12b, 40789 Monheim am Rhein, c/o Hausverwaltung Müller GmbH, "
        "Rheinpromenade 13, 40789 Monheim am Rhein\n\n"
        "Leistungsort: Bahnhofstraße 7, 40789 Monheim am Rhein\nMalerarbeiten Treppenhaus"
    )
    proposal, cands = run(text, index)
    assert proposal.decision == "auto" and proposal.chosen.object_number == "600"
    assert by_number(cands, "600").score > by_number(cands, "300").score
    lieferant = [c for c in cands if c.object_number == "100"]
    assert not lieferant or lieferant[0].score == 0.0
    assert all(c.object_number != "" for c in cands)
    assert not any(e.role == "own" for c in cands for e in c.evidence)


def test_gleicher_strassenname_in_zwei_orten_plz_entscheidet(index):
    proposal, cands = run("Leistungsort: Hauptstraße 12a, 50667 Köln", index)
    assert proposal.decision == "auto" and proposal.chosen.object_number == "400"
    assert [c.object_number for c in cands] == ["400"]
    proposal, cands = run("Leistungsort: Hauptstraße 12a", index)
    assert proposal.decision == "review"
    assert sorted(c.object_number for c in cands) == ["200", "400", "500"]
    assert all(c.score < Thresholds().auto_min for c in cands)


def test_hausnummernzusatz_trennt(index):
    proposal, cands = run("Leistungsort: Hauptstraße 12b, 40789 Monheim am Rhein", index)
    assert proposal.decision == "auto" and proposal.chosen.object_number == "300"
    assert [c.object_number for c in cands] == ["300"]


def test_weg_und_sev_gleiche_anschrift_review(index):
    proposal, cands = run("Leistungsort: Hauptstraße 12a, 40789 Monheim am Rhein\nReparatur Haustuer", index)
    assert proposal.decision == "review"
    assert sorted(c.object_number for c in cands) == ["200", "500"]
    assert by_number(cands, "200").score == by_number(cands, "500").score
    assert HINT_SAME_ADDRESS in by_number(cands, "200").hints
    assert HINT_SAME_ADDRESS in proposal.reasons
    assert not by_number(cands, "200").contradictions


def test_weg_und_sev_verwaltungsart_aus_text_nur_schwach(index):
    proposal, cands = run(
        "Leistungsort: Hauptstraße 12a, 40789 Monheim am Rhein\nMietvertrag, Kaltmiete 500,00 EUR, Mieter Beispiel",
        index,
    )
    assert proposal.decision == "review"
    assert by_number(cands, "500").score > by_number(cands, "200").score
    assert any("nur schwach" in h for h in by_number(cands, "500").hints)
    assert any(e.kind == "management_type" for e in by_number(cands, "500").evidence)


def test_sammelrechnung_zwei_leistungsorte_review_mit_widerspruch(index):
    text = (
        "Sammelrechnung Winterdienst\nLeistungsort: Bahnhofstraße 7, 40789 Monheim am Rhein\n"
        "Leistungsort: Musterweg 1, 12345 Musterstadt"
    )
    proposal, cands = run(text, index)
    assert proposal.decision == "review"
    top = [c for c in cands if c.score >= Thresholds().auto_min]
    assert sorted(c.object_number for c in top) == ["100", "600"]
    assert any("weitere Leistungsadresse" in x for x in by_number(cands, "100").contradictions)
    assert any("Widerspruch" in r for r in proposal.reasons)


def test_fehlende_adresse_none(index):
    proposal, cands = run("Allgemeines Rundschreiben ohne Anschrift und ohne Objektnummer", index)
    assert proposal.decision == "none" and proposal.chosen is None and cands == []


def test_hvm_adresse_ohne_weiteren_treffer_none(index):
    text = "Hausverwaltung Müller GmbH\nRheinpromenade 13\n40789 Monheim am Rhein\n\nSehr geehrte Damen und Herren"
    proposal, cands = run(text, index)
    assert proposal.decision == "none" and cands == []


def test_ordnerobjekt_ohne_widerspruch_auto(index):
    proposal, cands = run("Protokoll ohne Anschrift", index, folder_object_id=1)
    assert proposal.decision == "auto" and proposal.chosen.object_number == "100"
    assert cands[0].evidence[0].kind == "folder"
    assert cands[0].score == pytest.approx(WEIGHTS["folder"])
    proposal, _ = run("Protokoll", index, extra_hints={"drive_folder_id": "ordner-100"})
    assert proposal.decision == "auto" and proposal.chosen.object_number == "100"


def test_ordnerobjekt_mit_widersprechender_leistungsadresse_review(index):
    proposal, cands = run("Leistungsort: Bahnhofstraße 7, 40789 Monheim am Rhein", index, folder_object_id=1)
    assert proposal.decision == "review"
    assert any("Ordnerobjekt widerspricht" in x for x in by_number(cands, "100").contradictions)
    assert any("widerspricht Ordnerobjekt" in x for x in by_number(cands, "600").contradictions)


def test_ordnerobjekt_entscheidet_bei_gleicher_anschrift(index):
    idx = build_index(
        [
            Obj(
                2, "200", "Hauptstraße", "12a", "40789", "Monheim am Rhein", drive_root_folder_id="ordner-200"
            ),
            Obj(5, "500", "Hauptstraße", "12a", "40789", "Monheim am Rhein", management_type="rental"),
        ]
    )
    proposal, cands = run("Leistungsort: Hauptstraße 12a, 40789 Monheim am Rhein", idx, folder_object_id=2)
    assert proposal.decision == "auto" and proposal.chosen.object_number == "200"
    assert any("Ordnerobjekt spricht fuer" in h for h in by_number(cands, "500").hints)


def test_objektnummer_mit_kontextwort_und_dateiname(index):
    proposal, cands = run("Betrifft Verwaltungsobjekt 300, Angebot", index)
    assert proposal.decision == "auto" and proposal.chosen.object_number == "300"
    assert cands[0].evidence[0].kind == "object_number"
    proposal, cands = run("Es wurden 300 Wohnungen gezaehlt, Objekt: 300 Wohnungen", index)
    assert proposal.decision == "none"
    proposal, cands = run("Angebot", index, filename="300_Angebot_Dach.pdf")
    assert proposal.decision == "review" and cands[0].evidence[0].kind == "filename_number"
    proposal, cands = run("Angebot", index, filename="Angebot Bahnhofstraße 7.pdf")
    assert proposal.chosen.object_number == "600" and cands[0].evidence[0].kind == "filename_address"


def test_extract_numbers_und_features():
    numbers = extract_numbers("Kundennummer: 12 345 678\nVertrags-Nr. V-4711/88\nZählernummer 1ABC0099")
    kinds = {n["kind"]: n["value"] for n in numbers}
    assert kinds["customer"] == "12345678"
    assert kinds["contract"] == "v471188"
    assert kinds["meter"] == "1abc0099"
    features = extract_features(
        "Vertragsnummer 4711", filename="a.pdf", correspondent="Stadtwerke Beispiel GmbH", drive_folder_id="x"
    )
    assert features["supplier_norm"] == "stadtwerke beispiel gmbh"
    assert features["numbers"][0]["kind"] == "contract"
    assert features["drive_folder_id"] == "x" and features["addresses"] == []


def test_regeltreffer_als_starkes_signal(index):
    rule = Rule(
        7,
        "fc-abc",
        1,
        {"supplier_norm": "stadtwerke beispiel gmbh", "number_kind": "contract", "number": "4711-88"},
        6,
    )
    features = {
        "supplier_norm": "stadtwerke beispiel gmbh",
        "numbers": [{"kind": "contract", "value": "471188"}],
    }
    hits = apply_rules(features, [rule])
    assert hits and hits[0]["rule_id"] == 7 and hits[0]["object_id"] == 6
    assert apply_rules({"supplier_norm": "andere", "numbers": features["numbers"]}, [rule]) == []
    assert apply_rules(features, [Rule(8, "fc-x", 1, rule.conditions, 6, is_active=False)]) == []
    proposal, cands = run(
        "Abschlag Strom, Vertragsnummer 4711-88",
        index,
        extra_hints={"correspondent": "Stadtwerke Beispiel GmbH"},
        rules=[rule],
    )
    assert proposal.decision == "auto" and proposal.chosen.object_number == "600"
    assert cands[0].rule_hits[0]["code"] == "fc-abc"


def test_thresholds_und_decide_grenzen():
    from apps.sync.assignment.candidates import Candidate

    a = Candidate(1, "100", score=0.90)
    b = Candidate(2, "200", score=0.70)
    assert decide([a, b]).decision == "review"  # Abstand 0,20 unter gap_min
    assert decide([a, b], Thresholds(gap_min=0.1)).decision == "auto"
    a.contradictions.append("Test")
    assert decide([a], Thresholds()).decision == "review"
    assert decide([a], Thresholds(contradiction_blocks=False)).decision == "auto"
    assert decide([Candidate(3, "300", score=0.2)]).decision == "none"
    assert decide([]).decision == "none"
    p = decide([Candidate(4, "400", score=0.5)])
    assert p.to_dict()["decision"] == "review" and p.to_dict()["chosen_object_id"] == 4


def test_evidence_serialisierung():
    ev = Evidence(
        kind="address", text="Musterweg 1", page=1, position=3, weight=0.8, role="object", object_id=1
    )
    data = evidence_to_dicts([ev, {"kind": "x"}, "ignoriert"])
    assert data[0]["weight"] == 0.8 and data[0]["object_id"] == 1 and data[1] == {"kind": "x"}
