"""Latenzsonde mit dem Django-Testclient (Entwicklungsumgebung, ein Prozess): Liste, Detail, Suche, Vorschaubild.
Zielwert p95 unter 2 s (ANNAHME A-29); die Messung mit fuenf gleichzeitigen Nutzern gegen den Test-Compose folgt in
M13 mit tests/performance/latency_probe.py."""

from __future__ import annotations

import pytest
from django.urls import reverse
from latency_probe import measure

from .helpers import H623, make_case_document

pytestmark = [pytest.mark.django_db, pytest.mark.slow]


def test_latenz_review_center(welt, fake_oauth, run_all, client_as, admin_user):
    for i in range(30):
        make_case_document(
            welt,
            run_all,
            {
                "filename": f"unklar_{i}.pdf",
                "pages": [H623 + f"Schreiben Nummer {i} ohne erkennbaren Bezug. Mit freundlichen Grüßen"],
            },
        )
    doc, case = make_case_document(
        welt,
        run_all,
        {
            "filename": "Eigentuemerkonto_WE05.pdf",
            "pages": [H623 + "Kontoauszug Eigentümerkonto WE05\nSaldo 0,00 EUR"],
        },
    )
    client = client_as(admin_user)
    endpoints = {
        "liste": reverse("review_list"),
        "detail": reverse("review_detail", args=[case.pk]),
        "suche": reverse("review_owner_search", args=[welt["objects"]["623"].pk]) + "?q=mu",
        "einheiten": reverse("review_units", args=[welt["objects"]["623"].pk]),
    }
    report = measure(lambda name, url: client.get(url).status_code, endpoints, users=1, rounds=5)
    assert report["_summary"]["ok"], report
    assert all(r["n"] == 5 for k, r in report.items() if not k.startswith("_"))
