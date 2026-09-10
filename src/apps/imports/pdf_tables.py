"""Tabellen aus PDF-Listen der Vorverwaltung (Fachentwurf H 6.2, Profile pdf_digital_table und pdf_scan_ocr).

Digital: pdfplumber liest Tabellen ueber Linien; ohne Linien werden Woerter mit Koordinaten zu Zeilen (Oberkante y)
und Spalten (Luecken in der x-Verteilung ueber alle Zeilen aller Seiten) zusammengesetzt. Scan: Seiten werden mit
pypdfium2 gerastert und mit Tesseract als TSV gelesen (Wortkoordinaten und Wortkonfidenz); die Zellkonfidenz ist der
Mittelwert der Wortkonfidenzen der Zelle. Mehrseitige Tabellen mit wiederholter Kopfzeile werden zusammengefuehrt,
Fusszeilen (Seitenzahlen) werden erkannt und im Protokoll markiert, nicht verworfen.
"""

from __future__ import annotations

import csv
import io
import os
import re
import shutil
import subprocess
from bisect import bisect_right
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from statistics import fmean, median

FOOTER = re.compile(r"^\s*(seite|page|blatt)?\s*\d{1,4}\s*((von|/|of)\s*\d{1,4})?\s*$", re.IGNORECASE)
MIN_TEXT_CHARS = 50  # Digitalkriterium wie in der Seitenanalyse (A-11)
TSV_WORD_LEVEL = "5"
LEAD_ROWS = 3  # Zeilen am Seitenanfang, die als wiederholte Kopfzeile in Frage kommen (Titel, Kopfzeile)


@dataclass
class Word:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float
    conf: float | None = None  # 0 bis 100 aus der OCR, None bei digitalem Text

    @property
    def height(self) -> float:
        return max(self.bottom - self.top, 0.1)

    @property
    def center(self) -> float:
        return (self.x0 + self.x1) / 2


@dataclass
class PageTable:
    rows: list[list[str]]
    confidence: list[list[float | None]]
    method: str  # lines | words | ocr
    footers: list[str] = field(default_factory=list)


@dataclass
class TableResult:
    rows: list[list[str]]
    confidence: list[list[float | None]] | None
    meta: dict


# ---------------------------------------------------------------- Layoutanalyse
def group_rows(words: list[Word]) -> list[list[Word]]:
    """Zeilenbildung ueber y: ein Wort gehoert zur laufenden Zeile, wenn seine Oberkante hoechstens 60 Prozent der
    Medianhoehe aller Woerter von der Zeilenoberkante abweicht."""
    if not words:
        return []
    tol = 0.6 * median(w.height for w in words)
    rows: list[list[Word]] = []
    for w in sorted(words, key=lambda w: (w.top, w.x0)):
        if rows and abs(w.top - rows[-1][0].top) <= tol:
            rows[-1].append(w)
        else:
            rows.append([w])
    return [sorted(r, key=lambda w: w.x0) for r in rows]


def column_bounds(rows: list[list[Word]], min_gap: float | None = None) -> list[float]:
    """Spaltengrenzen aus Luecken der x-Verteilung: Wortspannen aller Zeilen werden vereinigt, solange ihr Abstand
    unter min_gap liegt (Vorgabe 90 Prozent der Medianhoehe); jede verbleibende Luecke trennt zwei Spalten."""
    # Titel- und Fusszeilen (wenige Woerter, Seitenzahl) verzerren die Spaltenbildung und bleiben aussen vor
    candidates = [r for r in rows if r and not FOOTER.match(" ".join(w.text for w in r))]
    if candidates:
        typical = median(len(r) for r in candidates)
        candidates = [r for r in candidates if len(r) >= 0.6 * typical] or candidates
    words = [w for r in candidates for w in r]
    if not words:
        return []
    if min_gap is None:
        min_gap = 0.9 * median(w.height for w in words)
    merged: list[list[float]] = []
    for x0, x1 in sorted((w.x0, w.x1) for w in words):
        if merged and x0 <= merged[-1][1] + min_gap:
            merged[-1][1] = max(merged[-1][1], x1)
        else:
            merged.append([x0, x1])
    return [(a[1] + b[0]) / 2 for a, b in zip(merged, merged[1:], strict=False)]


def build_cells(
    rows: list[list[Word]], bounds: list[float]
) -> tuple[list[list[str]], list[list[float | None]]]:
    ncols = len(bounds) + 1
    texts: list[list[str]] = []
    confs: list[list[float | None]] = []
    for row in rows:
        cells: list[list[str]] = [[] for _ in range(ncols)]
        cell_conf: list[list[float]] = [[] for _ in range(ncols)]
        for w in row:
            idx = bisect_right(bounds, w.center)
            cells[idx].append(w.text)
            if w.conf is not None:
                cell_conf[idx].append(w.conf)
        texts.append([" ".join(c).strip() for c in cells])
        confs.append([round(fmean(c), 1) if c else None for c in cell_conf])
    return texts, confs


def _norm(cells: list[str]) -> tuple[str, ...]:
    return tuple(re.sub(r"\s+", " ", c or "").strip().lower() for c in cells)


def is_footer(cells: list[str]) -> bool:
    filled = [c for c in cells if (c or "").strip()]
    return len(filled) == 1 and bool(FOOTER.match(filled[0]))


def merge_pages(pages: list[PageTable]) -> TableResult:
    """Zusammenfuehrung: Titel und Kopfzeile der ersten Seite bleiben; gleiche Zeilen am Anfang weiterer Seiten
    (wiederholte Kopfzeile) entfallen; Fusszeilen mit Seitenzahl werden gesammelt; Leerzeilen des Layouts entfallen."""
    width = max((len(r) for p in pages for r in p.rows), default=0)
    rows: list[list[str]] = []
    confs: list[list[float | None]] = []
    footers: list[str] = []
    lead: set[tuple[str, ...]] = set()
    for index, page in enumerate(pages):
        at_start = index > 0
        seen_on_page = 0
        for cells, conf in zip(page.rows, page.confidence, strict=False):
            cells = list(cells) + [""] * (width - len(cells))
            conf = list(conf) + [None] * (width - len(conf))
            if not any((c or "").strip() for c in cells):
                continue
            if is_footer(cells):
                footers.append(" ".join(c for c in cells if c).strip())
                continue
            key = _norm(cells)
            if index == 0 and seen_on_page < LEAD_ROWS:
                lead.add(key)
            elif at_start and key in lead:
                continue
            at_start = False
            seen_on_page += 1
            rows.append(cells)
            confs.append(conf)
        footers.extend(page.footers)
    return TableResult(rows, confs, {"footers": footers, "pages": len(pages)})


# ---------------------------------------------------------------- digital (pdfplumber)
def has_text_layer(path: Path, pages: int = 2) -> bool:
    import pdfplumber

    with pdfplumber.open(str(path)) as pdf:
        return sum(len(p.chars) for p in pdf.pages[:pages]) >= MIN_TEXT_CHARS


def _plumber_words(page) -> list[Word]:
    return [
        Word(w["text"], float(w["x0"]), float(w["x1"]), float(w["top"]), float(w["bottom"]))
        for w in page.extract_words(x_tolerance=1.5, y_tolerance=2, keep_blank_chars=False)
    ]


def extract_digital(path: Path, *, max_pages: int = 200) -> TableResult:
    """Tabellen je Seite ueber Linien (pdfplumber); Seiten ohne Linientabelle ueber Woerter mit Koordinaten, die
    Spaltengrenzen gelten fuer alle Wortseiten gemeinsam."""
    import pdfplumber

    pages: list[PageTable] = []
    word_pages: list[tuple[int, list[list[Word]]]] = []
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages[:max_pages]:
            tables = [
                t for t in (page.extract_tables() or []) if t and len(t) >= 2 and max(len(r) for r in t) >= 2
            ]
            if tables:
                rows = [[re.sub(r"\s+", " ", c or "").strip() for c in r] for t in tables for r in t]
                footers = []
                bottom = max((float(tb.bbox[3]) for tb in page.find_tables()), default=float(page.height))
                for line in group_rows([w for w in _plumber_words(page) if w.top > bottom + 2]):
                    text = " ".join(w.text for w in line)
                    if FOOTER.match(text):
                        footers.append(text)
                pages.append(PageTable(rows, [[None] * len(r) for r in rows], "lines", footers))
            else:
                pages.append(PageTable([], [], "words"))
                word_pages.append((len(pages) - 1, group_rows(_plumber_words(page))))
    if word_pages:
        bounds = column_bounds([r for _, rows in word_pages for r in rows])
        for idx, rows in word_pages:
            texts, confs = build_cells(rows, bounds)
            pages[idx] = PageTable(texts, confs, "words")
    result = merge_pages(pages)
    result.confidence = None  # digitaler Text traegt keine Konfidenz
    result.meta["method"] = "+".join(sorted({p.method for p in pages})) or "none"
    return result


# ---------------------------------------------------------------- Scan (Tesseract TSV)
def run_tesseract(image: Path, language: str) -> str:
    """Tesseract als TSV (Wortkoordinaten und Konfidenz), ein Thread wie in der OCR-Pipeline (A-08)."""
    if shutil.which("tesseract") is None:
        raise RuntimeError("tesseract nicht installiert")
    env = {**os.environ, "OMP_THREAD_LIMIT": "1"}
    proc = subprocess.run(
        # Seitensegmentierung automatisch, Tabellenerkennung aus: Tesseract laesst sonst Text in erkannten
        # Tabellenbereichen weg (Gitterlinien); die Zuordnung zu Zeilen und Spalten erfolgt hier ueber Koordinaten
        [
            "tesseract",
            str(image),
            "stdout",
            "-l",
            language,
            "--psm",
            "3",
            "-c",
            "textord_tabfind_find_tables=0",
            "tsv",
        ],
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"tesseract endete mit {proc.returncode}: {proc.stderr[:300]}")
    return proc.stdout


def parse_tsv(tsv: str) -> list[Word]:
    words: list[Word] = []
    reader = csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE)
    for rec in reader:
        if rec.get("level") != TSV_WORD_LEVEL:
            continue
        text = (rec.get("text") or "").strip()
        if not text or not any(ch.isalnum() for ch in text):
            continue  # leere Woerter und Linienreste wie | oder _
        left, top, width, height = (float(rec[k]) for k in ("left", "top", "width", "height"))
        conf = float(rec.get("conf") or -1)
        words.append(Word(text, left, left + width, top, top + height, conf if conf >= 0 else None))
    return words


def render_pages(path: Path, target_dir: Path, *, dpi: int = 300, max_pages: int = 50) -> list[Path]:
    if path.suffix.lower() != ".pdf":
        return [path]
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(path))
    out: list[Path] = []
    try:
        for index in range(min(len(pdf), max_pages)):
            page = pdf[index]
            try:
                image = page.render(scale=dpi / 72).to_pil()
            finally:
                page.close()
            target = target_dir / f"seite-{index + 1:04d}.png"
            image.save(target)
            out.append(target)
    finally:
        pdf.close()
    return out


def extract_scan(
    path: Path,
    *,
    language: str = "deu",
    dpi: int = 300,
    max_pages: int = 50,
    runner: Callable[[Path, str], str] | None = None,
) -> TableResult:
    """OCR je Seite mit Wortkonfidenzen; Layoutanalyse wie beim digitalen PDF; Zellkonfidenz je Zelle."""
    from apps.pipeline import storage

    runner = runner or run_tesseract
    tmp = storage.work_tmp_dir()
    try:
        images = render_pages(path, tmp, dpi=dpi, max_pages=max_pages)
        page_rows = [group_rows(parse_tsv(runner(img, language))) for img in images]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    bounds = column_bounds([r for rows in page_rows for r in rows])
    pages = []
    for rows in page_rows:
        texts, confs = build_cells(rows, bounds)
        pages.append(PageTable(texts, confs, "ocr"))
    result = merge_pages(pages)
    result.meta.update({"method": "ocr", "language": language, "dpi": dpi})
    return result
