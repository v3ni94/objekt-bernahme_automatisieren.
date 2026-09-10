"""Seitenbilder (Fachentwurf E, docs/architektur.md 6.1 Nr. 5): JPEG mit previews.long_edge_px langer Kante unter
previews/<document_id>/<page>.jpg; Aufbewahrung previews.retention_days_after_resolve. Auslieferung nur ueber eine
rechtegepruefte Route (B-15)."""

from __future__ import annotations

from pathlib import Path

from apps.pipeline.storage import previews_dir


def render_previews(
    pdf_path: Path,
    document_id: int,
    *,
    long_edge_px: int = 1200,
    quality: int = 80,
    pages: list[int] | None = None,
    heartbeat=None,
) -> int:
    import pypdfium2 as pdfium

    target = previews_dir(document_id)
    target.mkdir(parents=True, exist_ok=True)
    pdf = pdfium.PdfDocument(str(pdf_path))
    rendered = 0
    try:
        for index in range(len(pdf)):
            page_no = index + 1
            if pages and page_no not in pages:
                continue
            out = target / f"{page_no:04d}.jpg"
            if out.exists():
                continue
            page = pdf[index]
            width, height = page.get_width(), page.get_height()
            scale = long_edge_px / max(width, height) if max(width, height) else 1.0
            bitmap = page.render(scale=scale)
            image = bitmap.to_pil().convert("RGB")
            image.save(out, "JPEG", quality=quality, optimize=True)
            page.close()
            rendered += 1
            if heartbeat:
                heartbeat(page_no)
    finally:
        pdf.close()
    return rendered


def preview_path(document_id: int, page_no: int) -> Path:
    return previews_dir(document_id) / f"{page_no:04d}.jpg"
