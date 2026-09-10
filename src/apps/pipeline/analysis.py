"""Formatweiche und Seitenanalyse (Fachentwurf E 1.2 Schritte 4 bis 6, 1.3; docs/architektur.md 6.1 Nr. 3, 4).

Eine Seite gilt als digital, wenn die Textebene mindestens ocr.digital_min_chars Zeichen hat, der Anteil
alphanumerischer Zeichen ueber ocr.digital_alnum_ratio liegt und die Seite nicht nur aus einem seitenfuellenden Bild
mit wenig Text besteht (ANNAHME A-11). Rein digitale Dateien starten ocrmypdf nicht. Bilder werden verlustfrei mit
img2pdf in PDF gewandelt, Office-Dateien ohne OCR extrahiert, andere Formate gehen in die manuelle Pruefung.
"""

from __future__ import annotations

import csv
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

PDF_MIMES = {"application/pdf"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".gif", ".webp"}
OFFICE_EXT = {".docx", ".xlsx", ".xlsm", ".csv", ".txt"}
GOOGLE_DOC_PREFIX = "application/vnd.google-apps."
logger = logging.getLogger(__name__)


@dataclass
class PageInfo:
    page_no: int
    chars: int
    alnum_ratio: float
    image_cover: float
    digital: bool
    reason: str


@dataclass
class Analysis:
    kind: str  # pdf | image | office | google_doc | unsupported
    page_count: int = 0
    pages: list[PageInfo] = field(default_factory=list)
    digital_pages: list[int] = field(default_factory=list)
    ocr_pages: list[int] = field(default_factory=list)
    chunks: list[list[int]] = field(default_factory=list)
    texts: dict[int, str] = field(
        default_factory=dict
    )  # Textebene digitaler Seiten (unmaskiert, nur im Speicher)
    pdf_path: str | None = None
    note: str | None = None

    @property
    def origin_kind(self) -> str | None:
        if self.kind not in ("pdf", "image"):
            return "digital" if self.kind in ("office", "google_doc") else None
        if self.ocr_pages and self.digital_pages:
            return "mixed"
        return "scan" if self.ocr_pages else "digital"

    def to_json(self) -> dict:
        d = asdict(self)
        d.pop("texts", None)
        return d


def detect_kind(path: Path, mime_type: str | None) -> str:
    ext = path.suffix.lower()
    mime = (mime_type or "").lower()
    if mime in PDF_MIMES or ext == ".pdf":
        return "pdf"
    if mime.startswith(GOOGLE_DOC_PREFIX):
        return "google_doc"
    if ext in IMAGE_EXT or mime.startswith("image/"):
        return "image"
    if ext in OFFICE_EXT:
        return "office"
    return "unsupported"


def image_to_pdf(image_path: Path, target: Path) -> Path:
    import img2pdf
    from PIL import Image

    with Image.open(image_path) as im:
        frames = getattr(im, "n_frames", 1)
        if im.mode in ("RGBA", "P", "LA") or frames > 1:
            pages = []
            for i in range(frames):
                im.seek(i)
                buf = target.with_suffix(f".p{i}.png")
                im.convert("RGB").save(buf)
                pages.append(buf)
            target.write_bytes(img2pdf.convert([str(p) for p in pages]))
            for p in pages:
                p.unlink(missing_ok=True)
            return target
    target.write_bytes(img2pdf.convert(str(image_path)))
    return target


def _alnum_ratio(text: str) -> float:
    stripped = re.sub(r"\s", "", text)
    if not stripped:
        return 0.0
    return sum(ch.isalnum() for ch in stripped) / len(stripped)


def _image_cover(pdf, page_index: int) -> float:
    """Anteil der Seitenflaeche, den das groesste Bildobjekt bedeckt (Bounding Box aus dem Inhaltsstrom)."""
    try:
        page = pdf[page_index]
        width, height = page.get_width(), page.get_height()
        if not width or not height:
            return 0.0
        best = 0.0
        for obj in page.get_objects(filter=(3,)):  # 3 = FPDF_PAGEOBJ_IMAGE
            getter = getattr(obj, "get_bounds", None) or getattr(obj, "get_pos", None)
            if getter is None:
                continue
            left, bottom, right, top = getter()
            area = max(0.0, right - left) * max(0.0, top - bottom)
            best = max(best, area / (width * height))
        return min(best, 1.0)
    except Exception:
        logger.debug("Bildflaeche der Seite %s nicht bestimmbar", page_index + 1, exc_info=True)
        return 0.0


def analyze_pdf(
    path: Path, *, min_chars: int, alnum_ratio: float, cover_ratio: float, chunk_pages: int
) -> Analysis:
    import pypdfium2 as pdfium

    result = Analysis(kind="pdf", pdf_path=str(path))
    pdf = pdfium.PdfDocument(str(path))
    try:
        result.page_count = len(pdf)
        for i in range(result.page_count):
            page = pdf[i]
            textpage = page.get_textpage()
            text = textpage.get_text_bounded() or ""
            textpage.close()
            chars = len(text.strip())
            ratio = _alnum_ratio(text)
            cover = _image_cover(pdf, i)
            digital, reason = True, "text_layer"
            if chars < min_chars:
                digital, reason = False, "too_few_chars"
            elif ratio < alnum_ratio:
                digital, reason = False, "garbage_text_layer"
            elif cover >= cover_ratio and chars < max(min_chars * 4, 200):
                digital, reason = False, "full_page_image"
            info = PageInfo(i + 1, chars, round(ratio, 3), round(cover, 3), digital, reason)
            result.pages.append(info)
            if digital:
                result.digital_pages.append(i + 1)
                result.texts[i + 1] = text
            else:
                result.ocr_pages.append(i + 1)
            page.close()
    finally:
        pdf.close()
    result.chunks = [
        result.ocr_pages[i : i + chunk_pages] for i in range(0, len(result.ocr_pages), chunk_pages)
    ]
    return result


def extract_office_text(path: Path) -> list[str]:
    """Textextraktion ohne OCR: docx je Absatz, xlsx je Blatt, csv und txt als eine Seite."""
    ext = path.suffix.lower()
    if ext == ".docx":
        import docx

        document = docx.Document(str(path))
        text = "\n".join(p.text for p in document.paragraphs)
        for table in document.tables:
            for row in table.rows:
                text += "\n" + "\t".join(c.text for c in row.cells)
        return [text]
    if ext in (".xlsx", ".xlsm"):
        import openpyxl

        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        pages = []
        for ws in wb.worksheets:
            lines = [
                "\t".join("" if c is None else str(c) for c in row) for row in ws.iter_rows(values_only=True)
            ]
            pages.append(f"[Blatt {ws.title}]\n" + "\n".join(lines))
        return pages or [""]
    if ext == ".csv":
        data = path.read_bytes()
        for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=";,\t|")
        except csv.Error:
            dialect = csv.excel
        rows = list(csv.reader(text.splitlines(), dialect))
        return ["\n".join("\t".join(r) for r in rows)]
    return [path.read_text(encoding="utf-8", errors="replace")]


def analyze_file(
    path: Path,
    mime_type: str | None,
    *,
    min_chars: int,
    alnum_ratio: float,
    cover_ratio: float,
    chunk_pages: int,
    work: Path,
) -> Analysis:
    kind = detect_kind(path, mime_type)
    if kind == "pdf":
        return analyze_pdf(
            path,
            min_chars=min_chars,
            alnum_ratio=alnum_ratio,
            cover_ratio=cover_ratio,
            chunk_pages=chunk_pages,
        )
    if kind == "image":
        pdf_path = image_to_pdf(path, work / "converted.pdf")
        result = analyze_pdf(
            pdf_path,
            min_chars=min_chars,
            alnum_ratio=alnum_ratio,
            cover_ratio=cover_ratio,
            chunk_pages=chunk_pages,
        )
        result.kind = "image"
        return result
    if kind == "office":
        texts = extract_office_text(path)
        result = Analysis(kind="office", page_count=len(texts), digital_pages=list(range(1, len(texts) + 1)))
        result.texts = {i + 1: t for i, t in enumerate(texts)}
        result.pages = [
            PageInfo(i + 1, len(t), round(_alnum_ratio(t), 3), 0.0, True, "office_text")
            for i, t in enumerate(texts)
        ]
        return result
    return Analysis(
        kind=kind,
        note="Format nicht unterstützt, manuelle Prüfung"
        if kind == "unsupported"
        else "Google-Dokument, Export folgt in M6",
    )
