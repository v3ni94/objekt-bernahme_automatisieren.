"""Lesende CRM-Schnittstelle (Schnittstellenvertrag M29 Stufe 3): Token und Scopes, Paginierung, 404, Maskierung
der Personendaten, Datensaetze der Endpunkte 1 bis 5 und das Kommando crm_token. Alle Namen, Anschriften und die
IBAN sind erfundene Testwerte (die IBAN ist die veroeffentlichte Beispiel-IBAN)."""

from __future__ import annotations

from datetime import date, timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.crm_api.auth import hash_token
from apps.crm_api.models import SCOPES, CrmApiToken
from apps.documents.models import Document
from apps.drive.models import DriveNode, NodeKind
from apps.objects.models import ManagedObject, Unit
from apps.parties import services as party_services
from apps.parties.models import Lease, Owner, Tenant, TenantUnitAssignment
from apps.requirements import engine
from apps.review.models import ReviewCase

pytestmark = pytest.mark.django_db

BASE = "/api/crm/v1"
TEST_IBAN = "DE89 3704 0044 0532 0130 00"  # veroeffentlichte Beispiel-IBAN, kein reales Konto
OWNER_EMAIL = "erika.mustermann@beispiel.example"


def make_token(*scopes: str) -> str:
    out = StringIO()
    call_command("crm_token", "anlegen", "--name", "crm-test", "--scopes", *(scopes or SCOPES), stdout=out)
    return out.getvalue().strip().splitlines()[-1]


def get(client, path: str, token: str | None = None, **params):
    headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
    return client.get(f"{BASE}{path}", params, **headers)


@pytest.fixture
def objekt(seeded):
    return ManagedObject.objects.create(
        object_number="523",
        name="Musterstadt, Musterstraße 49",
        street="Musterstraße",
        house_number="49",
        postal_code="12345",
        city="Musterstadt",
        management_type="weg",
        status="takeover",
        drive_root_folder_id="root-523-alt",
    )


@pytest.fixture
def bestand(objekt):
    we1 = Unit.objects.create(
        object=objekt, unit_label="WE 1", unit_label_normalized="WE1", unit_number="1", unit_type="apartment"
    )
    we2 = Unit.objects.create(
        object=objekt, unit_label="WE 2", unit_label_normalized="WE2", unit_number="2", unit_type="apartment"
    )
    owner = Owner.objects.create(
        type="natural_person",
        first_name="Erika",
        last_name="Mustermann",
        search_name="MUSTERMANN ERIKA",
        email=OWNER_EMAIL,
        **party_services.iban_fields(TEST_IBAN),
    )
    party_services.create_assignment(owner=owner, unit=we1, valid_from=date(2020, 1, 1), valid_to=None)
    party_services.create_assignment(owner=owner, unit=we2, valid_from=date(2021, 1, 1), valid_to=None)
    frueher = Owner.objects.create(
        type="natural_person", first_name="Max", last_name="Beispiel", search_name="BEISPIEL MAX"
    )
    party_services.create_assignment(
        owner=frueher, unit=we1, valid_from=date(2010, 1, 1), valid_to=date(2019, 12, 31)
    )
    tenant = Tenant.objects.create(
        type="natural_person",
        first_name="Tim",
        last_name="Mieter",
        search_name="MIETER TIM",
        email="tim@mieter.example",
    )
    lease = Lease.objects.create(object=objekt, start_date=date(2022, 4, 1))
    TenantUnitAssignment.objects.create(
        tenant=tenant, unit=we2, lease=lease, valid_from=date(2022, 4, 1), role="tenant"
    )
    buerge = Tenant.objects.create(
        type="natural_person", first_name="Bea", last_name="Buerge", search_name="BUERGE BEA"
    )
    TenantUnitAssignment.objects.create(tenant=buerge, unit=we2, lease=lease, role="guarantor")
    DriveNode.objects.create(
        object=objekt, node_kind=NodeKind.OBJECT_ROOT, drive_file_id="root-523", drive_name="523 Musterstadt"
    )
    ReviewCase.objects.create(object=objekt, case_type="owner_candidates", case_subtype="mehrdeutig")
    ReviewCase.objects.create(object=objekt, case_type="unclear")
    ReviewCase.objects.create(object=objekt, case_type="unclear", status="resolved")
    now = timezone.now()
    common = {"object": objekt, "mime_type": "application/pdf", "source": "upload", "first_seen_at": now}
    abgelegt = Document.objects.create(
        **common,
        sha256="a" * 64,
        size_bytes=1234,
        original_name="Teilungserklaerung.pdf",
        current_name="2020-01-01_Teilungserklaerung.pdf",
        status="filed",
        category_id="02",
        drive_file_id="file-1",
        filed_at=now,
    )
    beleg = Document.objects.create(
        **common,
        sha256="b" * 64,
        size_bytes=99,
        original_name="Rechnung.pdf",
        current_name="Rechnung.pdf",
        status="classified",
        category_id="03",
    )
    Document.objects.create(  # ohne Hash: erscheint nicht
        **common, size_bytes=1, original_name="neu.pdf", current_name="neu.pdf", status="registered"
    )
    engine.evaluate_object(objekt)
    return {"we1": we1, "we2": we2, "owner": owner, "tenant": tenant, "abgelegt": abgelegt, "beleg": beleg}


# ---------------------------------------------------------------- Auth und Scopes
def test_ohne_oder_mit_falschem_token_401(client, objekt):
    resp = get(client, "/objects/")
    assert resp.status_code == 401 and resp["WWW-Authenticate"].startswith("Bearer")
    assert resp.json() == {"error": "nicht autorisiert"}
    assert get(client, "/objects/", "oak_falsch").status_code == 401
    resp = client.get(f"{BASE}/objects/", HTTP_AUTHORIZATION="Basic abc")
    assert resp.status_code == 401
    assert AuditEvent.objects.filter(action="auth.denied", entity_type="crm_api").count() == 3


def test_gueltiges_token_und_gesperrtes_token(client, objekt):
    raw = make_token()
    token = CrmApiToken.objects.get()
    assert token.token_hash == hash_token(raw) and raw not in str(token.__dict__)
    assert token.last_used_at is None
    resp = get(client, "/objects/", raw)
    assert resp.status_code == 200 and resp["Content-Type"] == "application/json; charset=utf-8"
    token.refresh_from_db()
    assert token.last_used_at is not None
    call_command("crm_token", "sperren", "--id", str(token.pk), stdout=StringIO())
    assert get(client, "/objects/", raw).status_code == 401


def test_fehlender_scope_403(client, objekt):
    raw = make_token("objects:read")
    assert get(client, "/objects/523/", raw).status_code == 200
    for path in ("/objects/523/documents/", "/objects/523/owners/", "/objects/523/tenants/"):
        resp = get(client, path, raw)
        assert resp.status_code == 403, path
        assert "Scope" in resp.json()["error"]
    nur_personen = make_token("persons:read")
    assert get(client, "/objects/", nur_personen).status_code == 403
    assert get(client, "/objects/523/owners/", nur_personen).status_code == 200


def test_nur_lesend(client, objekt):
    raw = make_token()
    for method in ("post", "put", "patch", "delete"):
        resp = getattr(client, method)(f"{BASE}/objects/523/", HTTP_AUTHORIZATION=f"Bearer {raw}")
        assert resp.status_code == 405 and resp["Allow"] == "GET", method


# ---------------------------------------------------------------- Paginierung und 404
def test_paginierung(client, seeded):
    for n in ("101", "102", "103"):
        ManagedObject.objects.create(object_number=n, name=f"Objekt {n}", management_type="weg")
    ManagedObject.objects.create(object_number="900", name="Test", management_type="weg", is_test=True)
    raw = make_token("objects:read")
    body = get(client, "/objects/", raw, page_size=2).json()
    assert body["count"] == 3 and body["page"] == 1 and body["page_size"] == 2
    assert [r["number"] for r in body["results"]] == ["101", "102"]
    body = get(client, "/objects/", raw, page=2, page_size=2).json()
    assert [r["number"] for r in body["results"]] == ["103"]
    assert get(client, "/objects/", raw, page=5).json()["results"] == []
    body = get(client, "/objects/", raw, page_size=10000).json()
    assert body["page_size"] == 500 and body["count"] == 3
    assert get(client, "/objects/", raw).json()["page_size"] == 100
    assert get(client, "/objects/", raw, page="x").status_code == 400
    assert get(client, "/objects/", raw, page_size=0).status_code == 400


def test_unbekannte_nummer_404(client, objekt):
    raw = make_token()
    for path in ("/objects/999/", "/objects/999/documents/", "/objects/abc/owners/", "/objects/999/tenants/"):
        resp = get(client, path, raw)
        assert resp.status_code == 404, path
        assert resp.json() == {"error": "Objekt nicht gefunden"}
    assert get(client, "/objects/0523/", raw).json()["number"] == "523"  # Zahlenwert wie im Objektregister


def test_archiviertes_objekt_und_eingangsobjekt(client, objekt):
    alt = ManagedObject.objects.create(object_number="777", name="Alt", management_type="weg")
    alt.deleted_at, alt.status = timezone.now(), "archived"
    alt.save()
    ManagedObject.objects.create(
        object_number="1", name="Eingang", management_type="weg", is_system_inbox=True
    )
    raw = make_token("objects:read")
    rows = {r["number"]: r for r in get(client, "/objects/", raw).json()["results"]}
    assert set(rows) == {"523", "777"}
    assert rows["777"]["archived"] is True and rows["523"]["archived"] is False
    assert get(client, "/objects/777/", raw).status_code == 200


# ---------------------------------------------------------------- Datensaetze
def test_objektliste_und_detail(client, bestand):
    raw = make_token()
    row = get(client, "/objects/", raw).json()["results"][0]
    assert set(row) == {
        "number",
        "name",
        "archived",
        "takeover_status",
        "open_review_cases",
        "completeness",
        "drive_folder_id",
        "drive_folder_url",
        "updated_at",
    }
    assert row["takeover_status"] == "takeover" and row["open_review_cases"] == 2
    assert row["drive_folder_id"] == "root-523"  # aus drive_nodes vor drive_root_folder_id
    assert row["drive_folder_url"] == "https://drive.google.com/drive/folders/root-523"
    assert row["updated_at"].endswith("+00:00")
    c = row["completeness"]
    assert set(c) == {"required", "present", "missing", "percent"}
    s = engine.summary(ManagedObject.objects.get(object_number="523"))
    assert c["required"] == s["counts"]["fulfilled"] + s["counts"]["partial"] + s["counts"]["missing"]
    assert c["present"] == s["counts"]["fulfilled"] and c["required"] == c["present"] + c["missing"]
    assert c["percent"] == round(s["ratio"] * 100, 1)

    detail = get(client, "/objects/523/", raw).json()
    assert detail["address"] == {
        "street": "Musterstraße",
        "house_number": "49",
        "zip": "12345",
        "city": "Musterstadt",
    }
    assert detail["open_cases_by_type"] == {"owner_candidates/mehrdeutig": 1, "unclear/": 1}
    missing = detail["missing_documents"]
    assert missing and all(set(m) == {"category", "subfolder", "label"} for m in missing)
    assert len(missing) == len(engine.open_items(ManagedObject.objects.get(object_number="523")))
    beschluss = [m for m in missing if m["label"].startswith("Beschlusssammlung")]
    assert beschluss and beschluss[0]["category"] == "02_Stammakte" and beschluss[0]["subfolder"]
    assert "Mustermann" not in str(missing)  # keine Personennamen unter objects:read


def test_dokumentliste_mit_filtern(client, bestand):
    raw = make_token("documents:read")
    body = get(client, "/objects/523/documents/", raw).json()
    assert body["count"] == 2
    doc = body["results"][0]
    assert doc == {
        "id": bestand["abgelegt"].pk,
        "title": "2020-01-01_Teilungserklaerung.pdf",
        "doc_type": None,
        "category": "02_Stammakte",
        "subfolder": None,
        "status": "filed",
        "drive_file_id": "file-1",
        "drive_url": "https://drive.google.com/file/d/file-1/view",
        "sha256": "a" * 64,
        "filed_at": doc["filed_at"],
        "mime_type": "application/pdf",
        "size_bytes": 1234,
        "crm_document_id": None,
        "paperless_id": None,
    }
    assert doc["filed_at"].endswith("+00:00")
    assert body["results"][1]["filed_at"] is None and body["results"][1]["drive_url"] is None
    assert [d["id"] for d in get(client, "/objects/523/documents/", raw, folder="03").json()["results"]] == [
        bestand["beleg"].pk
    ]
    by_name = get(client, "/objects/523/documents/", raw, folder="02_Stammakte").json()["results"]
    assert [d["id"] for d in by_name] == [bestand["abgelegt"].pk]
    future = (timezone.now() + timedelta(days=1)).isoformat()
    assert get(client, "/objects/523/documents/", raw, since=future).json()["count"] == 0
    assert get(client, "/objects/523/documents/", raw, since="2000-01-01").json()["count"] == 2
    assert get(client, "/objects/523/documents/", raw, since="gestern").status_code == 400


def test_eigentuemer_maskiert(client, bestand):
    raw = make_token("persons:read")
    resp = get(client, "/objects/523/owners/", raw)
    body = resp.json()
    assert body["count"] == 1  # Voreigentuemer mit beendeter Zuordnung erscheint nicht
    row = body["results"][0]
    assert row["id"] == bestand["owner"].pk and row["display_name"] == "Erika Mustermann"
    assert row["unit_labels"] == ["WE 1", "WE 2"] and row["share"] is None
    assert row["email_masked"] == "e***@b***.example"
    assert row["iban_masked"] == bestand["owner"].iban_masked and row["iban_masked"].endswith("30 00")
    text = resp.content.decode("utf-8")
    compact = TEST_IBAN.replace(" ", "")
    assert compact not in text and TEST_IBAN not in text and "37040044" not in text
    assert OWNER_EMAIL not in text


def test_mieter(client, bestand):
    raw = make_token("persons:read")
    body = get(client, "/objects/523/tenants/", raw).json()
    assert body["count"] == 1  # Buerge ist kein Mieter
    assert body["results"][0] == {
        "id": bestand["tenant"].pk,
        "display_name": "Tim Mieter",
        "unit_labels": ["WE 2"],
        "lease_start": "2022-04-01",
        "lease_end": None,
        "email_masked": "t***@m***.example",
    }


def test_maskierung_email_randfaelle():
    from apps.crm_api.data import mask_email

    assert mask_email(None) is None and mask_email("") is None and mask_email("ohne-at") is None
    assert mask_email("a@localhost") == "a***@l***"
    assert mask_email("x.y@sub.beispiel.de") == "x***@s***.de"


# ---------------------------------------------------------------- Kommando crm_token
def test_kommando_anlegen_liste_sperren():
    out = StringIO()
    call_command(
        "crm_token", "anlegen", "--name", "crm", "--scopes", "objects:read,documents:read", stdout=out
    )
    raw = out.getvalue().strip().splitlines()[-1]
    assert raw.startswith("oak_") and len(raw) > 40
    token = CrmApiToken.objects.get(name="crm")
    assert token.scopes == ["objects:read", "documents:read"] and token.is_active
    assert AuditEvent.objects.filter(action="crm_token.create", entity_id=token.pk).exists()
    assert raw not in str(AuditEvent.objects.filter(action="crm_token.create").values().first())

    liste = StringIO()
    call_command("crm_token", "liste", stdout=liste)
    assert "crm" in liste.getvalue() and "aktiv" in liste.getvalue() and raw not in liste.getvalue()

    call_command("crm_token", "sperren", "--name", "crm", stdout=StringIO())
    token.refresh_from_db()
    assert not token.is_active and token.revoked_at is not None
    assert AuditEvent.objects.filter(action="crm_token.revoke", entity_id=token.pk).exists()
    with pytest.raises(CommandError):
        call_command("crm_token", "sperren", "--id", str(token.pk), stdout=StringIO())
    with pytest.raises(CommandError):
        call_command("crm_token", "anlegen", "--name", "x", "--scopes", "objects:write", stdout=StringIO())


def test_token_hashes_nicht_fuer_auswertungsrolle():
    out = StringIO()
    call_command("grants_sql", stdout=out)
    zeilen = out.getvalue().splitlines()
    assert any("`crm_api_tokens` TO 'app_rw'@'%'" in z for z in zeilen)
    assert not any("`crm_api_tokens` TO 'app_ro'" in z for z in zeilen)
