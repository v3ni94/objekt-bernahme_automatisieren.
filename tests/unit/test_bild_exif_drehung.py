"""Bilder mit ungueltiger EXIF-Drehung (Orientation 0) werden trotzdem in PDF gewandelt (23.09.2026, 24 Scans)."""

import img2pdf
import pytest
from PIL import Image

from apps.pipeline.analysis import image_to_pdf


def _scan_mit_exif_drehung_null(pfad):
    im = Image.new("RGB", (40, 30), "white")
    exif = im.getexif()
    exif[274] = 0  # Orientation: gueltig sind 1 bis 8
    im.save(pfad, format="JPEG", exif=exif.tobytes())


def test_img2pdf_lehnt_ungueltige_exif_drehung_ohne_ifvalid_ab(tmp_path):
    p = tmp_path / "scan.jpg"
    _scan_mit_exif_drehung_null(p)
    with pytest.raises(img2pdf.ExifOrientationError):
        img2pdf.convert(str(p))


def test_image_to_pdf_ignoriert_ungueltige_exif_drehung(tmp_path):
    p = tmp_path / "scan.jpg"
    _scan_mit_exif_drehung_null(p)
    out = image_to_pdf(p, tmp_path / "scan.pdf")
    assert out.read_bytes().startswith(b"%PDF")
