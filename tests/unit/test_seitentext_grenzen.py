"""Seitentexte begrenzen und Einfuegepakete nach Bytes buendeln (23.09.2026, Fehler 1153 in Objekt 503)."""

from types import SimpleNamespace

from apps.pipeline.tasks import BULK_BYTES, MAX_PAGE_CHARS, _batches_by_bytes, _cap_page_text


def test_seitentext_wird_auf_hoechstlaenge_gekuerzt():
    kurz = "a" * 100
    assert _cap_page_text(kurz, doc_pk=1, page_no=1) == kurz
    lang = "b" * (MAX_PAGE_CHARS + 5)
    assert len(_cap_page_text(lang, doc_pk=1, page_no=5)) == MAX_PAGE_CHARS


def test_pakete_bleiben_unter_der_bytegrenze():
    rows = [SimpleNamespace(text_content="x" * 1_000_000) for _ in range(9)]
    batches = list(_batches_by_bytes(rows, limit=BULK_BYTES))
    assert sum(len(b) for b in batches) == 9
    assert [len(b) for b in batches] == [3, 3, 3]  # 4 x (1 MB + 512) laege ueber 4 MB
    einzeln = list(_batches_by_bytes([SimpleNamespace(text_content="y" * 5_000_000)], limit=BULK_BYTES))
    assert len(einzeln) == 1 and len(einzeln[0]) == 1
    leer = list(_batches_by_bytes([SimpleNamespace(text_content=None)]))
    assert len(leer) == 1 and len(leer[0]) == 1
