"""Maskierung von Bank- und Ausweisdaten (CR 14 Unit-Test IBAN-Maskierung, Beschluss B-11)."""

import pytest

from objektakte.masking import contains_sensitive, iban_mod97, mask_text, normalize_iban

VALID_DE = "DE89 3704 0044 0532 0130 00"  # gueltige Pruefziffer (Beispiel-IBAN der Bundesbank-Dokumentation)


def test_de_iban_mit_leerzeichen_wird_maskiert_fulltext():
    r = mask_text(f"Bitte ueberweisen auf {VALID_DE} bis zum 05.03.2025.", mode="fulltext")
    assert "3704" not in r.text
    assert "[IBAN_****3000]" in r.text
    assert r.count == 1 and r.hits[0].kind == "iban" and r.hits[0].last4 == "3000"
    assert r.hits[0].mod97_valid is True


def test_de_iban_ohne_leerzeichen_und_prompt_modus():
    r = mask_text("IBAN DE89370400440532013000 Kontoinhaber Mustermann", mode="prompt")
    assert r.text == "IBAN [IBAN] Kontoinhaber Mustermann"


def test_ocr_fehler_buchstabe_o_statt_null():
    r = mask_text("DE89 37O4 OO44 O532 O13O OO", mode="log")
    assert r.count == 1
    assert normalize_iban("DE89 37O4 OO44 O532 O13O OO") == "DE89370400440532013000"
    assert r.hits[0].mod97_valid is True


def test_auslaendische_iban_oesterreich_und_niederlande():
    r = mask_text("AT61 1904 3002 3457 3201 und NL91 ABNA 0417 1643 00", mode="log")
    assert r.count == 2
    assert all(h.kind == "iban" for h in r.hits)
    assert "1904" not in r.text and "ABNA" not in r.text


def test_bic_nur_mit_kontextwort():
    assert mask_text("BIC: COBADEFFXXX", mode="log").text == "BIC: [BIC]"
    # Grossgeschriebenes deutsches Wort ohne Kontext bleibt erhalten
    assert mask_text("HAUSGELD FAELLIG", mode="log").text == "HAUSGELD FAELLIG"


def test_blz_und_kontonummer_mit_kontext():
    r = mask_text("BLZ 370 400 44, Konto-Nr. 532013000, Kto.-Nr.: 123 456 78", mode="log")
    assert "[BLZ]" in r.text and r.text.count("[KONTONR]") == 2
    assert "532013000" not in r.text


def test_ausweisnummer_mit_kontextwort():
    r = mask_text("Personalausweis-Nr. L01X00T47 vorgelegt; Reisepass C01X00T478.", mode="log")
    assert r.text.count("[AUSWEISNR]") == 2
    assert {h.kind for h in r.hits} == {"ausweisnummer"}


@pytest.mark.parametrize(
    "text",
    [
        "Zahlung bis 15.04.2025 in Hoehe von 1.234,56 EUR",
        "Telefon +49 1522 9233185, Mobil 0170 1234567",
        "Rechnungsnummer 2025-004711 vom 03.03.2025",
        "Wirtschaftsjahr 2024, Einheit WE 14, MEA 123/10.000",
        "Aktenzeichen 12 O 345/24",
    ],
)
def test_negativfaelle_bleiben_unmaskiert(text):
    r = mask_text(text, mode="fulltext")
    assert r.text == text and r.count == 0


def test_wiederholte_maskierung_findet_nichts_mehr():
    once = mask_text(f"Konto {VALID_DE} BIC COBADEFFXXX", mode="fulltext").text
    assert mask_text(once, mode="fulltext").count == 0
    assert contains_sensitive(once) is False


def test_hmac_gleich_fuer_dokument_und_stammdaten_iban():
    key = b"test-hmac-key"
    a = mask_text("IBAN: DE89 3704 0044 0532 0130 00", hmac_key=key).hits[0].hmac_hex
    b = mask_text("de89370400440532013000", hmac_key=key)
    # Kleinschreibung faellt nicht ins Muster (Grossbuchstaben verlangt), Normalisierung deckt Leerzeichen ab
    c = mask_text("DE89370400440532013000", hmac_key=key).hits[0].hmac_hex
    assert a == c and len(a) == 64
    assert b.count == 0


def test_mod97_erkennt_falsche_pruefziffer():
    assert iban_mod97("DE89370400440532013000") is True
    assert iban_mod97("DE00370400440532013000") is False
