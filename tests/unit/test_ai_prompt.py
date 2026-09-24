"""Nutzerteil der Stufe 3: konstante Teile (Taxonomie, Schema) zuerst, damit Anbieter mit automatischem Prompt-Cache den
unveraenderten Praefix wiederverwenden; Inhalt und Reparaturhinweis bleiben unveraendert."""

import json

from apps.ai.prompt import PROMPT_VERSION, build_messages, prompt_hash
from apps.ai.schema import ClassificationRequest, Taxonomy, TaxonomyEntry


def _taxonomy() -> Taxonomy:
    return Taxonomy(
        categories=(TaxonomyEntry("05", "Eigentümer"), TaxonomyEntry("06", "Sonstiges")),
        subfolders={"05": (TaxonomyEntry("04", "Hausgeld"),), "06": (TaxonomyEntry("01", "Unklar"),)},
        document_types={"05/04": (TaxonomyEntry("hausgeldabrechnung", "Hausgeldabrechnung"),)},
    )


def _request(filename: str, excerpt: str) -> ClassificationRequest:
    return ClassificationRequest(
        excerpt_masked=excerpt,
        filename_masked=filename,
        management_type="weg",
        taxonomy=_taxonomy(),
        unit_label_patterns=["WE"],
        hints={"has_period_year": True},
        object_context={
            "objektnummer": "623",
            "anschriften": ["Joachimstraße 49, 40545 Düsseldorf"],
            "anschrift_bekannt": True,
        },
    )


def test_konstante_teile_stehen_vorn_und_praefix_ist_fuer_alle_dokumente_gleich():
    _, user_a = build_messages(_request("A.pdf", "Hausgeldabrechnung 2025 WE01"))
    _, user_b = build_messages(_request("B.pdf", "Protokoll der Eigentümerversammlung"))
    keys = list(json.loads(user_a).keys())
    assert keys == [
        "taxonomie",
        "schema",
        "verwaltungsart",
        "einheitenmuster",
        "objekt",
        "hinweise",
        "dateiname",
        "textauszug",
    ]
    # gemeinsamer Praefix reicht bis hinter die Hinweise, erst der Dateiname unterscheidet sich
    cut = user_a.index('"dateiname"')
    assert user_a[:cut] == user_b[:cut]
    assert "Hausgeldabrechnung 2025 WE01" in user_a and "A.pdf" in user_a


def test_reparaturhinweis_haengt_hinten_an_und_aendert_den_hash():
    req = _request("A.pdf", "Text")
    system, user = build_messages(req)
    system_r, user_r = build_messages(req, repair_hint="Unterordner nur als Code")
    assert system == system_r and user_r.startswith(user)
    assert user_r.endswith("Hinweis zur Korrektur der vorherigen Antwort: Unterordner nur als Code")
    assert prompt_hash(system, user) != prompt_hash(system_r, user_r)
    assert PROMPT_VERSION == "2026-09-24.2"


def test_objektangaben_und_objektbezugsregel_im_auftrag():
    """24.09.2026: die KI bekommt Objektnummer und Anschriften; ohne Anschrift darf sie den Objektbezug nicht verneinen;
    Unsicherheit heisst niedrige Konfidenz statt 0,9."""
    system, user = build_messages(_request("A.pdf", "Jahresabrechnung 2024"))
    payload = json.loads(user)
    assert payload["objekt"] == {
        "objektnummer": "623",
        "anschriften": ["Joachimstraße 49, 40545 Düsseldorf"],
        "anschrift_bekannt": True,
    }
    assert "anschrift_bekannt false, setze object_related nur dann false" in system
    assert 'object_related false ist category immer "06" mit Unterordner "04"' in system
    assert "Briefkopf der Hausverwaltung" in system
    assert "Unsicherheit über die Konfidenz aus" in system and "0,85 oder höher" in system
    assert "höchstens 0,6" not in system
    assert "beleg_einheit" in system and "gesamtjahresabrechnung" in system
