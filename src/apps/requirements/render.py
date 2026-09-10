"""Erzeugung der Nachforderung als PDF (ReportLab, HVM-Briefbogen aus hvm_ci) und DOCX (python-docx) aus denselben
Daten (H 4.4); keine Konvertierung zwischen den Formaten. Entwuerfe tragen Wasserzeichen und Hinweiszeile, die
freigegebene Fassung traegt beides nicht und bettet das Unterschriftsbild ein, wenn es im Betrieb hinterlegt ist."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from hvm_ci import briefbogen as ci

ATTACHMENT_COLUMNS = ("Einheit", "Eigentümer", "Unterlage", "Jahr")


def _recipient_lines(req) -> list[str]:
    lines = [req.recipient_name or "[Vorverwaltung]"]
    if req.recipient_contact_person:
        lines.append(req.recipient_contact_person)
    street = " ".join(p for p in (req.recipient_street, req.recipient_house_number) if p)
    lines.append(street or "[Straße und Hausnummer]")
    city = " ".join(p for p in (req.recipient_postal_code, req.recipient_city) if p)
    lines.append(city or "[PLZ Ort]")
    return lines


def _info_pairs(req, letter_date: date) -> list[tuple[str, str]]:
    pairs = []
    if req.reference:
        pairs.append(("Ihr Zeichen", req.reference))
    pairs.append(("Unser Zeichen", f"Objekt {req.object.object_number}"))
    person = getattr(req.created_by, "display_name", None)
    if person:
        pairs.append(("Ansprechpartner", person))
    pairs.append(("Datum", letter_date.strftime("%d.%m.%Y")))
    return pairs


class _Pdf:
    """Seitenweiser Fluss mit Briefkopf auf Seite 1 und Folgeseitenkopf."""

    def __init__(self, path: Path, *, final: bool):
        self.seite = ci.Seite.hoch()
        self.c = canvas.Canvas(str(path), pagesize=self.seite.groesse)
        self.final = final
        self.page = 1
        self.y = 0.0
        self.bottom = ci.RAND_U + 4 * mm

    def start_page1(self) -> None:
        if not self.final:
            ci.entwurf_wasserzeichen(self.c, self.seite)
        ci.briefkopf_seite1(self.c, self.seite)
        ci.fusszeile(self.c, self.seite)

    def new_page(self) -> None:
        self.c.showPage()
        self.page += 1
        if not self.final:
            ci.entwurf_wasserzeichen(self.c, self.seite)
        ci.folgeseite(self.c, self.seite, self.page)
        ci.fusszeile(self.c, self.seite)
        self.y = self.seite.hoehe - 25 * mm

    def ensure(self, needed: float) -> None:
        if self.y - needed < self.bottom:
            self.new_page()

    def text(
        self, text: str, *, size: float = 10.5, bold: bool = False, indent: float = 0.0, gap: float = 1.0
    ) -> None:
        font = ci.SCHRIFT_FETT if bold else ci.SCHRIFT
        width = self.seite.textbreite - indent
        for line in ci.umbrechen(text, width, font, size):
            self.ensure(size * 1.35)
            self.c.setFont(font, size)
            self.c.setFillColor(ci.SCHWARZ)
            self.c.drawString(ci.RAND_L + indent, self.y, line)
            self.y -= size * 1.35
        self.y -= size * 0.35 * gap

    def bullet(self, text: str) -> None:
        self.ensure(10.5 * 1.35)
        self.c.setFont(ci.SCHRIFT, 10.5)
        self.c.setFillColor(ci.ORANGE)
        self.c.rect(ci.RAND_L + 1 * mm, self.y + 1.2 * mm, 1.6 * mm, 1.6 * mm, fill=1, stroke=0)
        self.c.setFillColor(ci.SCHWARZ)
        self.text(text, indent=6 * mm, gap=0.2)

    def blank(self, factor: float = 1.0) -> None:
        self.y -= 4 * mm * factor


def render_pdf(req, letter, path: Path, *, final: bool, signature: Path | None, letter_date: date) -> Path:
    pdf = _Pdf(path, final=final)
    c, s = pdf.c, pdf.seite
    pdf.start_page1()
    ci.anschriftfeld(c, s, _recipient_lines(req))
    ci.infoblock(c, s, _info_pairs(req, letter_date))
    y = s.hoehe - 100 * mm
    if not final:
        c.setFont(ci.SCHRIFT, 8.5)
        c.setFillColor(ci.ANTHRAZIT)
        from apps.requirements.requests import DRAFT_HINT

        c.drawString(ci.RAND_L, y + 7 * mm, DRAFT_HINT)
    ci.betreff(c, s, letter.subject, y)
    pdf.y = y - 10 * mm
    pdf.text(letter.salutation)
    for p in letter.paragraphs:
        pdf.text(p)
    for g in letter.groups:
        pdf.text(g["title"], bold=True, gap=0.3)
        if letter.attachment:
            pdf.text(f"{len(g['lines'])} Positionen, siehe Anlage", indent=6 * mm, gap=0.5)
        else:
            for line in g["lines"]:
                pdf.bullet(line)
            pdf.blank(0.5)
    pdf.text(letter.deadline_text)
    if letter.closing:
        pdf.text(letter.closing)
    pdf.ensure(45 * mm)
    pdf.text(ci.GRUSSFORMEL, gap=0.0)
    pdf.y = ci.unterschriftsblock(c, s, ci.RAND_L, pdf.y, signature if final else None)
    if letter.attachment:
        _attachment_pages(pdf, letter.positions)
    c.save()
    return path


def _attachment_pages(pdf: _Pdf, positions: list[dict]) -> None:
    widths = [25 * mm, 55 * mm, 70 * mm, 15 * mm]
    xs = [ci.RAND_L]
    for w in widths[:-1]:
        xs.append(xs[-1] + w)

    def header() -> None:
        pdf.c.setFont(ci.SCHRIFT_FETT, 9)
        pdf.c.setFillColor(ci.ANTHRAZIT)
        for x, title in zip(xs, ATTACHMENT_COLUMNS, strict=True):
            pdf.c.drawString(x, pdf.y, title)
        pdf.y -= 3 * mm
        pdf.c.setStrokeColor(ci.HELL)
        pdf.c.line(ci.RAND_L, pdf.y, ci.RAND_L + sum(widths), pdf.y)
        pdf.y -= 4.5 * mm

    pdf.new_page()
    pdf.text("Anlage: Einzelaufstellung der fehlenden Unterlagen", bold=True)
    header()
    for p in positions:
        cells = [
            p.get("unit_label") or "Objekt",
            p.get("owner") or "",
            p.get("check_name") or "",
            str(p.get("period_year") or ""),
        ]
        wrapped = [
            ci.umbrechen(cell, w - 2 * mm, ci.SCHRIFT, 8.5) for cell, w in zip(cells, widths, strict=True)
        ]
        height = max(len(w) for w in wrapped) * 4 * mm
        if pdf.y - height < pdf.bottom:
            pdf.new_page()
            header()
        pdf.c.setFont(ci.SCHRIFT, 8.5)
        pdf.c.setFillColor(ci.SCHWARZ)
        for x, lines in zip(xs, wrapped, strict=True):
            yy = pdf.y
            for line in lines:
                pdf.c.drawString(x, yy, line)
                yy -= 4 * mm
        pdf.y -= height + 1 * mm


# ---------------------------------------------------------------- DOCX
def _shade(cell, hex_color: str) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_color)
    tc_pr.append(shd)


def _grey(run, size: float, color: str = "87888A") -> None:
    from docx.shared import Pt, RGBColor

    run.font.size = Pt(size)
    run.font.color.rgb = RGBColor.from_string(color)


def render_docx(req, letter, path: Path, *, final: bool, signature: Path | None, letter_date: date) -> Path:
    from docx import Document as DocxDocument
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Mm, Pt, RGBColor

    from apps.requirements.requests import DRAFT_HINT

    doc = DocxDocument()
    section = doc.sections[0]
    section.page_width, section.page_height = Mm(210), Mm(297)
    section.left_margin, section.right_margin = Mm(25), Mm(20)
    section.top_margin, section.bottom_margin = Mm(20), Mm(28)
    normal = doc.styles["Normal"]
    normal.font.name = "Arial"
    normal.font.size = Pt(10.5)
    normal.font.color.rgb = RGBColor.from_string("1A1A1A")
    # Kopf: Kennlinie als vier eingefaerbte Zellen, Logo rechts
    header = section.header
    band = header.add_table(rows=1, cols=4, width=Mm(165))
    band.alignment = WD_TABLE_ALIGNMENT.CENTER
    for cell, color, width in zip(
        band.rows[0].cells, ("87888A", "9C9D9F", "E6A83C", "D7D8DA"), (66, 33, 12, 54), strict=True
    ):
        _shade(cell, color)
        cell.width = Mm(width)
        cell.paragraphs[0].paragraph_format.space_after = Pt(0)
        cell.paragraphs[0].add_run(" ").font.size = Pt(2)
    logo_p = header.add_paragraph()
    logo_p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    logo_p.add_run().add_picture(str(ci.LOGO), width=Mm(40))
    sender = header.add_paragraph()
    _grey(sender.add_run(ci.ABSENDERZEILE), 7.5)
    if not final:
        hint = header.add_paragraph()
        run = hint.add_run("ENTWURF, " + DRAFT_HINT.split(", ", 1)[1])
        run.bold = True
        run.font.size = Pt(9)
        run.font.color.rgb = RGBColor.from_string("E6A83C")
    footer = section.footer
    f1 = footer.paragraphs[0]
    f1.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _grey(f1.add_run(ci.FUSS_1), 7.5)
    f2 = footer.add_paragraph()
    f2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _grey(f2.add_run(ci.FUSS_2), 7.5)
    # Anschrift und Infoblock
    for line in _recipient_lines(req):
        p = doc.add_paragraph(line)
        p.paragraph_format.space_after = Pt(0)
    doc.add_paragraph()
    info = doc.add_table(rows=0, cols=2)
    for label, value in _info_pairs(req, letter_date):
        row = info.add_row().cells
        _grey(row[0].paragraphs[0].add_run(label), 8)
        row[1].paragraphs[0].add_run(value).font.size = Pt(9.5)
    doc.add_paragraph()
    subj = doc.add_paragraph()
    run = subj.add_run(letter.subject)
    run.bold = True
    run.font.size = Pt(11.5)
    doc.add_paragraph(letter.salutation)
    for para in letter.paragraphs:
        doc.add_paragraph(para)
    for g in letter.groups:
        title = doc.add_paragraph()
        title.add_run(g["title"]).bold = True
        if letter.attachment:
            doc.add_paragraph(f"{len(g['lines'])} Positionen, siehe Anlage")
        else:
            for line in g["lines"]:
                doc.add_paragraph(line, style="List Bullet")
    doc.add_paragraph(letter.deadline_text)
    if letter.closing:
        doc.add_paragraph(letter.closing)
    doc.add_paragraph(ci.GRUSSFORMEL)
    if final and signature is not None and Path(signature).exists():
        doc.add_paragraph().add_run().add_picture(str(signature), width=Mm(40))
    else:
        doc.add_paragraph()
        doc.add_paragraph()
    name, funktion, firma = ci.UNTERZEICHNER
    doc.add_paragraph(name).paragraph_format.space_after = Pt(0)
    p = doc.add_paragraph()
    _grey(p.add_run(funktion), 9.5)
    p.paragraph_format.space_after = Pt(0)
    _grey(doc.add_paragraph().add_run(firma), 9.5)
    if letter.attachment:
        doc.add_page_break()
        doc.add_paragraph().add_run("Anlage: Einzelaufstellung der fehlenden Unterlagen").bold = True
        table = doc.add_table(rows=1, cols=4)
        table.style = "Table Grid"
        for cell, title in zip(table.rows[0].cells, ATTACHMENT_COLUMNS, strict=True):
            _shade(cell, "D7D8DA")
            cell.paragraphs[0].add_run(title).bold = True
        for pos in letter.positions:
            cells = table.add_row().cells
            for cell, value in zip(
                cells,
                (
                    pos.get("unit_label") or "Objekt",
                    pos.get("owner") or "",
                    pos.get("check_name") or "",
                    str(pos.get("period_year") or ""),
                ),
                strict=True,
            ):
                cell.paragraphs[0].add_run(value).font.size = Pt(9)
    doc.save(str(path))
    return path
