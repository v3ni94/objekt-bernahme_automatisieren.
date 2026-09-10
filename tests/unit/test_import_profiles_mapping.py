"""Formatprofile und Spaltenzuordnung (H 6.2, 6.3) mit synthetischen Dateien in Originalspaltenstruktur."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from apps.imports.mapping import (
    apply_mapping_overrides,
    header_row_index,
    mapping_from_proposals,
    propose_mapping,
)
from apps.imports.profiles import IMMOWARE24_EINHEITEN, choose_profile, read_csv, read_xlsx

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "imports"
SYNONYMS = json.loads(
    (Path(__file__).resolve().parents[2] / "db" / "seeds" / "app_settings.json").read_text(encoding="utf-8")
)["import.column_synonyms"]


def test_immoware24_erkannt_und_alle_spalten_zugeordnet():
    path = FIX / "immoware24_einheiten.csv"
    profile, scores = choose_profile(path, path.name)
    assert profile.code == "immoware24_export" and scores["immoware24_export"] == 0.95
    table = profile.extract(path)
    assert table.meta["delimiter"] == ";" and table.meta["immoware24_file"] == "einheiten"
    h = header_row_index(table.rows, SYNONYMS)
    assert table.rows[h] == IMMOWARE24_EINHEITEN
    proposals = propose_mapping(table.rows[h], table.rows[h + 1 :], SYNONYMS)
    targets = {p.source_header: p.target for p in proposals}
    assert targets == {
        "Objekt-Nr": "object_number",
        "Status": "unit_status",
        "Objekt": "object_name",
        "Verwaltungsart": "management_type",
        "Gebaeude": "building",
        "VE-Nr": "external_ref",
        "VE-Beschreibung": "unit_label",
        "Lage": "location",
        "Eigentuemer": "owner_name_raw",
        "Hausgeld_EUR_mtl": "house_fee_monthly",
        "Mieter": "tenant_name_raw",
        "Miete_EUR_mtl": "base_rent",
    }
    assert all(p.confidence >= 0.8 for p in proposals), [(p.source_header, p.confidence) for p in proposals]


def test_immoware24_kontakte():
    path = FIX / "immoware24_kontakte.csv"
    profile, _ = choose_profile(path, path.name)
    assert profile.code == "immoware24_export"
    table = profile.extract(path)
    assert table.meta["immoware24_file"] == "kontakte"
    proposals = propose_mapping(table.rows[0], table.rows[1:], SYNONYMS)
    targets = {p.source_header: p.target for p in proposals}
    assert (
        targets["Name"] == "owner_name_raw"
        and targets["Briefanrede"] == "salutation"
        and targets["Adresse"] == "street"
    )
    assert targets["PLZ"] == "postal_code" and targets["Stadt"] == "city" and targets["Land"] == "country"
    assert targets["Telefon"] == "phone" and targets["E-Mail"] == "email" and targets["ID"] == "external_ref"


def test_csv_generic_mit_komma_und_inhaltsmustern():
    path = FIX / "eigentuemerliste_generic.csv"
    profile, _ = choose_profile(path, path.name)
    assert profile.code == "csv_generic"
    table = read_csv(path)
    assert table.meta["delimiter"] == "," and table.meta["encoding"].startswith("utf-8")
    proposals = propose_mapping(table.rows[0], table.rows[1:], SYNONYMS)
    targets = {p.source_header: p.target for p in proposals}
    assert (
        targets["Einheit"] == "unit_label"
        and targets["Eigentümer"] == "owner_name_raw"
        and targets["IBAN"] == "iban_raw"
    )
    assert (
        targets["MEA"] == "co_ownership_share"
        and targets["Eigentum seit"] == "valid_from"
        and targets["Straße"] == "street"
    )
    assert len(table.rows) == 4  # Summenzeile bleibt erhalten


def test_xlsx_blattwahl_verbundene_zellen_und_kopfzeile():
    path = FIX / "eigentuemerliste_generic.xlsx"
    profile, _ = choose_profile(path, path.name)
    assert profile.code == "xlsx_generic"
    table = read_xlsx(path)
    assert table.sheet == "Eigentümer" and "Deckblatt" in table.meta["sheets"]
    assert (
        table.rows[0][0] == table.rows[0][5] == "Eigentümerliste WEG Beispielstadt"
    )  # verbundene Zelle uebertragen
    h = header_row_index(table.rows, SYNONYMS)
    assert h == 1 and table.rows[h][0] == "WE"
    row = table.rows[h + 1]
    assert row[4] == "12345" and row[6] == "245,5" and row[7] == "01.01.2015" and row[8] == "ja"
    proposals = propose_mapping(table.rows[h], table.rows[h + 1 :], SYNONYMS)
    targets = {p.source_header: p.target for p in proposals}
    assert (
        targets["WE"] == "unit_label"
        and targets["Nachname"] == "last_name"
        and targets["Vorname"] == "first_name"
    )
    assert (
        targets["Hausgeld"] == "house_fee_monthly"
        and targets["Eigentumsbeginn"] == "valid_from"
        and targets["SEPA"] == "sepa_mandate_present"
    )


def test_zielfeld_nur_einmal_und_manuelle_korrektur():
    header = ["Name", "Eigentümer", "Bemerkung", "Hinweis"]
    proposals = propose_mapping(header, [["a", "b", "c", "d"]], SYNONYMS)
    targets = [p.target for p in proposals]
    assert targets.count("owner_name_raw") == 1 and targets.count("notes") == 2
    mapping = mapping_from_proposals(proposals)
    mapping = apply_mapping_overrides(mapping, {0: "last_name"})
    assert mapping["targets"]["last_name"] == 0 and mapping["columns"][0]["manual"] is True
    with pytest.raises(ValueError):
        apply_mapping_overrides(mapping, {1: "last_name"})


def test_encoding_cp1252(tmp_path):
    p = tmp_path / "liste.csv"
    p.write_bytes("Einheit;Eigentümer;Straße\nWE1;Müller;Königsallee 1\n".encode("cp1252"))
    table = read_csv(p)
    assert table.meta["encoding"] == "cp1252" and table.rows[1][1] == "Müller"
