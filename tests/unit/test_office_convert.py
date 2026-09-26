"""Umwandlung von Office-Altformaten ueber LibreOffice (26.09.2026): der Aufruf wird mit einem gefaelschten
soffice (Shell-Skript, das eine Minimal-PDF schreibt) geprueft, dazu fehlende Binaerdatei, Fehlercode,
Zeitueberschreitung und der Fall Rueckgabecode 0 ohne Ausgabedatei (so verhaelt sich soffice bei nicht ladbarer
Quelle)."""

from __future__ import annotations

import os
import stat
import subprocess
import time
from pathlib import Path

import pytest

from apps.pipeline import office_convert

MINIMAL_PDF = (
    "%PDF-1.4\\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\\n"
    "2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\\n"
    "3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\\n"
    "trailer<</Root 1 0 R>>\\n%%EOF\\n"
)


def _fake_soffice(tmp_path: Path, body: str) -> Path:
    """Shell-Skript als soffice-Ersatz; protokolliert Argumente und HOME in log.txt neben dem Skript."""
    script = tmp_path / "soffice"
    script.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$@" > "{tmp_path / "args.txt"}"\n'
        f'printf "%s\\n" "$HOME" > "{tmp_path / "home.txt"}"\n' + body,
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


def _schreibt_pdf() -> str:
    # --outdir ist das sechste Argument, die Quelle das siebte; die PDF heisst wie die Quelle mit .pdf
    return (
        'out="$6"; src="$7"; stem="$(basename "$src")"; stem="${stem%.*}"\n'
        f"printf '%b' '{MINIMAL_PDF}' > \"$out/$stem.pdf\"\n"
        "exit 0\n"
    )


def test_umwandlung_mit_gefaelschtem_soffice(tmp_path, monkeypatch):
    script = _fake_soffice(tmp_path, _schreibt_pdf())
    monkeypatch.setenv("SOFFICE_BIN", str(script))
    monkeypatch.setenv("HOME", str(tmp_path / "echtes-home"))
    quelle = tmp_path / "original.doc"
    quelle.write_bytes(b"\xd0\xcf\x11\xe0" + bytes(64))
    out_dir = tmp_path / "convert"

    assert office_convert.available()
    pdf = office_convert.convert_to_pdf(quelle, out_dir, timeout_s=30)

    assert pdf == out_dir / "original.pdf" and pdf.read_bytes().startswith(b"%PDF-1.4")
    args = (tmp_path / "args.txt").read_text(encoding="utf-8").split("\n")
    assert args[:6] == ["--headless", "--norestore", "--convert-to", "pdf", "--outdir", str(out_dir)]
    assert args[6] == str(quelle)
    # Eigenes HOME je Aufruf unter dem Ausgabeverzeichnis, danach entfernt (kein gemeinsames Profil)
    home = Path((tmp_path / "home.txt").read_text(encoding="utf-8").strip())
    assert home != tmp_path / "echtes-home" and home.parent == out_dir
    assert not home.exists()
    assert os.environ["HOME"] == str(tmp_path / "echtes-home")


def test_fehlende_binaerdatei(tmp_path, monkeypatch):
    monkeypatch.setenv("SOFFICE_BIN", str(tmp_path / "gibt-es-nicht"))
    quelle = tmp_path / "a.xls"
    quelle.write_bytes(b"x")
    assert not office_convert.available()
    with pytest.raises(office_convert.ConversionUnavailable):
        office_convert.convert_to_pdf(quelle, tmp_path / "convert", timeout_s=5)
    # Name ohne Pfad wird im PATH gesucht
    monkeypatch.setenv("SOFFICE_BIN", "soffice-gibt-es-nicht-4711")
    assert office_convert.resolve_binary() is None
    with pytest.raises(office_convert.ConversionUnavailable):
        office_convert.convert_to_pdf(quelle, tmp_path / "convert", timeout_s=5)


def test_fehlercode_ohne_dateiname_in_der_meldung(tmp_path, monkeypatch):
    script = _fake_soffice(tmp_path, 'echo "Error: Vertrauliche_Datei.doc kaputt" >&2\nexit 77\n')
    monkeypatch.setenv("SOFFICE_BIN", str(script))
    quelle = tmp_path / "Vertrauliche_Datei.doc"
    quelle.write_bytes(b"x")
    with pytest.raises(office_convert.ConversionFailed) as info:
        office_convert.convert_to_pdf(quelle, tmp_path / "convert", timeout_s=5)
    assert str(info.value) == "Rückgabecode 77"
    assert "Vertrauliche" not in str(info.value)


def test_rueckgabecode_null_ohne_pdf_gilt_als_fehler(tmp_path, monkeypatch):
    script = _fake_soffice(tmp_path, 'echo "Error: source file could not be loaded" >&2\nexit 0\n')
    monkeypatch.setenv("SOFFICE_BIN", str(script))
    quelle = tmp_path / "a.doc"
    quelle.write_bytes(b"x")
    with pytest.raises(office_convert.ConversionFailed) as info:
        office_convert.convert_to_pdf(quelle, tmp_path / "convert", timeout_s=5)
    assert str(info.value) == "keine PDF erzeugt"


def test_zeitueberschreitung_beendet_die_ganze_prozessgruppe(tmp_path, monkeypatch):
    """Das echte soffice ist ein Skript, das oosplash und darueber soffice.bin startet; nach dem Zeitlimit darf
    kein Prozess der Gruppe mehr laufen (26.09.2026). Das Fake-Skript startet dafuer einen langlebigen Enkel
    (sleep im Hintergrund), schreibt beide PIDs und wartet selbst laenger als das Zeitlimit; ohne das Beenden der
    Gruppe liefe der Enkel weiter und der Test scheitert nach dem Ende des Skripts."""
    pid_datei = tmp_path / "pids.txt"
    script = _fake_soffice(
        tmp_path,
        f'sleep 300 >/dev/null 2>&1 &\nprintf "%s %s\\n" "$$" "$!" > "{pid_datei}"\nsleep 20\nexit 0\n',
    )
    monkeypatch.setenv("SOFFICE_BIN", str(script))
    quelle = tmp_path / "a.doc"
    quelle.write_bytes(b"x")
    with pytest.raises(office_convert.ConversionFailed) as info:
        office_convert.convert_to_pdf(quelle, tmp_path / "convert", timeout_s=1)
    assert str(info.value) == "Zeitlimit 1 s überschritten"
    assert not any(p.name.startswith("soffice-home-") for p in (tmp_path / "convert").iterdir())
    skript_pid, enkel = (int(x) for x in pid_datei.read_text(encoding="utf-8").split())
    # Das Skript lief in eigener Sitzung, die Gruppen-ID ist seine PID; der Enkel gehoert zu dieser Gruppe.
    # Nach SIGKILL an die Gruppe darf kein Mitglied mehr existieren. Den Enkel sammelt init ein, deshalb kurz
    # warten, bis killpg(gruppe, 0) mit ProcessLookupError scheitert.
    for _ in range(50):
        try:
            os.killpg(skript_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        pytest.fail("Prozessgruppe laeuft nach dem Zeitlimit weiter")
    with pytest.raises(ProcessLookupError):
        os.kill(enkel, 0)


def test_kill_process_group_ignoriert_beendete_gruppe():
    proc = subprocess.Popen(["true"], start_new_session=True)
    proc.wait()
    office_convert.kill_process_group(proc.pid)  # kein Fehler, Gruppe bereits weg
