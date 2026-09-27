"""Upload aus dem CRM (Ergaenzung M29, 26.09.2026): Schalter, Scope, Pruefung von Kennung und Hinweisen,
Idempotenz, Statusabfrage und 405 fuer andere Methoden. Die Verarbeitung selbst ist durch
tests/integration/pipeline/test_crm_upload_ablage.py abgedeckt; hier wird der Lauf nur angelegt."""

from __future__ import annotations

import json
from io import StringIO

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.config import store
from apps.crm_api.models import CrmUpload
from apps.documents.models import Document
from apps.objects.models import ManagedObject

pytestmark = pytest.mark.django_db

BASE = "/api/crm/v1"


def make_token(*scopes: str) -> str:
    out = StringIO()
    call_command("crm_token", "anlegen", "--name", "crm-test", "--scopes", *scopes, stdout=out)
    return out.getvalue().strip().splitlines()[-1]


@pytest.fixture
def objekt(seeded):
    return ManagedObject.objects.create(
        object_number="523", name="Musterstadt, Musterstraße 49", management_type="weg", status="active"
    )


@pytest.fixture
def an(admin_user):
    store.set("sync.crm_uploads_enabled", True, user=admin_user)


@pytest.fixture
def no_run(monkeypatch):
    """Keine Verarbeitung anstossen; die Tests pruefen nur die Annahme."""
    calls = []
    monkeypatch.setattr("apps.documents.ingest.ensure_run", lambda obj, **kw: calls.append((obj.pk, kw)))
    return calls


def upload(client, token, number="523", *, crm_id="01a0df87-c032-7614", name="Rechnung.pdf", hints=None):
    body = {"file": SimpleUploadedFile(name, b"%PDF-1.4 Testinhalt", content_type="application/pdf")}
    if crm_id is not None:
        body["crm_document_id"] = crm_id
    if hints is not None:
        body["hints"] = hints if isinstance(hints, str) else json.dumps(hints)
    return client.post(f"{BASE}/objects/{number}/documents/", body, HTTP_AUTHORIZATION=f"Bearer {token}")


def test_schalter_aus_503(client, objekt, no_run):
    resp = upload(client, make_token("documents:write"))
    assert resp.status_code == 503 and resp["Retry-After"] == "900"
    assert not Document.objects.exists() and no_run == []


def test_scope_und_token(client, objekt, an, no_run):
    assert upload(client, "oak_falsch").status_code == 401
    assert upload(client, make_token("documents:read")).status_code == 403
    assert not Document.objects.exists()


def test_upload_legt_dokument_herkunft_und_lauf_an(client, objekt, an, no_run):
    hints = {"unit_labels": ["WE 1", " "], "ticket_number": "TNR#412", "tenant_refs": ["c-7"]}
    resp = upload(client, make_token("documents:write"), hints=hints)
    assert resp.status_code == 202, resp.content
    body = resp.json()
    doc = Document.objects.get()
    assert body["id"] == doc.pk and body["crm_document_id"] == "01a0df87-c032-7614"
    assert body["status"] == "registered" and body["object_number"] == "523"
    assert body["paperless_id"] is None and body["duplicate_of"] is None
    assert resp["Location"] == f"/api/crm/v1/documents/{doc.pk}/"
    assert doc.source == "upload" and doc.original_name == "Rechnung.pdf"
    up = CrmUpload.objects.get()
    assert up.document_id == doc.pk
    assert up.hints == {"unit_labels": ["WE 1"], "tenant_refs": ["c-7"], "ticket_number": "TNR#412"}
    assert no_run == [(objekt.pk, {"documents": [doc]})]
    event = AuditEvent.objects.get(action="crm_api.upload")
    assert event.after_state["hint_keys"] == ["tenant_refs", "ticket_number", "unit_labels"]


def test_wiederholung_ist_idempotent(client, objekt, an, no_run):
    token = make_token("documents:write")
    first = upload(client, token)
    again = upload(client, token)
    assert first.status_code == 202 and again.status_code == 200
    assert again.json()["id"] == first.json()["id"]
    assert Document.objects.count() == 1 and len(no_run) == 1


def test_gleiche_kennung_anderes_objekt_409(client, objekt, an, no_run):
    ManagedObject.objects.create(object_number="524", name="Nachbarhaus", management_type="weg")
    token = make_token("documents:write")
    assert upload(client, token).status_code == 202
    assert upload(client, token, number="524").status_code == 409


@pytest.mark.parametrize(
    ("kwargs", "status"),
    [
        ({"crm_id": None}, 400),
        ({"crm_id": "mit leerzeichen"}, 400),
        ({"hints": "{kein json"}, 400),
        ({"hints": ["liste"]}, 400),
        ({"hints": {"name": "Erika Mustermann"}}, 400),
        ({"hints": {"unit_labels": "WE 1"}}, 400),
        ({"name": "programm.exe"}, 400),
        ({"number": "999"}, 404),
    ],
)
def test_ungueltige_anfragen(client, objekt, an, no_run, kwargs, status):
    assert upload(client, make_token("documents:write"), **kwargs).status_code == status
    assert not Document.objects.exists()


def test_datei_fehlt(client, objekt, an, no_run):
    resp = client.post(
        f"{BASE}/objects/523/documents/",
        {"crm_document_id": "x1"},
        HTTP_AUTHORIZATION=f"Bearer {make_token('documents:write')}",
    )
    assert resp.status_code == 400


def test_archiviertes_objekt_409(client, objekt, an, no_run):
    objekt.deleted_at, objekt.status = timezone.now(), "archived"
    objekt.save()
    assert upload(client, make_token("documents:write")).status_code == 409


def test_andere_methoden_405(client, objekt):
    token = make_token("documents:read", "documents:write")
    for method in ("put", "patch", "delete"):
        resp = getattr(client, method)(f"{BASE}/objects/523/documents/", HTTP_AUTHORIZATION=f"Bearer {token}")
        assert resp.status_code == 405 and resp["Allow"] == "GET, POST", method
    resp = client.post(f"{BASE}/documents/1/", HTTP_AUTHORIZATION=f"Bearer {token}")
    assert resp.status_code == 405 and resp["Allow"] == "GET"


def test_statusabfrage(client, objekt, an, no_run):
    token = make_token("documents:write", "documents:read")
    doc_id = upload(client, token).json()["id"]
    resp = client.get(f"{BASE}/documents/{doc_id}/", HTTP_AUTHORIZATION=f"Bearer {token}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["crm_document_id"] == "01a0df87-c032-7614"
    assert body["open_review_cases"] == 0 and body["deleted"] is False
    assert (
        client.get(f"{BASE}/documents/{doc_id + 99}/", HTTP_AUTHORIZATION=f"Bearer {token}").status_code
        == 404
    )
    nur_schreiben = make_token("documents:write")
    assert (
        client.get(f"{BASE}/documents/{doc_id}/", HTTP_AUTHORIZATION=f"Bearer {nur_schreiben}").status_code
        == 403
    )


def test_dublette_zeigt_original(client, objekt, an, no_run):
    token = make_token("documents:write", "documents:read")
    original = Document.objects.create(
        object=objekt,
        original_name="a.pdf",
        current_name="a.pdf",
        source="upload",
        status="filed",
        sha256="a" * 64,
        drive_file_id="drive-1",
        size_bytes=10,
        first_seen_at=timezone.now(),
    )
    doc_id = upload(client, token).json()["id"]
    Document.objects.filter(pk=doc_id).update(status="duplicate", duplicate_of=original)
    body = client.get(f"{BASE}/documents/{doc_id}/", HTTP_AUTHORIZATION=f"Bearer {token}").json()
    assert body["status"] == "duplicate"
    assert body["duplicate_of"]["id"] == original.pk and body["duplicate_of"]["drive_file_id"] == "drive-1"


def test_einheit_aus_hinweis_in_der_zuordnung(objekt):
    """Ohne erkannte Einheit im Text gilt die im CRM gewaehlte Einheit, wenn das Objekt sie fuehrt."""
    from apps.classification.context import build_context
    from apps.objects.models import Unit

    we1 = Unit.objects.create(
        object=objekt, unit_label="WE 1", unit_label_normalized="WE1", unit_number="1", unit_type="apartment"
    )
    doc = Document.objects.create(
        object=objekt,
        original_name="scan.pdf",
        current_name="scan.pdf",
        source="upload",
        status="ocr_done",
        size_bytes=10,
        first_seen_at=timezone.now(),
    )
    CrmUpload.objects.create(
        document=doc, crm_document_id="crm-hint-1", hints={"unit_labels": ["we 1", "WE 99"]}
    )
    ctx = build_context(doc, entities=[])
    assert ctx.unit_ids == [we1.pk]
    assert ctx.unit_labels == ["WE 1"]
    ohne = Document.objects.create(
        object=objekt,
        original_name="b.pdf",
        current_name="b.pdf",
        source="upload",
        status="ocr_done",
        size_bytes=10,
        first_seen_at=timezone.now(),
    )
    assert build_context(ohne, entities=[]).unit_ids == []


# ---------------------------------------------------------------- Personenliste als Import (27.09.2026)
EINHEITENLISTE = (
    "Objekt-Nr;Status;Objekt;Verwaltungsart;Gebaeude;VE-Nr;VE-Beschreibung;Lage;Eigentuemer;Hausgeld_EUR_mtl;"
    "Mieter;Miete_EUR_mtl\n523;aktiv;Musterstraße 49;WEG;Haus;1;WE 1;EG links;Erika Mustermann;;Max Beispiel;\n"
)


def importieren(client, token, number="523", name="einheitenliste.csv", body=EINHEITENLISTE):
    upload = SimpleUploadedFile(name, body.encode("utf-8"), content_type="text/csv")
    return client.post(
        f"{BASE}/objects/{number}/imports/", {"file": upload}, HTTP_AUTHORIZATION=f"Bearer {token}"
    )


def test_personenliste_schalter_scope_und_import(client, objekt, admin_user, monkeypatch):
    from apps.imports.models import ImportBatch

    started = []
    monkeypatch.setattr("apps.crm_api.views.parse_batch_task.delay", lambda pk: started.append(pk))
    token = make_token("persons:write")
    assert importieren(client, token).status_code == 503
    store.set("sync.crm_persons_enabled", True, user=admin_user)
    assert importieren(client, make_token("persons:read")).status_code == 403
    assert importieren(client, token, name="liste.pdf").status_code == 400
    assert importieren(client, token, number="999").status_code == 404
    first = importieren(client, token)
    assert first.status_code == 202, first.content
    body = first.json()
    batch = ImportBatch.objects.get(pk=body["batch_id"])
    assert batch.object_id == objekt.pk
    assert batch.import_kind == "mixed"
    assert body["review_path"] == f"/importe/{batch.pk}/"
    assert started == [batch.pk]
    again = importieren(client, token)
    assert again.status_code == 200
    assert again.json()["batch_id"] == batch.pk
    assert started == [batch.pk]
    assert AuditEvent.objects.filter(action="crm_api.person_import").count() == 2


def test_personenliste_wird_als_immoware24_liste_eingelesen(client, objekt, admin_user):
    from apps.imports import services as import_services
    from apps.imports.models import ImportBatch

    store.set("sync.crm_persons_enabled", True, user=admin_user)
    resp = importieren(client, make_token("persons:write"))
    batch = ImportBatch.objects.get(pk=resp.json()["batch_id"])
    import_services.parse_batch(batch)
    batch.refresh_from_db()
    assert batch.rows_total == 1
    assert "immoware24" in (batch.parser_profile or "")
