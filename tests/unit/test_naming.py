"""Benennungsfunktion der Eigentuemerakten: 32 Faelle aus Fachentwurf F 6.4 in beiden Praefixmodi (B-23) plus
Eigenschaftstest. Alle Namen synthetisch."""

from __future__ import annotations

import unicodedata
from datetime import date

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from apps.drive.naming import (
    FORBIDDEN,
    OwnerFileNamingConfig,
    OwnerNameInput,
    OwnerNamePart,
    build_owner_folder_name,
)

BY_TYPE = OwnerFileNamingConfig(unit_prefix_mode="by_type")
ALWAYS_WE = OwnerFileNamingConfig(unit_prefix_mode="always_we")


def person(name: str) -> OwnerNamePart:
    return OwnerNamePart(kind="natural_person", last_name=name)


def inp(kind="unit_owner", label=None, utype=None, names=(), **kw) -> OwnerNameInput:
    parts = tuple(p if isinstance(p, OwnerNamePart) else person(p) for p in names)
    return OwnerNameInput(file_kind=kind, unit_label=label, unit_type=utype, owner_names=parts, **kw)


LONG40 = ["A" * 40, "B" * 40, "C" * 40]

# (Nr, Eingabe, Erwartung by_type, Erwartung always_we)
CASES = [
    (1, inp(label="WE 1", utype="apartment", names=["Mustermann"]), "WE01_Mustermann", "WE01_Mustermann"),
    (2, inp(label="WE14", utype="apartment", names=["Mustermann"]), "WE14_Mustermann", "WE14_Mustermann"),
    (3, inp(label="WE 103", utype="apartment", names=["Mustermann"]), "WE103_Mustermann", "WE103_Mustermann"),
    (4, inp(label="WE 14a", utype="apartment", names=["Mustermann"]), "WE14a_Mustermann", "WE14a_Mustermann"),
    (5, inp(label="WE03", utype="apartment", names=["Zeta", "Alpha"]), "WE03_Alpha-Zeta", "WE03_Alpha-Zeta"),
    (
        6,
        inp(label="WE03", utype="apartment", names=["Gamma", "Alpha", "Beta"]),
        "WE03_Alpha-Beta-Gamma",
        "WE03_Alpha-Beta-Gamma",
    ),
    (
        7,
        inp(label="WE03", utype="apartment", names=["Delta", "Gamma", "Alpha", "Beta"]),
        "WE03_Alpha-ua",
        "WE03_Alpha-ua",
    ),
    (
        8,
        inp(label="WE05", utype="apartment", names=["Mustermann", "Mustermann"]),
        "WE05_Mustermann",
        "WE05_Mustermann",
    ),
    (
        9,
        inp(label="WE06", utype="apartment", names=["Öztürk", "Adler", "Zimmer"]),
        "WE06_Adler-Öztürk-Zimmer",
        "WE06_Adler-Öztürk-Zimmer",
    ),
    (
        10,
        inp(label="WE06", utype="apartment", names=["meier", "Adler"]),
        "WE06_Adler-meier",
        "WE06_Adler-meier",
    ),
    (
        11,
        inp(label="WE02", utype="apartment", names=["Muster-Beispiel"]),
        "WE02_Muster-Beispiel",
        "WE02_Muster-Beispiel",
    ),
    (
        13,
        inp(
            label="WE07",
            utype="apartment",
            names=[OwnerNamePart(kind="legal_entity", company_name="Muster Immobilien GmbH & Co. KG")],
        ),
        "WE07_Muster Immobilien",
        "WE07_Muster Immobilien",
    ),
    (
        14,
        inp(
            label="WE07",
            utype="apartment",
            names=[
                OwnerNamePart(
                    kind="legal_entity", company_name="Muster Immobilien GmbH", short_name="MusterImmo"
                )
            ],
        ),
        "WE07_MusterImmo",
        "WE07_MusterImmo",
    ),
    (
        15,
        inp(
            label="WE08",
            utype="apartment",
            names=[OwnerNamePart(kind="community", company_name="Erbengemeinschaft Mustermann")],
        ),
        "WE08_Erbengemeinschaft Mustermann",
        "WE08_Erbengemeinschaft Mustermann",
    ),
    (
        16,
        inp(label="WE04", utype="apartment", names=["Muster/Frau: Test?"]),
        "WE04_MusterFrau Test",
        "WE04_MusterFrau Test",
    ),
    (
        17,
        inp(label="WE04", utype="apartment", names=["  Muster   Mann "]),
        "WE04_Muster Mann",
        "WE04_Muster Mann",
    ),
    (18, inp(label="Garage 3", utype="garage", names=["Mustermann"]), "GA03_Mustermann", "WE03_Mustermann"),
    (
        20,
        inp(label="Stellplatz Nr. 12", utype="parking", names=["Mustermann"]),
        "ST12_Mustermann",
        "WE12_Mustermann",
    ),
    (
        21,
        inp(label="TG 7", utype="underground_parking", names=["Mustermann"]),
        "TG07_Mustermann",
        "WE07_Mustermann",
    ),
    (22, inp(label="WE03", utype="apartment", names=[]), "WE03_Unbekannt", "WE03_Unbekannt"),
    (
        23,
        inp(kind="unknown_unit", names=["Mustermann"]),
        "Unbekannte_WE_Mustermann",
        "Unbekannte_WE_Mustermann",
    ),
    (
        24,
        inp(kind="unknown_unit", names=["Zeta", "Alpha"]),
        "Unbekannte_WE_Alpha-Zeta",
        "Unbekannte_WE_Alpha-Zeta",
    ),
    (25, inp(kind="unassigned"), "Unzugeordnet", "Unzugeordnet"),
    (
        26,
        inp(
            label="WE03",
            utype="apartment",
            names=["Mustermann"],
            existing_names_in_object=frozenset({"WE03_Mustermann"}),
            valid_from=date(2026, 7, 1),
        ),
        "WE03_Mustermann_2026",
        "WE03_Mustermann_2026",
    ),
    (
        27,
        inp(
            label="WE03",
            utype="apartment",
            names=["Mustermann"],
            existing_names_in_object=frozenset({"WE03_Mustermann", "WE03_Mustermann_2"}),
        ),
        "WE03_Mustermann_3",
        "WE03_Mustermann_3",
    ),
    (
        28,
        inp(label="WE09", utype="apartment", names=LONG40),
        "WE09_" + "A" * 40 + "-ua",
        "WE09_" + "A" * 40 + "-ua",
    ),
    (29, inp(label="WE09", utype="apartment", names=["N" * 120]), "WE09_" + "N" * 95, "WE09_" + "N" * 95),
    (
        30,
        inp(label="WE01", utype="apartment", names=[unicodedata.normalize("NFD", "Müller")]),
        "WE01_Müller",
        "WE01_Müller",
    ),
    (31, inp(label="Haus 3", utype="other", names=["Mustermann"]), "VE03_Mustermann", "WE03_Mustermann"),
    (
        32,
        inp(
            label="WE 5",
            utype="apartment",
            names=["Mustermann"],
            existing_names_in_object=frozenset({"WE05_Mustermann"}),
            current_name="WE05_Mustermann",
        ),
        "WE05_Mustermann",
        "WE05_Mustermann",
    ),
]


@pytest.mark.parametrize(
    ("nr", "eingabe", "erwartet_by_type", "erwartet_always_we"), CASES, ids=[f"F{c[0]}" for c in CASES]
)
def test_faelle_beide_modi(nr, eingabe, erwartet_by_type, erwartet_always_we):
    assert build_owner_folder_name(eingabe, BY_TYPE) == erwartet_by_type
    assert build_owner_folder_name(eingabe, ALWAYS_WE) == erwartet_always_we


def test_fall_12_transliteration():
    cfg = OwnerFileNamingConfig(unit_prefix_mode="by_type", transliterate_umlauts=True)
    assert (
        build_owner_folder_name(inp(label="WE02", utype="apartment", names=["Müller-Lüdenscheidt"]), cfg)
        == "WE02_Mueller-Luedenscheidt"
    )


def test_fall_19_garage_immer_we():
    assert (
        build_owner_folder_name(inp(label="Garage 3", utype="garage", names=["Mustermann"]), ALWAYS_WE)
        == "WE03_Mustermann"
    )


def test_fall_30_ergebnis_ist_nfc():
    name = build_owner_folder_name(
        inp(label="WE01", utype="apartment", names=[unicodedata.normalize("NFD", "Müller")]), BY_TYPE
    )
    assert name == unicodedata.normalize("NFC", name)


def test_kollision_beruecksichtigt_hoechstlaenge():
    cfg = OwnerFileNamingConfig(name_max_length=20)
    existing = frozenset({"WE01_" + "M" * 15})
    name = build_owner_folder_name(
        inp(label="WE01", utype="apartment", names=["M" * 30], existing_names_in_object=existing), cfg
    )
    assert len(name) <= 20 and name.endswith("_2") and name not in existing


def test_konfiguration_aus_app_settings(seeded):
    cfg = OwnerFileNamingConfig.from_settings()
    assert cfg.unit_prefix_mode == "always_we"  # Seed nach B-23 bis Antwort F6
    assert cfg.name_unknown_unit_prefix == "Unbekannte_WE"
    assert cfg.type_prefix_mapping["GARAGE"] == "garage"


# ---------------------------------------------------------------- Eigenschaftstest (F 6.4)
_text = st.text(min_size=0, max_size=60).filter(lambda s: "\x00" not in s)
_names = st.lists(_text, min_size=0, max_size=6)
_labels = st.one_of(
    st.none(),
    st.from_regex(r"^(WE|GE|TG|Garage|Stellplatz Nr\.|Haus)?\s?[0-9]{1,4}[a-z]?$", fullmatch=True),
    _text,
)
_kinds = st.sampled_from(["unit_owner", "unknown_unit", "unassigned"])
_types = st.sampled_from(
    ["apartment", "commercial", "parking", "garage", "underground_parking", "cellar", "other", None]
)
_cfgs = st.sampled_from(
    [
        BY_TYPE,
        ALWAYS_WE,
        OwnerFileNamingConfig(unit_prefix_mode="by_type", transliterate_umlauts=True, name_max_length=40),
    ]
)


@settings(max_examples=300, deadline=None)
@given(kind=_kinds, label=_labels, utype=_types, names=_names, cfg=_cfgs)
def test_eigenschaften(kind, label, utype, names, cfg):
    eingabe = inp(kind=kind, label=label, utype=utype, names=names)
    name = build_owner_folder_name(eingabe, cfg)
    assert name, "Ergebnis nie leer"
    assert not FORBIDDEN.search(name), "keine verbotenen Zeichen"
    assert len(name) <= cfg.name_max_length
    assert name == unicodedata.normalize("NFC", name)
    assert name == build_owner_folder_name(eingabe, cfg), "deterministisch"
    if kind == "unit_owner" and label and any(ch.isdigit() for ch in label):
        assert "_" in name and name.split("_", 1)[0], "beginnt mit dem Einheitentoken"
    if kind == "unassigned":
        assert name == cfg.name_unassigned
