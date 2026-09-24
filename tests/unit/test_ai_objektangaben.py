"""Objektangaben fuer die KI (24.09.2026): Anschriften ohne Personenbezug, weitere Anschriften desselben Gebaeudes,
keine Bezeichnung (kann Personennamen tragen); ohne Strasse gilt die Anschrift als unbekannt."""

from types import SimpleNamespace

from apps.ai.services import object_context


def _obj(**kw):
    basis = {
        "object_number": "467",
        "name": "Am Panke Park 67-85 H5 Familie Muster",
        "street": None,
        "house_number": None,
        "postal_code": None,
        "city": None,
        "additional_addresses": None,
    }
    basis.update(kw)
    return SimpleNamespace(**basis)


def test_ohne_strasse_gilt_anschrift_als_unbekannt_und_name_bleibt_weg():
    ctx = object_context(_obj())
    assert ctx == {"objektnummer": "467", "anschriften": [], "anschrift_bekannt": False}
    assert "Muster" not in str(ctx)


def test_haupt_und_nebenanschriften_ohne_dubletten():
    ctx = object_context(
        _obj(
            street="Am Panke Park",
            house_number="67-85",
            postal_code="16321",
            city="Bernau",
            additional_addresses=[
                {
                    "street": "Am Panke Park",
                    "house_number": "67-85",
                    "postal_code": "16321",
                    "city": "Bernau",
                },
                {"street": "Pankeweg", "house_number": "2"},
                "kein dict",
                {"street": "", "house_number": "9"},
            ],
        )
    )
    assert ctx["anschrift_bekannt"] is True
    assert ctx["anschriften"] == ["Am Panke Park 67-85, 16321 Bernau", "Pankeweg 2"]
