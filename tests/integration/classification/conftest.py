"""Fixtures fuer die Klassifikationstests: Testkatalog E 8 als Objekte, Einheiten, Eigentuemer, Mieter und Dokumente mit
Seitentexten (ohne OCR), Fake-Drive mit Objektordnern aus dem Abgleich."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from django.conf import settings
from django.utils import timezone

from apps.config import store
from apps.documents.models import Document, DocumentPage
from apps.drive.adapter import InMemoryDriveAdapter
from apps.drive.reconcile import DriveConfig, reconcile_object
from apps.objects.models import ManagedObject, Unit
from apps.objects.units import normalize_label
from apps.parties import services as party_services
from apps.parties.models import Owner, Tenant, TenantUnitAssignment

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "documents" / "testkatalog.json"


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    path = tmp_path / "data"
    path.mkdir()
    monkeypatch.setitem(settings.OBJEKTAKTE, "DATA_DIR", path)
    monkeypatch.setitem(settings.OBJEKTAKTE, "DISK_RESERVE_GB", 0)
    return path


@pytest.fixture
def katalog():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def drive(seeded):
    return InMemoryDriveAdapter()


@pytest.fixture
def fake_oauth(drive, monkeypatch):
    from apps.drive import oauth

    monkeypatch.setattr(oauth, "get_adapter", lambda: drive)
    return drive


def _date(value):
    return date.fromisoformat(value) if value else None


@pytest.fixture
def welt(seeded, data_dir, katalog, drive, admin_user):
    """Baut die Testumgebung aus dem Katalog; Rueckgabe Objekte, Eigentuemer je Schluessel, Einheiten je Objekt."""
    store.set("classification.contract_partners", katalog["contract_partners"], user=admin_user)
    objects: dict[str, ManagedObject] = {}
    owners: dict[str, Owner] = {}
    units: dict[tuple[str, str], Unit] = {}
    cfg = DriveConfig.from_settings(root_folder_id=drive.root_id)
    for o in katalog["objects"]:
        obj = ManagedObject.objects.create(
            object_number=o["object_number"],
            name=o["name"],
            city=o["city"],
            street=o["street"],
            house_number=o["house_number"],
            management_type=o["management_type"],
            is_test=True,
        )
        objects[o["object_number"]] = obj
        for u in o["units"]:
            unit = Unit.objects.create(
                object=obj,
                unit_label=u["label"],
                unit_label_normalized=normalize_label(u["label"]),
                unit_number=u.get("number"),
                unit_type=u["type"],
            )
            units[(o["object_number"], u["label"])] = unit
        for ow in o["owners"]:
            if ow.get("company"):
                owner = Owner.objects.create(
                    type="legal_entity", company_name=ow["company"], search_name=ow["company"].upper()
                )
            else:
                owner = Owner.objects.create(
                    type="natural_person",
                    first_name=ow["first"],
                    last_name=ow["last"],
                    search_name=f"{ow['last']} {ow['first']}".upper(),
                    **(party_services.iban_fields(ow["iban"]) if ow.get("iban") else {}),
                )
            owners[ow["key"]] = owner
            for label, von, bis in ow["units"]:
                party_services.create_assignment(
                    owner=owner,
                    unit=units[(o["object_number"], label)],
                    valid_from=_date(von),
                    valid_to=_date(bis),
                )
        for t in o["tenants"]:
            tenant = Tenant.objects.create(
                type="natural_person",
                first_name=t["first"],
                last_name=t["last"],
                search_name=f"{t['last']} {t['first']}".upper(),
            )
            TenantUnitAssignment.objects.create(
                tenant=tenant, unit=units[(o["object_number"], t["unit"])], valid_from=date(2024, 1, 1)
            )
        if o["units"]:
            reconcile_object(obj, drive=drive, dry_run=False, cfg=cfg)
    return {"objects": objects, "owners": owners, "units": units, "drive": drive, "cfg": cfg}


def make_document(
    obj, entry: dict, *, source: str = "upload", drive=None, parent_id: str | None = None
) -> Document:
    """Dokument mit Seitentexten im Status ocr_done (Seiten aus dem Katalog, keine OCR)."""
    import hashlib

    content = "\n\f".join(entry["pages"]).encode("utf-8")
    sha = hashlib.sha256(content + entry["filename"].encode()).hexdigest()
    drive_file_id = None
    drive_node = None
    if source == "drive_existing":
        assert drive is not None and parent_id is not None
        drive_file_id = drive.add_file(parent_id, entry["filename"], content, "application/pdf")
        from apps.drive.models import DriveNode as DriveNodeRow

        drive_node = DriveNodeRow.objects.filter(drive_file_id=parent_id).first()
    from apps.parties.services import _hmac_key
    from apps.pipeline import ocr as ocr_mod
    from apps.pipeline import storage

    work = storage.work_dir(sha)
    work.mkdir(parents=True, exist_ok=True)
    (work / "original.pdf").write_bytes(content)
    masked_pages = []
    for i, text in enumerate(entry["pages"]):
        ocr_mod.mask_and_cache(sha, i + 1, text, source="text_layer", hmac_key=_hmac_key())
        masked_pages.append(storage.read_page(sha, i + 1)[0])
    doc = Document.objects.create(
        object=obj,
        sha256=sha,
        size_bytes=len(content),
        mime_type="application/pdf",
        original_name=entry["filename"],
        current_name=entry["filename"],
        source=source,
        source_path=None,
        drive_file_id=drive_file_id,
        drive_node=drive_node,
        page_count=len(entry["pages"]),
        origin_kind="digital",
        ocr_cache_key=sha,
        status="ocr_done",
        first_seen_at=timezone.now(),
    )
    DocumentPage.objects.bulk_create(
        [
            DocumentPage(
                document=doc,
                page_no=i + 1,
                text_source="text_layer",
                is_scan=False,
                text_content=text,
                text_hash=hashlib.sha256(text.encode()).hexdigest(),
                char_count=len(text),
                word_count=len(text.split()),
            )
            for i, text in enumerate(masked_pages)
        ]
    )
    return doc


@pytest.fixture
def run_all():
    from apps.pipeline.local import run_pending_jobs

    return run_pending_jobs
