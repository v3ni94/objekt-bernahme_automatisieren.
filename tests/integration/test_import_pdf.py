"""M9: PDF-Listen (digital mit Linien ueber drei Seiten, digital ohne Linien ueber Woerter mit Koordinaten, Scan mit
Wortkonfidenzen und unsicheren Zellen), Mieterliste mit leases und Rollen (H 6.9), Listenerkennung aus der Pipeline mit
Import nach Bestaetigung im Review Center (B-34)."""

from __future__ import annotations

import shutil
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle

from apps.imports import pdf_tables, services
from apps.imports.models import ImportRow
from apps.imports.profiles import PROFILES, choose_profile
from apps.imports.services import Decision
from apps.objects.models import ManagedObject
from apps.parties.models import Lease, TenantUnitAssignment
from apps.review.models import ReviewCase

pytestmark = pytest.mark.django_db
FIX = Path(__file__).resolve().parents[1] / "fixtures" / "imports"
HEADER = ["Einheit", "Eigentümer", "MEA", "Hausgeld", "Eigentumsbeginn"]
NAMES = ["Mustermann, Max", "Beispiel, Erika", "Altmuster, Karl", "Neumuster GmbH"]


def owner_rows(n: int) -> list[list[str]]:
    return [
        [f"WE{i + 1:02d}", NAMES[i % len(NAMES)], f"{10 + i},5/1000", f"{200 + i},00", "01.01.2020"]
        for i in range(n)
    ]


@pytest.fixture
def objekt(seeded):
    return ManagedObject.objects.create(
        object_number="623", name="Musterstadt, Musterweg 1", management_type="weg", is_test=True
    )


def pdf_with_grid(path: Path, rows: list[list[str]]) -> None:
    """Tabelle mit Gitterlinien, Kopfzeile je Seite wiederholt, Seitenzahl in der Fusszeile."""

    def footer(canv, doc):
        canv.setFont("Helvetica", 8)
        canv.drawString(100 * mm, 12 * mm, f"Seite {doc.page}")

    doc = SimpleDocTemplate(str(path), pagesize=A4, topMargin=20 * mm, bottomMargin=25 * mm)
    table = Table([HEADER, *rows], repeatRows=1)
    table.setStyle(
        TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black), ("FONTSIZE", (0, 0), (-1, -1), 9)])
    )
    doc.build([table], onFirstPage=footer, onLaterPages=footer)


def pdf_without_grid(path: Path, rows: list[list[str]], per_page: int = 15) -> None:
    """Spalten ueber feste x-Positionen ohne Linien; Titel und Kopfzeile je Seite, Fusszeile Seite n von m."""
    c = canvas.Canvas(str(path), pagesize=A4)
    width, height = A4
    xs = [20 * mm, 45 * mm, 105 * mm, 135 * mm, 165 * mm]
    pages = [rows[i : i + per_page] for i in range(0, len(rows), per_page)]
    for n, chunk in enumerate(pages, start=1):
        y = height - 20 * mm
        c.setFont("Helvetica-Bold", 11)
        c.drawString(xs[0], y, "Eigentümerliste Objekt 623")
        y -= 8 * mm
        c.setFont("Helvetica-Bold", 9)
        for x, cell in zip(xs, HEADER, strict=True):
            c.drawString(x, y, cell)
        c.setFont("Helvetica", 9)
        for row in chunk:
            y -= 6 * mm
            for x, cell in zip(xs, row, strict=True):
                c.drawString(x, y, cell)
        c.setFont("Helvetica", 8)
        c.drawString(100 * mm, 12 * mm, f"Seite {n} von {len(pages)}")
        c.showPage()
    c.save()


def fake_tsv(rows: list[list[tuple[str, float]]]) -> str:
    """TSV wie Tesseract: Woerter (level 5) mit Koordinaten in Pixel bei 300 dpi und Konfidenz."""
    lines = ["level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"]
    xs = [200, 500, 1200, 1600, 2000]
    for r, row in enumerate(rows):
        top = 300 + r * 60
        for cidx, (text, conf) in enumerate(row):
            x = xs[cidx]
            for w, word in enumerate(text.split()):
                width = 22 * len(word)
                lines.append(f"5\t1\t1\t1\t{r + 1}\t{w + 1}\t{x}\t{top}\t{width}\t40\t{conf}\t{word}")
                x += width + 12
    return "\n".join(lines) + "\n"


def test_digitale_tabelle_mit_linien_ueber_drei_seiten(objekt, admin_user, tmp_path):
    pdf = tmp_path / "eigentuemerliste.pdf"
    pdf_with_grid(pdf, owner_rows(90))
    result = pdf_tables.extract_digital(pdf)
    assert result.meta["pages"] >= 3 and result.meta["method"] == "lines"
    assert result.rows[0] == HEADER and len(result.rows) == 91  # Kopfzeile einmal, 90 Datenzeilen
    assert sum(1 for r in result.rows if r == HEADER) == 1
    assert len(result.meta["footers"]) == result.meta["pages"]
    profile, scores = choose_profile(pdf, pdf.name)
    assert profile.code == "pdf_digital_table" and scores["pdf_scan_ocr"] == 0.0
    batch, _ = services.create_batch(objekt, filename=pdf.name, data=pdf.read_bytes(), user=admin_user)
    services.parse_batch(batch)
    services.normalize_batch(batch, user=admin_user)
    batch.refresh_from_db()
    targets = batch.column_mapping["targets"]
    assert {"unit_label", "owner_name_raw", "co_ownership_share", "house_fee_monthly", "valid_from"} <= set(
        targets
    )
    assert batch.rows_total == 90 and services.status_sum_matches(batch)
    assert batch.source_format == "pdf_digital" and batch.column_mapping["meta"]["method"] == "lines"


def test_digitale_tabelle_ohne_linien_woerter_mit_koordinaten(tmp_path):
    pdf = tmp_path / "liste_ohne_linien.pdf"
    pdf_without_grid(pdf, owner_rows(40))
    result = pdf_tables.extract_digital(pdf)
    assert result.meta["method"] == "words" and result.meta["pages"] == 3
    assert result.rows[0][0] == "Eigentümerliste" and result.rows[1] == HEADER
    assert len(result.rows) == 42  # Titel, Kopfzeile, 40 Datenzeilen; Wiederholungen je Seite entfallen
    assert result.rows[2] == ["WE01", "Mustermann, Max", "10,5/1000", "200,00", "01.01.2020"]
    assert result.rows[-1][0] == "WE40"
    assert result.meta["footers"] == ["Seite 1 von 3", "Seite 2 von 3", "Seite 3 von 3"]


def test_scan_liste_mit_niedriger_wortkonfidenz(objekt, admin_user, tmp_path, monkeypatch):
    from PIL import Image

    png = tmp_path / "liste_scan.png"
    Image.new("RGB", (2480, 3508), "white").save(png)
    rows = [[(h, 96.0) for h in HEADER]]
    for i, r in enumerate(owner_rows(6)):
        confs = [95.0] * 5
        if i == 2:
            confs[1] = 41.0  # Eigentuemername unsicher
        if i == 4:
            confs[2] = 55.5  # Miteigentumsanteil unsicher
        rows.append(list(zip(r, confs, strict=True)))
    rows.append([("Seite 1", 90.0)])
    calls = []

    def runner(image, language):
        calls.append((image.name, language))
        return fake_tsv(rows)

    monkeypatch.setattr(pdf_tables, "run_tesseract", runner)
    profile, _ = choose_profile(png, png.name)
    assert profile.code == "pdf_scan_ocr"
    batch, _ = services.create_batch(objekt, filename=png.name, data=png.read_bytes(), user=admin_user)
    services.parse_batch(batch)
    assert calls == [("liste_scan.png", "deu")]
    services.normalize_batch(batch, user=admin_user)
    batch.refresh_from_db()
    rows_db = list(ImportRow.objects.filter(batch=batch).order_by("row_no", "sub_index"))
    assert batch.rows_total == 6 and services.status_sum_matches(batch)  # nichts verworfen
    low = [r for r in rows_db if "low_ocr_confidence" in (r.uncertainty_reasons or [])]
    assert [r.row_no for r in low] == [4, 6]
    assert low[0].status == "uncertain" and low[0].parsed_fields["_low_confidence_cells"] == [
        {"column": "Eigentümer", "confidence": 41.0}
    ]
    assert low[1].parsed_fields["_low_confidence_cells"][0]["column"] == "MEA"
    assert all(r.status == "parsed" for r in rows_db if r not in low)
    meta = batch.column_mapping["meta"]
    assert meta["footers"] == ["Seite 1"] and meta["method"] == "ocr" and batch.source_format == "pdf_scan"
    assert ReviewCase.objects.filter(import_row__in=low, status="open").count() == 2


@pytest.mark.slow
@pytest.mark.skipif(shutil.which("tesseract") is None, reason="Tesseract fehlt")
def test_scan_mit_echter_ocr(tmp_path):
    import pypdfium2 as pdfium

    pdf = tmp_path / "liste.pdf"
    pdf_with_grid(pdf, owner_rows(8))
    page = pdfium.PdfDocument(str(pdf))[0].render(scale=300 / 72).to_pil()
    img_pdf = tmp_path / "liste_scan.pdf"
    page.convert("RGB").save(img_pdf, "PDF", resolution=300)
    assert not pdf_tables.has_text_layer(img_pdf)
    result = pdf_tables.extract_scan(img_pdf)
    joined = " ".join(" ".join(r) for r in result.rows).lower()
    assert "einheit" in joined and "mustermann" in joined
    assert sum(1 for r in result.rows if r and r[0].upper().startswith("WE")) >= 4
    assert result.confidence and all(c is None or 0 <= c <= 100 for row in result.confidence for c in row)


def test_mieterliste_erzeugt_leases_und_rollen(objekt, admin_user):
    objekt.management_type = "rental"
    objekt.save()
    data = (FIX / "mieterliste_generic.csv").read_bytes()
    batch, _ = services.create_batch(
        objekt, filename="mieterliste_generic.csv", data=data, user=admin_user, import_kind="tenant_list"
    )
    services.parse_batch(batch)
    services.normalize_batch(batch, user=admin_user)
    batch.refresh_from_db()
    rows = list(ImportRow.objects.filter(batch=batch).order_by("row_no", "sub_index"))
    assert [(r.row_no, r.sub_index, r.parsed_fields.get("role")) for r in rows] == [
        (2, 0, "tenant"),
        (2, 1, "co_tenant"),
        (3, 0, "tenant"),
        (4, 0, "tenant"),
    ]
    assert all(r.target_entity_type == "tenant_unit_assignment" for r in rows)
    result = services.commit_rows(batch, {r.pk: Decision("accept") for r in rows}, user=admin_user)
    batch.refresh_from_db()
    assert batch.status == "committed" and result.get("committed") == 4
    assert Lease.objects.filter(object=objekt).count() == 3  # ein Mietverhaeltnis je Quellzeile
    l1 = Lease.objects.get(object=objekt, start_date=date(2022, 3, 1))
    assert l1.base_rent == Decimal("650.00") and l1.utilities_prepayment == Decimal("120.00")
    assert l1.heating_prepayment == Decimal("80.00") and l1.deposit_amount == Decimal("1950.00")
    assert l1.deposit_type == "savings_book" and l1.persons_count == 2 and l1.end_date is None
    assert sorted(TenantUnitAssignment.objects.filter(lease=l1).values_list("role", flat=True)) == [
        "co_tenant",
        "tenant",
    ]
    l2 = Lease.objects.get(object=objekt, start_date=date(2019, 7, 15))
    assert l2.end_date == date(2025, 12, 31) and l2.deposit_type == "bank_guarantee"
    assert l2.base_rent == Decimal("1200.00")
    assert Lease.objects.get(object=objekt, start_date=date(2024, 10, 1)).deposit_type == "cash_account"


def test_domus_profil_nur_manuell(tmp_path):
    csv_path = tmp_path / "export.csv"
    csv_path.write_text("Einheit;Eigentümer\nWE01;Mustermann, Max\n", encoding="utf-8")
    profile, scores = choose_profile(csv_path, csv_path.name)
    assert profile.code == "csv_generic" and scores["domus_export"] == 0.0
    table = PROFILES["domus_export"].extract(csv_path)
    assert table.rows[0] == ["Einheit", "Eigentümer"] and PROFILES["domus_export"].source_format == "domus"


def test_listenerkennung_aus_pipeline_und_import_nach_bestaetigung(objekt, admin_user, tmp_path):
    from apps.audit.models import AuditEvent
    from apps.documents.ingest import ingest_upload
    from apps.pipeline.local import run_pending_jobs
    from apps.pipeline.models import JobType
    from apps.review import services as review_services

    pdf = tmp_path / "Eigentuemerliste_2026.pdf"
    pdf_with_grid(pdf, owner_rows(20))
    doc, _run = ingest_upload(objekt, filename=pdf.name, data=pdf.read_bytes(), user=admin_user)
    run_pending_jobs(objekt, job_types=[JobType.HASH, JobType.ANALYZE_PAGES])
    case = ReviewCase.objects.get(document=doc, case_type="import_candidate")
    assert case.case_subtype == "owner_list" and case.context["profile"] == "pdf_digital_table"
    assert case.context["import_kind"] == "owner_list" and case.context["rows"] == 21
    assert {"unit_label", "owner_name_raw"} <= set(case.context["targets"])
    batch = review_services.start_import(case, admin_user, import_kind="owner_list")
    case.refresh_from_db()
    batch.refresh_from_db()
    doc.refresh_from_db()
    assert case.status == "resolved" and case.resolution["import_batch_id"] == batch.pk
    assert batch.parser_profile == "pdf_digital_table" and batch.status == "parsed"
    assert batch.rows_total == 20 and batch.source_sha256 == doc.sha256 and batch.import_kind == "owner_list"
    assert AuditEvent.objects.filter(action="review.import_started", entity_id=case.pk).exists()
    with pytest.raises(review_services.ReviewError):
        review_services.start_import(case, admin_user)
