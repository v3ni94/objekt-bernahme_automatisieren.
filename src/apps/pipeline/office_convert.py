"""Office-Altformate ueber LibreOffice in PDF wandeln (26.09.2026, Vorlage E-1, Gruppe Office-Altformate).

Im Drive-Altbestand standen 853 Dokumente .doc und .xls als Fall "nicht unterstuetztes Format". Die Kette wandelt
solche Dateien (office_legacy: .doc, .xls, .rtf, .odt, .ods; Praesentationen nicht, kein Impress im Image) im
Arbeitsverzeichnis des Dokuments in eine PDF
und liest diese wie eine hochgeladene PDF (Textebene, OCR nur wenn noetig, Seitenbilder). Die PDF dient nur der
Texterkennung; abgelegt bleibt das Original.

Aufruf nach dem Muster des OCR-Kindprozesses (apps.pipeline.ocr.run_ocr): Kindprozess mit Zeitlimit,
Rueckgabecode und Ergebnis ausdruecklich pruefen. Besonderheiten von LibreOffice:
- /usr/bin/soffice ist ein Shell-Skript, das oosplash startet, und oosplash forkt soffice.bin. Ein Zeitlimit
  ueber subprocess.run(timeout=...) beendet nur den direkten Kindprozess, soffice.bin liefe als verwaister
  Prozess weiter (Kern und Speicher belegt, PDF und Profilreste koennten spaeter noch entstehen). Deshalb
  startet der Aufruf den Prozess in einer eigenen Sitzung (start_new_session) und beendet bei
  Zeitueberschreitung die ganze Prozessgruppe mit SIGKILL, bevor das temporaere HOME entfernt wird.
- Jeder Aufruf erhaelt ein eigenes HOME in einem temporaeren Verzeichnis. LibreOffice legt sein Profil unter
  $HOME an und sperrt es; parallele Worker mit gemeinsamem Profil blockieren sich sonst gegenseitig oder brechen
  mit "user installation could not be completed" ab.
- soffice endet auch dann mit Rueckgabecode 0, wenn die Quelle nicht geladen werden konnte (Meldung nur auf
  stderr, keine Ausgabedatei). Massgeblich ist deshalb, ob die PDF im Ausgabeverzeichnis entstanden ist.
- Die Binaerdatei kommt aus SOFFICE_BIN (Standard soffice im PATH); nur das Worker-Image bringt LibreOffice mit
  (docker/app.Dockerfile, docs/betrieb/deployment.md). Fehlt sie, meldet ConversionUnavailable, die Kette legt
  dann den Fall "nicht unterstuetztes Format" mit Notiz an.

Meldungen der Ausnahmen bleiben knapp und ohne Dateinamen: sie landen als Notiz im Fallkontext.
"""

from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 120  # Vorgabe des Katalogschluessels processing.office_convert_timeout_s
STDERR_TAIL = 300


class ConversionUnavailable(Exception):
    """LibreOffice ist auf diesem Host nicht vorhanden (SOFFICE_BIN oder soffice nicht im PATH)."""


class ConversionFailed(Exception):
    """Umwandlung gestartet, aber ohne brauchbare PDF beendet (Zeitueberschreitung, Fehlercode, keine Ausgabe)."""


def soffice_binary() -> str:
    return os.environ.get("SOFFICE_BIN") or "soffice"


def resolve_binary() -> str | None:
    """Absoluter Pfad der LibreOffice-Binaerdatei oder None, wenn sie fehlt."""
    binary = soffice_binary()
    if os.sep in binary:
        return binary if Path(binary).is_file() and os.access(binary, os.X_OK) else None
    return shutil.which(binary)


def available() -> bool:
    return resolve_binary() is not None


def convert_command(binary: str, path: Path, out_dir: Path) -> list[str]:
    return [
        binary,
        "--headless",
        "--norestore",
        "--convert-to",
        "pdf",
        "--outdir",
        str(out_dir),
        str(path),
    ]


def _run_with_timeout(cmd: list[str], env: dict[str, str], timeout_s: int) -> tuple[int, str]:
    """Startet cmd in eigener Prozessgruppe und liefert Rueckgabecode und stderr (26.09.2026).

    Bei Zeitueberschreitung wird die ganze Prozessgruppe mit SIGKILL beendet (soffice-Skript, oosplash und
    soffice.bin), der Kindprozess eingesammelt und subprocess.TimeoutExpired weitergereicht. So bleibt kein
    verwaister soffice.bin zurueck, der Kern und Speicher belegt oder nach dem Abbruch noch Dateien schreibt.
    """
    proc = subprocess.Popen(
        cmd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        _, stderr = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        kill_process_group(proc.pid)
        proc.communicate()
        raise
    return proc.returncode, stderr or ""


def kill_process_group(pid: int) -> None:
    """Beendet die Prozessgruppe des mit start_new_session gestarteten Prozesses (Gruppen-ID gleich pid)."""
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass  # Gruppe bereits beendet


def convert_to_pdf(path: Path, out_dir: Path, timeout_s: int = DEFAULT_TIMEOUT_S) -> Path:
    """Wandelt path nach out_dir/<stem>.pdf und liefert den Pfad der PDF.

    ConversionUnavailable, wenn die Binaerdatei fehlt; ConversionFailed bei Zeitueberschreitung, Fehlercode oder
    fehlender Ausgabedatei. Ein eigenes HOME je Aufruf liegt unter out_dir und wird danach entfernt.
    """
    binary = resolve_binary()
    if binary is None:
        raise ConversionUnavailable("LibreOffice nicht vorhanden")
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / (path.stem + ".pdf")
    target.unlink(missing_ok=True)
    home = Path(tempfile.mkdtemp(prefix="soffice-home-", dir=out_dir))
    env = {**os.environ, "HOME": str(home), "TMPDIR": str(home), "OMP_THREAD_LIMIT": "1"}
    try:
        try:
            returncode, stderr = _run_with_timeout(convert_command(binary, path, out_dir), env, timeout_s)
        except subprocess.TimeoutExpired as exc:
            raise ConversionFailed(f"Zeitlimit {timeout_s} s überschritten") from exc
        except OSError as exc:
            # Binaerdatei vorhanden, aber nicht startbar (Rechte, kaputter Link)
            raise ConversionUnavailable("LibreOffice nicht startbar") from exc
        tail = (stderr or "").strip()[-STDERR_TAIL:]
        if returncode != 0:
            logger.debug("soffice endete mit %s: %s", returncode, tail)
            raise ConversionFailed(f"Rückgabecode {returncode}")
        if not target.is_file() or target.stat().st_size == 0:
            # Rueckgabecode 0 ohne PDF: Quelle nicht ladbar oder Filter (writer, calc) nicht installiert
            logger.debug("soffice ohne Ausgabedatei: %s", tail)
            raise ConversionFailed("keine PDF erzeugt")
        return target
    finally:
        shutil.rmtree(home, ignore_errors=True)
