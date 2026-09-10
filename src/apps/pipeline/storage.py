"""Ablageorte der Verarbeitung (Beschluss B-14): work/<sha256>/ (Download, Chunks, OCR-Zwischenstand, loeschbar),
ocr-cache/<sha256>/pages/ (maskierter Seitentext, bleibt), previews/<document_id>/ (Seitenbilder), transit/ (Uploads)."""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

from django.conf import settings


class DiskFull(Exception):
    pass


def data_dir() -> Path:
    return Path(settings.OBJEKTAKTE["DATA_DIR"])


def work_dir(sha256: str) -> Path:
    return data_dir() / "work" / sha256


def work_tmp_dir() -> Path:
    path = data_dir() / "work" / f"tmp-{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_dir(sha256: str) -> Path:
    return data_dir() / "ocr-cache" / sha256


def cache_pages_dir(sha256: str) -> Path:
    return cache_dir(sha256) / "pages"


def page_text_path(sha256: str, page_no: int) -> Path:
    return cache_pages_dir(sha256) / f"{page_no:04d}.txt"


def page_hits_path(sha256: str, page_no: int) -> Path:
    return cache_pages_dir(sha256) / f"{page_no:04d}.hits.json"


def page_meta_path(sha256: str, page_no: int) -> Path:
    return cache_pages_dir(sha256) / f"{page_no:04d}.meta.json"


def analysis_path(sha256: str) -> Path:
    return cache_dir(sha256) / "analysis.json"


def previews_dir(document_id: int) -> Path:
    return data_dir() / "previews" / str(document_id)


def upload_dir(object_id: int) -> Path:
    path = data_dir() / "transit" / str(object_id) / "incoming" / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_disk_reserve(path: Path | None = None) -> None:
    """Ingest stoppt, wenn weniger als DISK_RESERVE_GB frei sind (docs/architektur.md 10.4)."""
    target = path or data_dir()
    probe = target if target.exists() else data_dir() if data_dir().exists() else Path("/")
    free_gb = shutil.disk_usage(probe).free / 1024**3
    reserve = float(settings.OBJEKTAKTE["DISK_RESERVE_GB"])
    if free_gb < reserve:
        raise DiskFull(f"Nur {free_gb:.1f} GB frei, Reserve {reserve:.0f} GB")


def write_page(sha256: str, page_no: int, text: str, hits: list[dict], meta: dict) -> None:
    pages = cache_pages_dir(sha256)
    pages.mkdir(parents=True, exist_ok=True)
    tmp = page_text_path(sha256, page_no).with_suffix(".txt.part")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(page_text_path(sha256, page_no))
    page_hits_path(sha256, page_no).write_text(json.dumps(hits, ensure_ascii=False), encoding="utf-8")
    page_meta_path(sha256, page_no).write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")


def read_page(sha256: str, page_no: int) -> tuple[str, list[dict], dict] | None:
    p = page_text_path(sha256, page_no)
    if not p.exists():
        return None
    hits_p, meta_p = page_hits_path(sha256, page_no), page_meta_path(sha256, page_no)
    hits = json.loads(hits_p.read_text(encoding="utf-8")) if hits_p.exists() else []
    meta = json.loads(meta_p.read_text(encoding="utf-8")) if meta_p.exists() else {}
    return p.read_text(encoding="utf-8"), hits, meta


def original_path(sha256: str) -> Path | None:
    wd = work_dir(sha256)
    if not wd.exists():
        return None
    for p in sorted(wd.iterdir()):
        if p.is_file() and p.name.startswith("original"):
            return p
    return None


def dir_size(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) if path.exists() else 0
