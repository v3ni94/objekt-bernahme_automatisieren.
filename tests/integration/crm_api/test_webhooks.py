"""Ausgehende CRM-Webhooks (Schnittstellenvertrag M29 Stufe 3): HMAC-Signatur ueber den rohen Body, Kopfzeilen,
Abschaltung ohne URL oder Geheimnis, Wiederholungen mit Backoff, Ausloesung bei Ablage und Uebernahmeabschluss.
Kein Netz: requests.post wird ersetzt. Das Geheimnis ist ein erkennbarer Testwert."""

from __future__ import annotations

import hashlib
import hmac
import json
from types import SimpleNamespace

import pytest
from django.conf import settings
from django.utils import timezone

from apps.crm_api import tasks, webhooks
from apps.documents.models import Document
from apps.objects.models import ManagedObject

pytestmark = pytest.mark.django_db

URL = "https://crm.example.test/api/v1/integrations/objektakte/webhook"
SECRET = "webhook-geheimnis-testwert"  # noqa: S105  Testwert, kein Geheimnis


@pytest.fixture
def aktiv(monkeypatch):
    monkeypatch.setattr(settings, "CRM_WEBHOOK_URL", URL, raising=False)
    monkeypatch.setattr(settings, "CRM_WEBHOOK_SECRET", SECRET, raising=False)


@pytest.fixture
def posts(monkeypatch):
    import requests

    calls: list[dict] = []
    answers: list[int] = []

    def fake_post(url, data=None, headers=None, timeout=None, allow_redirects=None):
        calls.append({"url": url, "data": data, "headers": headers, "timeout": timeout})
        return SimpleNamespace(status_code=answers.pop(0) if answers else 202)

    monkeypatch.setattr(requests, "post", fake_post)
    return SimpleNamespace(calls=calls, answers=answers)


@pytest.fixture
def objekt(seeded):
    return ManagedObject.objects.create(
        object_number="523", name="Musterstadt", management_type="weg", status="takeover"
    )


def _doc(objekt, **over):
    data = {
        "object": objekt,
        "sha256": "c" * 64,
        "size_bytes": 10,
        "mime_type": "application/pdf",
        "original_name": "a.pdf",
        "current_name": "a.pdf",
        "source": "upload",
        "first_seen_at": timezone.now(),
        "status": "filed",
        "category_id": "02",
        "drive_file_id": "file-9",
        "filed_at": timezone.now(),
    }
    data.update(over)
    return Document.objects.create(**data)


def test_signatur_hmac_sha256_ueber_rohen_body(aktiv, posts):
    body = webhooks.build_body("document.filed", "523", {"id": 1, "title": "Prüfbericht"})
    assert webhooks.deliver(body, "document.filed") == 202
    call = posts.calls[0]
    assert call["url"] == URL and call["data"] == body.encode("utf-8")
    expected = hmac.new(SECRET.encode(), call["data"], hashlib.sha256).hexdigest()
    assert call["headers"]["X-Objektakte-Signature"] == f"sha256={expected}"
    assert call["headers"]["X-Objektakte-Event"] == "document.filed"
    assert call["headers"]["Content-Type"].startswith("application/json")
    payload = json.loads(call["data"])
    assert set(payload) == {"event", "occurred_at", "object_number", "document"}
    assert payload["object_number"] == "523" and payload["occurred_at"].endswith("+00:00")
    assert "Prüfbericht" in call["data"].decode("utf-8")  # UTF-8, nicht als Escape
    assert webhooks.sign(b"x", "k") == "sha256=" + hmac.new(b"k", b"x", hashlib.sha256).hexdigest()


def test_abgeschaltet_ohne_url_oder_geheimnis(objekt, posts, monkeypatch, django_capture_on_commit_callbacks):
    doc = _doc(objekt)
    monkeypatch.setattr(settings, "CRM_WEBHOOK_URL", "", raising=False)
    monkeypatch.setattr(settings, "CRM_WEBHOOK_SECRET", SECRET, raising=False)
    with django_capture_on_commit_callbacks(execute=True) as cbs:
        assert webhooks.document_filed(doc) is False
        assert webhooks.object_taken_over(objekt) is False
    assert cbs == [] and posts.calls == []
    monkeypatch.setattr(settings, "CRM_WEBHOOK_URL", URL, raising=False)
    monkeypatch.setattr(settings, "CRM_WEBHOOK_SECRET", "", raising=False)
    assert webhooks.document_filed(doc) is False
    assert tasks.send_webhook.apply(args=["{}", "document.filed"]).get() == {"status": "disabled"}
    assert posts.calls == []


def test_document_filed_nach_commit(aktiv, posts, objekt, django_capture_on_commit_callbacks):
    doc = _doc(objekt)
    with django_capture_on_commit_callbacks(execute=True):
        assert webhooks.document_filed(doc) is True
    assert len(posts.calls) == 1
    payload = json.loads(posts.calls[0]["data"])
    assert payload["event"] == "document.filed" and payload["object_number"] == "523"
    assert payload["document"]["id"] == doc.pk and payload["document"]["sha256"] == "c" * 64
    assert payload["document"]["category"] == "02_Stammakte"


def test_kein_webhook_ohne_endgueltige_ablage_oder_fuer_testobjekte(aktiv, posts, objekt):
    assert webhooks.document_filed(_doc(objekt, status="review")) is False
    test_obj = ManagedObject.objects.create(object_number="901", management_type="weg", is_test=True)
    assert webhooks.document_filed(_doc(test_obj, sha256="d" * 64, drive_file_id="f-2")) is False
    assert webhooks.object_taken_over(test_obj) is False


def test_wiederholung_bei_serverfehler_und_endgueltiger_fehlschlag(aktiv, posts):
    """Ein Versuch plus drei Wiederholungen (eager ohne Wartezeit), danach endgueltig fehlgeschlagen."""
    body = webhooks.build_body("object.taken_over", "523")
    posts.answers.extend([503, 503, 503, 503])
    result = tasks.send_webhook.apply(args=[body, "object.taken_over"]).get()
    assert result["status"] == "failed" and result["retries"] == 3
    assert len(posts.calls) == 4 and all(c["data"] == body.encode() for c in posts.calls)
    assert tasks.send_webhook.max_retries == 3
    assert [tasks.backoff_seconds(n) for n in range(3)] == [30, 120, 480]


def test_erfolg_nach_wiederholung_und_kein_retry_bei_4xx(aktiv, posts):
    body = webhooks.build_body("object.taken_over", "523")
    posts.answers.extend([502, 200])
    assert tasks.send_webhook.apply(args=[body, "object.taken_over"]).get() == {
        "status": "sent",
        "http_status": 200,
    }
    assert len(posts.calls) == 2
    posts.calls.clear()
    posts.answers.extend([400])
    assert tasks.send_webhook.apply(args=[body, "object.taken_over"]).get()["status"] == "failed"
    assert len(posts.calls) == 1


def test_verbindungsfehler_wird_wiederholt(aktiv, monkeypatch):
    import requests

    def boom(*a, **k):
        raise requests.ConnectionError("kein Netz")

    monkeypatch.setattr(requests, "post", boom)
    with pytest.raises(webhooks.DeliveryError) as info:
        webhooks.deliver("{}", "document.filed")
    assert info.value.retryable


def test_ausloesefehler_blockiert_nicht(aktiv, objekt, monkeypatch):
    def kaputt(*a, **k):
        raise RuntimeError("Broker weg")

    monkeypatch.setattr(webhooks, "_enqueue", kaputt)
    assert webhooks.document_filed(_doc(objekt)) is False  # kein Fehler nach aussen
    assert webhooks.object_taken_over(objekt) is False


def test_objekt_uebernommen_beim_statuswechsel(
    aktiv, posts, objekt, client_as, clerk_user, django_capture_on_commit_callbacks
):
    from django.forms.models import model_to_dict
    from django.urls import reverse

    client = client_as(clerk_user)
    data = {
        k: v for k, v in model_to_dict(objekt).items() if v is not None and not isinstance(v, list | dict)
    }
    data.update({"status": "active", "fiscal_year_start_month": 1})
    data.pop("is_test", None)
    with django_capture_on_commit_callbacks(execute=True):
        resp = client.post(reverse("object_edit", args=[objekt.pk]), data)
    assert resp.status_code == 302, resp.content[:500]
    events = [json.loads(c["data"])["event"] for c in posts.calls]
    assert events == ["object.taken_over"]
    posts.calls.clear()
    data["name"] = "Musterstadt Nord"
    with django_capture_on_commit_callbacks(execute=True):
        client.post(reverse("object_edit", args=[objekt.pk]), data)  # bleibt aktiv: kein neues Ereignis
    assert posts.calls == []
