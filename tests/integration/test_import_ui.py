"""Importansicht Ende zu Ende mit dem Django-Testclient (gleichwertig zu Playwright fuer serverseitig gerenderte Seiten):
Upload, Spaltenzuordnung, Erkennung, 40 sichere Zeilen ueber die Vorschau bestaetigen, unsichere Zeile einzeln entscheiden,
Protokoll abrufen. Celery laeuft im Test synchron (CELERY_TASK_ALWAYS_EAGER)."""

from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.imports.models import ImportBatch, ImportRow
from apps.objects.models import ManagedObject, Unit
from apps.parties.models import Owner, OwnerUnitAssignment
from apps.review.models import ReviewCase

pytestmark = pytest.mark.django_db


def csv_40() -> bytes:
    lines = ["Einheit;Eigentümer;Straße;PLZ;Ort;Eigentumsbeginn"]
    for i in range(1, 41):
        lines.append(f"WE {i};Mustermann{i}, Erika;Musterweg {i};12345;Musterstadt;01.01.2020")
    lines.append("WE 41;Anna Maria Beispiel Zweitname;Beispielweg 1;12345;Musterstadt;")  # unsicher
    return "\n".join(lines).encode("utf-8")


def test_import_ansicht_40_zeilen(client_as, clerk_user, seeded):
    client = client_as(clerk_user)
    obj = ManagedObject.objects.create(
        object_number="625", name="Beispielstadt", management_type="weg", is_test=True
    )

    resp = client.get(reverse("import_upload", args=[obj.pk]))
    assert resp.status_code == 200
    upload = SimpleUploadedFile("eigentuemer.csv", csv_40(), content_type="text/csv")
    resp = client.post(reverse("import_upload", args=[obj.pk]), {"file": upload, "import_kind": "owner_list"})
    assert resp.status_code == 302
    batch = ImportBatch.objects.get(object=obj)
    assert batch.status == "parsed" and batch.rows_total == 41

    detail = client.get(reverse("import_batch", args=[batch.pk]))
    body = detail.content.decode()
    assert detail.status_code == 200 and "Spaltenzuordnung" in body and "Eigentümer" in body
    # Zuordnung so uebernehmen, wie vorgeschlagen (Formularwerte aus dem Vorschlag)
    data = {f"map-col_{c['source_index']}": c["target"] or "" for c in batch.column_mapping["columns"]}
    resp = client.post(reverse("import_mapping", args=[batch.pk]), data)
    assert resp.status_code == 302
    batch.refresh_from_db()
    assert batch.status == "in_review" and batch.rows_uncertain == 1
    assert ImportRow.objects.filter(batch=batch, status="parsed").count() == 40
    assert Owner.objects.count() == 0

    # Vorschau und Ausfuehrung fuer alle sicheren Zeilen
    resp = client.post(reverse("import_preview", args=[batch.pk]), {"all_parsed": "1", "action": "accept"})
    assert resp.status_code == 200 and "Vorschau: 40 Zeilen" in resp.content.decode()
    ids = list(ImportRow.objects.filter(batch=batch, status="parsed").values_list("pk", flat=True))
    resp = client.post(reverse("import_commit", args=[batch.pk]), {"row": ids, "action": "accept"})
    assert resp.status_code == 302
    batch.refresh_from_db()
    assert batch.rows_committed == 40 and batch.status == "partially_committed"
    assert Owner.objects.count() == 40 and Unit.objects.filter(object=obj).count() == 40
    assert OwnerUnitAssignment.objects.filter(data_status="confirmed").count() == 40

    # unsichere Zeile einzeln: als Gemeinschaft uebernehmen
    row = ImportRow.objects.get(batch=batch, status="uncertain")
    page = client.get(reverse("import_row", args=[row.pk]))
    assert page.status_code == 200 and "as_community" in page.content.decode()
    resp = client.post(
        reverse("import_row", args=[row.pk]), {"action": "as_community", "unit_label": "WE 41"}
    )
    assert resp.status_code == 302, resp.content.decode()[:800]
    row.refresh_from_db()
    assert row.status == "committed"
    assert Owner.objects.filter(type="community", company_name="Anna Maria Beispiel Zweitname").exists()
    batch.refresh_from_db()
    assert batch.status == "committed" and batch.rows_total == batch.rows_committed + batch.rows_rejected
    assert ReviewCase.objects.filter(batch_key=f"import:{batch.pk}", status="open").count() == 0

    proto = client.get(reverse("import_protocol", args=[batch.pk]))
    assert proto.status_code == 200 and proto["Content-Type"].startswith("application/vnd.openxmlformats")
    liste = client.get(reverse("import_list", args=[obj.pk]))
    assert liste.status_code == 200 and "eigentuemer.csv" in liste.content.decode()


def test_import_rechte(client_as, clerk_user, admin_user, seeded, client):
    obj = ManagedObject.objects.create(object_number="626", management_type="weg", is_test=True)
    from apps.accounts.models import Role

    gast = Role.objects.create(code="gast", name="Gast", permissions=["status.read"], is_system=False)
    clerk_user.role = gast
    clerk_user.save()
    c = client_as(clerk_user)
    assert c.get(reverse("import_upload", args=[obj.pk])).status_code == 403
    assert c.get(reverse("import_list", args=[obj.pk])).status_code == 200  # Lesen mit Login erlaubt
