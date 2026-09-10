"""PDF-Ausgabe der Listen im HVM-CI, A4 quer (H 5.4, ReportLab): Kennlinie, Logo, Kopf mit Objekt, Verwaltungsart,
Stand und Seite X von Y auf jeder Seite, Abschnitte Aktuell, Historie, Offene Punkte als Tabellen mit wiederholtem
Kopf (Orange), alternierenden Zeilen (Hellgrau) und Linien (Umrissgrau); Tabellenschrift aus lists.pdf_table_font_pt
(Vorschlag 8 pt, ANNAHME A-35), Zeilenumbruch in Textzellen; Fusszeile mit Pflichtangaben."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from apps.lists.data import AMOUNT_COLUMNS, DATE_COLUMNS, OPEN_COLUMNS, ListData
from hvm_ci import briefbogen as ci

RAND = 12 * mm  # ANNAHME H 5.4 Nr. 1
TOP = 32 * mm  # unter Kennlinie und Kopfzeilen
BOTTOM = 20 * mm
MIN_WIDTH = {
    "Straße und Hausnummer": 28 * mm,
    "E-Mail": 30 * mm,
    "Abweichende Zustelladresse": 28 * mm,
    "Bemerkung": 26 * mm,
    "Hinweis": 40 * mm,
    "Prüfpunkt": 40 * mm,
}


def _fmt(value, column: str) -> str:
    if value in (None, ""):
        return ""
    if column in AMOUNT_COLUMNS and isinstance(value, Decimal | int | float):
        return f"{Decimal(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    if column in DATE_COLUMNS and isinstance(value, date | datetime):
        return value.strftime("%d.%m.%Y")
    if isinstance(value, Decimal):
        return f"{value.normalize():f}".replace(".", ",")
    return str(value)


def _widths(columns: list[str], rows: list[list[str]], total: float, font_pt: float) -> list[float]:
    char = font_pt * 0.55
    natural = []
    for i, name in enumerate(columns):
        longest = max([len(name)] + [len(r[i]) for r in rows]) if rows else len(name)
        natural.append(max(min(longest, 32) * char + 4, MIN_WIDTH.get(name, 12 * mm) * 0.6))
    factor = total / sum(natural)
    return [w * factor for w in natural]


def _table(columns: list[str], rows: list[dict], font_pt: float, width: float, empty_text: str) -> Table:
    body = ParagraphStyle(
        "zelle", fontName=ci.SCHRIFT, fontSize=font_pt, leading=font_pt * 1.2, alignment=TA_LEFT
    )
    head = ParagraphStyle("kopf", parent=body, fontName=ci.SCHRIFT_FETT)
    texts = [[_fmt(r.get(c), c) for c in columns] for r in rows]
    widths = _widths(columns, texts, width, font_pt)
    data = [[Paragraph(c, head) for c in columns]]
    if texts:
        data.extend(
            [[Paragraph(t.replace("&", "&amp;").replace("<", "&lt;"), body) for t in row] for row in texts]
        )
    else:
        data.append([Paragraph(empty_text, body)] + [Paragraph("", body) for _ in columns[1:]])
    table = Table(data, colWidths=widths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), ci.ORANGE),
        ("TEXTCOLOR", (0, 0), (-1, 0), ci.SCHWARZ),
        ("GRID", (0, 0), (-1, -1), 0.4, ci.UMRISS),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 1.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
    ]
    for i in range(1, len(data)):
        if i % 2 == 0:
            style.append(("BACKGROUND", (0, i), (-1, i), ci.HELL))
    table.setStyle(TableStyle(style))
    return table


def write_pdf(data: ListData, path: Path, *, font_pt: float = 8.0) -> Path:
    seite = ci.Seite.quer()
    header = data.header
    kopfzeilen = [
        header["management_type"],
        f"Stand: {header['generated_at'].strftime('%d.%m.%Y %H:%M')}",
    ]
    titel = f"{data.title} Objekt {header['object_number']} {header['object_name']}".strip()
    width = seite.breite - 2 * RAND
    section = ParagraphStyle("abschnitt", fontName=ci.SCHRIFT_FETT, fontSize=10.5, leading=13, spaceAfter=4)

    def story():
        out = []
        for name, rows, cols, empty in (
            ("Aktuell", data.current, data.columns, "Keine Datensätze"),
            ("Historie", data.history, data.columns, "Keine Datensätze"),
            ("Offene Punkte", data.open_items, OPEN_COLUMNS, "Keine offenen Punkte"),
        ):
            out.append(Paragraph(f"{name} ({len(rows)})", section))
            out.append(_table(cols, rows, font_pt, width, empty))
            out.append(Spacer(1, 6 * mm))
        if out and isinstance(out[-1], Spacer):
            out.pop()
        return out

    def build(target: Path, total: int) -> int:
        def on_page(canv, doc):
            canv.saveState()
            ci.listenkopf(canv, seite, titel, kopfzeilen, doc.page, total)
            canv.restoreState()

        doc = BaseDocTemplate(
            str(target),
            pagesize=seite.groesse,
            leftMargin=RAND,
            rightMargin=RAND,
            topMargin=TOP,
            bottomMargin=BOTTOM,
            title=titel,
            author="Hausverwaltung Müller GmbH",
        )
        frame = Frame(
            RAND,
            BOTTOM,
            width,
            seite.hoehe - TOP - BOTTOM,
            id="tabelle",
            leftPadding=0,
            rightPadding=0,
            topPadding=0,
            bottomPadding=0,
        )
        doc.addPageTemplates([PageTemplate(id="liste", frames=[frame], onPage=on_page)])
        doc.build(story())
        return doc.page

    path.parent.mkdir(parents=True, exist_ok=True)
    probe = path.with_suffix(".probe.pdf")
    pages = build(probe, 0)  # erster Durchlauf: Gesamtseitenzahl (H 5.4 Nr. 3)
    probe.unlink(missing_ok=True)
    build(path, pages)
    return path


__all__ = ["write_pdf", "PageBreak", "colors"]
