"""Webhook document.filed an das CRM (M29 Stufe 3) aus der echten Ablage: genau ein Ereignis je endgueltig
abgelegtem Dokument, nach dem Commit; ohne Ziel-URL keines. requests.post ist ersetzt, kein Netz."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from django.conf import settings

from apps.documents import ingest

pytestmark = pytest.mark.django_db

URL = "https://crm.example.test/api/v1/integrations/objektakte/webhook"
SECRET = "webhook-geheimnis-testwert"  # noqa: S105  Testwert, kein Geheimnis


@pytest.fixture
def posts(monkeypatch):
    import requests

    calls: list[dict] = []

    def fake_post(url, data=None, headers=None, timeout=None, allow_redirects=None):
        calls.append({"data": data, "headers": headers})
        return SimpleNamespace(status_code=202)

    monkeypatch.setattr(requests, "post", fake_post)
    return calls


def _ablage(objekt, pdf_factory, run_all, capture):
    objekt.is_test = False  # Testobjekte loesen keine Webhooks aus
    objekt.save(update_fields=["is_test", "updated_at"])
    pdf = pdf_factory(
        "Gesamtabrechnung_2025.pdf",
        [["Gesamtabrechnung 2025 der WEG Musterstadt, Abrechnungsjahr 2025", "Gesamtkosten 45.000,00 EUR"]],
    )
    doc, _run = ingest.ingest_upload(objekt, filename="Gesamtabrechnung_2025.pdf", data=pdf.read_bytes())
    with capture(execute=True):
        run_all(objekt)
    doc.refresh_from_db()
    return doc


def test_ablage_loest_document_filed_aus(
    objekt, stammdaten, pdf_factory, run_all, posts, monkeypatch, django_capture_on_commit_callbacks
):
    monkeypatch.setattr(settings, "CRM_WEBHOOK_URL", URL, raising=False)
    monkeypatch.setattr(settings, "CRM_WEBHOOK_SECRET", SECRET, raising=False)
    doc = _ablage(objekt, pdf_factory, run_all, django_capture_on_commit_callbacks)
    assert doc.status == "filed"
    events = [json.loads(c["data"]) for c in posts]
    assert [e["event"] for e in events] == ["document.filed"]
    assert events[0]["object_number"] == "623"
    assert events[0]["document"]["id"] == doc.pk
    assert events[0]["document"]["drive_file_id"] == doc.drive_file_id
    assert events[0]["document"]["sha256"] == doc.sha256
    assert posts[0]["headers"]["X-Objektakte-Signature"].startswith("sha256=")


def test_ablage_ohne_webhook_url(
    objekt, stammdaten, pdf_factory, run_all, posts, monkeypatch, django_capture_on_commit_callbacks
):
    monkeypatch.setattr(settings, "CRM_WEBHOOK_URL", "", raising=False)
    doc = _ablage(objekt, pdf_factory, run_all, django_capture_on_commit_callbacks)
    assert doc.status == "filed" and posts == []
