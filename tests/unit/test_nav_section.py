"""Aktiver Bereich der Hauptnavigation aus dem URL-Pfad (Designueberarbeitung 11.09.2026)."""

from apps.ui.context_processors import nav_section


def test_bereiche():
    assert nav_section("/objekte/") == "objekte"
    assert nav_section("/objekte/12/dokumente/") == "objekte"
    assert nav_section("/dokumente/5/") == "objekte"
    assert nav_section("/abgleiche/3/") == "objekte"
    assert nav_section("/eigentuemer/neu/") == "eigentuemer"
    assert nav_section("/review/7/") == "review"
    assert nav_section("/verwaltung/drive/") == "drive"
    assert nav_section("/verwaltung/konfiguration/") == "konfiguration"
    assert nav_section("/verwaltung/protokoll/") == "protokoll"
    assert nav_section("/verwaltung/nutzer/3/rolle/") == "nutzer"
    assert nav_section("/verwaltung/altbestand/") == "altbestand"
    assert nav_section("/konto/2fa/") == "konto"
    assert nav_section("/") is None and nav_section("") is None
