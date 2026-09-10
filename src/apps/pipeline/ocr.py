"""OCR je Block (Fachentwurf E 1.2 Schritt 7, docs/architektur.md 6.1 Nr. 4).

Seitenblock mit pikepdf herausschneiden, ocrmypdf --skip-text --jobs 1 --sidecar -l <ocr.language> --output-type pdf,
OMP_THREAD_LIMIT=1, nice und ionice (Wirkung hostabhaengig, ANNAHME A-25). Der Sidecar-Text wird je Originalseite
sofort maskiert und in den OCR-Cache geschrieben; Klartext wird verworfen. Das Original bleibt unveraendert (F25).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from apps.pipeline.storage import write_page
from objektakte.masking import mask_text

FORM_FEED = "\f"


class OcrError(Exception):
    pass


@dataclass
class OcrResult:
    pages: list[int]
    chars: int
    masked_hits: int
    duration_s: float


def tools_available() -> bool:
    return shutil.which("tesseract") is not None and shutil.which("gs") is not None


def extract_pages(source_pdf: Path, pages: list[int], target: Path) -> Path:
    import pikepdf

    with pikepdf.open(str(source_pdf)) as src, pikepdf.Pdf.new() as out:
        for p in pages:
            out.pages.append(src.pages[p - 1])
        out.save(str(target))
    return target


def ocrmypdf_command(input_pdf: Path, output_pdf: Path, sidecar: Path, language: str) -> list[str]:
    cmd = [
        sys.executable,
        "-m",
        "ocrmypdf",
        "--skip-text",
        "--jobs",
        "1",
        "--sidecar",
        str(sidecar),
        "-l",
        language,
        "--output-type",
        "pdf",
        "--quiet",
        str(input_pdf),
        str(output_pdf),
    ]
    prefix: list[str] = []
    if shutil.which("nice"):
        prefix += ["nice", "-n", "10"]
    if shutil.which("ionice"):
        prefix += ["ionice", "-c", "3"]
    return prefix + cmd


def run_ocr(input_pdf: Path, work: Path, language: str, *, timeout_s: int = 1700) -> str:
    output_pdf = work / (input_pdf.stem + ".ocr.pdf")
    sidecar = work / (input_pdf.stem + ".txt")
    env = {**os.environ, "OMP_THREAD_LIMIT": "1", "TMPDIR": str(work)}
    proc = subprocess.run(
        ocrmypdf_command(input_pdf, output_pdf, sidecar, language),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
    )
    if proc.returncode not in (
        0,
        6,
    ):  # 6: PriorOcrFoundError, kommt mit --skip-text nur bei vollstaendig digitalen Bloecken vor
        raise OcrError(f"ocrmypdf endete mit {proc.returncode}: {proc.stderr.strip()[-500:]}")
    if not sidecar.exists():
        raise OcrError("Sidecar-Text fehlt")
    return sidecar.read_text(encoding="utf-8", errors="replace")


def split_sidecar(text: str, expected_pages: int) -> list[str]:
    parts = text.split(FORM_FEED)
    if parts and parts[-1].strip() == "":
        parts = parts[:-1]
    while len(parts) < expected_pages:
        parts.append("")
    return parts[:expected_pages]


def mask_and_cache(
    sha256: str, page_no: int, text: str, *, source: str, hmac_key: bytes, engine: str = "tesseract"
) -> tuple[int, int]:
    """Maskierung vor jeder Persistierung (B-11); Rueckgabe Zeichen und Trefferzahl."""
    masked = mask_text(text, mode="fulltext", hmac_key=hmac_key)
    hits = [
        {
            "kind": h.kind,
            "start": h.start,
            "end": h.end,
            "last4": h.last4,
            "hmac": h.hmac_hex,
            "mod97_valid": h.mod97_valid,
            "context": h.context,
        }
        for h in masked.hits
    ]
    write_page(
        sha256,
        page_no,
        masked.text,
        hits,
        {
            "source": source,
            "engine": engine if source == "ocr" else None,
            "chars": len(masked.text),
            "masked": len(hits),
        },
    )
    return len(masked.text), len(hits)


def ocr_chunk(
    source_pdf: Path,
    sha256: str,
    pages: list[int],
    chunk_no: int,
    work: Path,
    *,
    language: str,
    hmac_key: bytes,
    heartbeat=None,
) -> OcrResult:
    import time

    started = time.monotonic()
    work.mkdir(parents=True, exist_ok=True)
    chunk_pdf = extract_pages(source_pdf, pages, work / f"chunk_{chunk_no:03d}.pdf")
    text = run_ocr(chunk_pdf, work, language)
    chars, hits_total = 0, 0
    for i, page_text in enumerate(split_sidecar(text, len(pages))):
        c, h = mask_and_cache(sha256, pages[i], page_text, source="ocr", hmac_key=hmac_key)
        chars += c
        hits_total += h
        if heartbeat:
            heartbeat(i + 1)
    for p in work.glob(f"chunk_{chunk_no:03d}*"):
        p.unlink(missing_ok=True)
    return OcrResult(pages, chars, hits_total, time.monotonic() - started)
