"""Sammelaktion je Objekt und Unterart (faelle_sammelaktion, 26.09.2026): Vorschau ohne Wirkung, Ausfuehrung mit
Wirkung ueber die Dienste des Pruefcenters, Mindestkonfidenz greift, Idempotenz (zweiter Lauf findet nichts),
Objektfilter und Begrenzung, kein Drive-Aufruf im Kommando (Ablage als Job)."""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from tests.integration.classification.conftest import make_document

from apps.audit.models import AuditEvent
from apps.documents.models import DocumentSubfolder
from apps.pipeline.models import JobType, ProcessingJob
from apps.review.models import CaseStatus, ReviewCase, ReviewDecision

from .helpers import H623

pytestmark = pytest.mark.django_db

TEILUNG = {"category": "02", "subfolder": "05", "document_type": "teilungserklaerung"}


def _doc(obj, name: str, *, sub: str = "01"):
    doc = make_document(obj, {"filename": name, "pages": [H623 + "Text des Dokuments, Inhalt ohne Belang."]})
    doc.category_id = "06"
    doc.subfolder = DocumentSubfolder.objects.get(category_id="06", code=sub)
    doc.status = "review"
    doc.save(update_fields=["category", "subfolder", "status", "updated_at"])
    return doc


def _fall(
    obj,
    doc,
    *,
    subtype: str,
    case_type: str = "unclear",
    intended: dict | None = None,
    confidence: float | None = None,
    misc: str = "01",
):
    candidates = (
        [{"stage": 1, **intended, "confidence": confidence, "rule": "R-02-TEST-001"}]
        if intended and confidence is not None
        else None
    )
    return ReviewCase.objects.create(
        object=obj,
        case_type=case_type,
        case_subtype=subtype,
        document=doc,
        misc_subfolder=DocumentSubfolder.objects.get(category_id="06", code=misc),
        candidates=candidates,
        context={"reason": "Test", "confidence": confidence, "intended": intended},
        batch_key=f"grp:{obj.pk}:test",
    )


def _run(*args) -> str:
    out = StringIO()
    call_command("faelle_sammelaktion", *args, stdout=out)
    return out.getvalue()


@pytest.fixture
def kandidaten(welt, fake_oauth):
    obj = welt["objects"]["623"]
    hoch = _fall(obj, _doc(obj, "hoch.pdf"), subtype="below_threshold", intended=TEILUNG, confidence=0.9)
    tief = _fall(
        obj,
        _doc(obj, "tief.pdf"),
        subtype="below_threshold",
        case_type="move_proposal",
        intended=TEILUNG,
        confidence=0.5,
    )
    ohne = _fall(obj, _doc(obj, "ohne.pdf"), subtype="below_threshold")
    return obj, hoch, tief, ohne


def test_kandidat_bestaetigen_vorschau_ohne_wirkung(kandidaten, welt):
    obj, hoch, tief, ohne = kandidaten
    ops_before = len(welt["drive"].ops)
    out = _run("--aktion", "kandidat-bestaetigen", "--min-konfidenz", "0.7")
    assert "Ausgewaehlt: 1 | ausfuehrbar: 1" in out
    assert "Objekt 623: 1" in out and "Unterart below_threshold: 1" in out and "Ziel 02: 1" in out
    assert "Uebersprungen (unter Mindestkonfidenz): 1" in out
    assert "Uebersprungen (ohne Kandidat 01 bis 05): 1" in out
    assert "Vorschau, nichts veraendert" in out
    assert ReviewDecision.objects.count() == 0
    assert ReviewCase.objects.filter(status=CaseStatus.OPEN).count() == 3
    assert not ProcessingJob.objects.filter(job_type=JobType.FILE_TO_DRIVE).exists()
    assert not AuditEvent.objects.filter(action__in=["review.bulk_command", "review.confirm"]).exists()
    assert len(welt["drive"].ops) == ops_before


def test_kandidat_bestaetigen_echt_schwelle_idempotenz_und_ablage(
    kandidaten, welt, run_all, admin_user, django_capture_on_commit_callbacks
):
    obj, hoch, tief, ohne = kandidaten
    with pytest.raises(CommandError, match="--benutzer"):
        _run("--aktion", "kandidat-bestaetigen", "--echt")
    ops_before = len(welt["drive"].ops)
    out = _run(
        "--aktion", "kandidat-bestaetigen", "--min-konfidenz", "0.7", "--echt", "--benutzer", admin_user.email
    )
    assert "Ausgefuehrt: 1 erledigt, 0 fehlgeschlagen" in out
    assert len(welt["drive"].ops) == ops_before  # kein Drive-Aufruf im Kommando, Ablage als Job
    hoch.refresh_from_db()
    tief.refresh_from_db()
    ohne.refresh_from_db()
    assert hoch.status == CaseStatus.RESOLVED and hoch.resolution["decision"] == "confirm"
    assert hoch.resolution["target"]["category"] == "02" and hoch.resolution["target"]["subfolder"] == "05"
    assert tief.status == CaseStatus.OPEN and ohne.status == CaseStatus.OPEN
    decision = ReviewDecision.objects.get(review_case=hoch)
    assert decision.is_bulk and decision.bulk_key.startswith("cmd:kandidat-bestaetigen:")
    assert decision.system_was_correct is True and decision.decided_by_id == admin_user.pk
    doc = hoch.document
    doc.refresh_from_db()
    assert doc.category_id == "02" and doc.subfolder.code == "05"
    assert doc.document_type.code == "teilungserklaerung" and doc.status == "classified"
    assert ProcessingJob.objects.filter(document=doc, job_type=JobType.FILE_TO_DRIVE).exists()
    audit = AuditEvent.objects.get(action="review.bulk_command")
    assert audit.after_state["ok"] == 1 and audit.after_state["bulk_key"] == decision.bulk_key
    assert audit.user_id == admin_user.pk
    assert AuditEvent.objects.filter(action="review.confirm", entity_id=hoch.pk).count() == 1
    # Ablage folgt wie im Bestand: der Job legt die Datei in 02/05 ab
    with django_capture_on_commit_callbacks(execute=True):
        run_all(obj)
    doc.refresh_from_db()
    assert doc.status == "filed" and doc.drive_node is not None
    assert doc.drive_node.category_id == "02" and doc.drive_node.subfolder.code == "05"
    # Idempotenz: zweiter Lauf findet nichts mehr ueber der Schwelle
    out = _run(
        "--aktion", "kandidat-bestaetigen", "--min-konfidenz", "0.7", "--echt", "--benutzer", admin_user.email
    )
    assert "Ausgewaehlt: 0 | ausfuehrbar: 0" in out and "Ausgefuehrt: 0 erledigt" in out
    assert ReviewDecision.objects.count() == 1
    # Schwelle gesenkt: der Fall mit Konfidenz 0,5 folgt, der ohne Kandidat bleibt offen
    out = _run(
        "--aktion", "kandidat-bestaetigen", "--min-konfidenz", "0.4", "--echt", "--benutzer", admin_user.email
    )
    assert "Ausgefuehrt: 1 erledigt" in out
    tief.refresh_from_db()
    ohne.refresh_from_db()
    assert tief.status == CaseStatus.RESOLVED and ohne.status == CaseStatus.OPEN


def test_manuelle_pruefung_legt_nach_06_02_ab(
    welt, fake_oauth, run_all, admin_user, django_capture_on_commit_callbacks
):
    obj = welt["objects"]["623"]
    doc = _doc(obj, "manuell.pdf", sub="02")
    case = _fall(
        obj,
        doc,
        subtype="manual_check",
        intended={"category": "06", "subfolder": "02", "document_type": None},
        misc="02",
    )
    andere = _fall(obj, _doc(obj, "unklar.pdf"), subtype="below_threshold")
    out = _run("--aktion", "manuelle-pruefung")
    assert "Ausgewaehlt: 1 | ausfuehrbar: 1" in out and "Ziel 06: 1" in out
    assert "Vorschau, nichts veraendert" in out and ReviewDecision.objects.count() == 0
    out = _run("--aktion", "manuelle-pruefung", "--echt", "--benutzer", admin_user.email)
    assert "Ausgefuehrt: 1 erledigt, 0 fehlgeschlagen" in out
    case.refresh_from_db()
    andere.refresh_from_db()
    assert case.status == CaseStatus.RESOLVED and andere.status == CaseStatus.OPEN
    assert case.resolution["target"]["category"] == "06" and case.resolution["target"]["subfolder"] == "02"
    assert "faelle_sammelaktion" in case.resolution["target"]["reason"]
    doc.refresh_from_db()
    assert doc.category_id == "06" and doc.subfolder.code == "02" and doc.status == "classified"
    with django_capture_on_commit_callbacks(execute=True):
        run_all(obj)
    doc.refresh_from_db()
    assert (
        doc.status == "filed" and doc.drive_node.category_id == "06" and doc.drive_node.subfolder.code == "02"
    )
    out = _run("--aktion", "manuelle-pruefung", "--echt", "--benutzer", admin_user.email)
    assert "Ausgewaehlt: 0" in out and ReviewDecision.objects.count() == 1


def test_verwerfen_ai_sample_mit_audit_und_idempotenz(welt, fake_oauth, admin_user):
    obj = welt["objects"]["623"]
    probe1 = _fall(obj, _doc(obj, "probe1.pdf"), subtype="ai_sample", case_type="move_proposal")
    probe2 = _fall(obj, _doc(obj, "probe2.pdf"), subtype="ai_sample", case_type="move_proposal")
    bleibt = _fall(obj, _doc(obj, "bleibt.pdf"), subtype="below_threshold")
    out = _run("--aktion", "verwerfen")
    assert "Ausgewaehlt: 2 | ausfuehrbar: 2" in out and "Ziel verworfen: 2" in out
    assert "Vorschau, nichts veraendert" in out
    assert ReviewCase.objects.filter(status=CaseStatus.DISMISSED).count() == 0
    out = _run(
        "--aktion", "verwerfen", "--echt", "--benutzer", admin_user.email, "--grund", "Stichprobe geprüft"
    )
    assert "Ausgefuehrt: 2 erledigt, 0 fehlgeschlagen" in out
    for case in (probe1, probe2):
        case.refresh_from_db()
        assert case.status == CaseStatus.DISMISSED
        assert case.resolution["decision"] == "reject" and case.resolution["reason"] == "Stichprobe geprüft"
        assert case.resolution["bulk_key"].startswith("cmd:verwerfen:")
        decision = ReviewDecision.objects.get(review_case=case)
        assert decision.decision_type == "reject" and decision.is_bulk and decision.bulk_key
        audit = AuditEvent.objects.get(action="review.dismiss", entity_id=case.pk)
        assert audit.after_state["bulk_key"] == decision.bulk_key and audit.user_id == admin_user.pk
    bleibt.refresh_from_db()
    assert bleibt.status == CaseStatus.OPEN
    assert AuditEvent.objects.filter(action="review.bulk_command").count() == 1
    out = _run("--aktion", "verwerfen", "--echt", "--benutzer", admin_user.email)
    assert "Ausgewaehlt: 0" in out and ReviewDecision.objects.count() == 2


def test_verwerfen_unterart_frei_und_objektfall_bleibt(welt, fake_oauth, admin_user):
    obj = welt["objects"]["623"]
    mieter = _fall(obj, _doc(obj, "mieter.pdf"), subtype="tenant_unknown", misc="02")
    struktur = ReviewCase.objects.create(
        object=obj,
        case_type="drive_structure",
        case_subtype="tenant_unknown",
        document=_doc(obj, "struktur.pdf"),
        context={"reason": "Test"},
    )
    out = _run(
        "--aktion", "verwerfen", "--unterart", "tenant_unknown", "--echt", "--benutzer", admin_user.email
    )
    assert "Uebersprungen (Objektfall (nur einzeln)): 1" in out and "Ausgefuehrt: 1 erledigt" in out
    mieter.refresh_from_db()
    struktur.refresh_from_db()
    assert mieter.status == CaseStatus.DISMISSED and struktur.status == CaseStatus.OPEN
    with pytest.raises(CommandError, match="Unterarten"):
        _run("--aktion", "verwerfen", "--unterart", "move_proposal")


def test_objektfilter_und_begrenzung(welt, fake_oauth):
    o623, o624 = welt["objects"]["623"], welt["objects"]["624"]
    for i in range(2):
        _fall(o623, _doc(o623, f"a{i}.pdf"), subtype="ai_sample", case_type="move_proposal")
    _fall(o624, _doc(o624, "b.pdf"), subtype="ai_sample", case_type="move_proposal")
    out = _run("--aktion", "verwerfen", "--objekt", "624")
    assert "Ausgewaehlt: 1 | ausfuehrbar: 1" in out and "Objekt 624: 1" in out and "Objekt 623" not in out
    out = _run("--aktion", "verwerfen", "--limit", "1")
    assert "Ausgewaehlt: 1" in out and "Begrenzung 1 erreicht, 2 nicht bearbeitet" in out
    with pytest.raises(CommandError, match="Ziffern"):
        _run("--aktion", "verwerfen", "--objekt", "x1")


# ---------------------------------------------------------------- Gegenpruefung 26.09.2026 (Pruefrueckstand)
OHNE_UNTERART = {"category": "02", "subfolder": "05", "document_type": None}  # rote Zeile „Unterart wählen"


def _bulk_max(n: int, user):
    from apps.config import store

    store.set("review.bulk_max_cases", n, user=user, reason="Test")


def test_limit_zaehlt_nur_ausfuehrbare_faelle(welt, fake_oauth, admin_user):
    """Blockierte Faelle mit kleinerer id verbrauchen das Limit nicht: ein Pilot mit limit=1 erledigt einen Fall,
    der naechste Lauf den zweiten; die blockierten bleiben offen."""
    obj = welt["objects"]["623"]
    blockiert = [
        _fall(
            obj, _doc(obj, f"block{i}.pdf"), subtype="below_threshold", intended=OHNE_UNTERART, confidence=0.9
        )
        for i in range(2)
    ]
    frei = [
        _fall(obj, _doc(obj, f"frei{i}.pdf"), subtype="below_threshold", intended=TEILUNG, confidence=0.9)
        for i in range(2)
    ]
    assert max(b.pk for b in blockiert) < min(f.pk for f in frei)
    out = _run("--aktion", "kandidat-bestaetigen", "--limit", "1")
    assert "Ausgewaehlt: 3 | ausfuehrbar: 1" in out and "Blockiert (Unterart wählen): 2" in out
    assert "Begrenzung 1 erreicht, 1 nicht bearbeitet" in out and "Vorschau, nichts veraendert" in out
    out = _run("--aktion", "kandidat-bestaetigen", "--limit", "1", "--echt", "--benutzer", admin_user.email)
    assert "Ausgefuehrt: 1 erledigt, 0 fehlgeschlagen" in out
    assert ReviewCase.objects.filter(status=CaseStatus.RESOLVED).count() == 1
    out = _run("--aktion", "kandidat-bestaetigen", "--limit", "1", "--echt", "--benutzer", admin_user.email)
    assert "Ausgefuehrt: 1 erledigt, 0 fehlgeschlagen" in out and "Begrenzung" not in out
    for case in frei:
        case.refresh_from_db()
        assert case.status == CaseStatus.RESOLVED
    for case in blockiert:
        case.refresh_from_db()
        assert case.status == CaseStatus.OPEN
    assert ReviewDecision.objects.count() == 2


def test_bloecke_und_blockierte_zeilen(welt, fake_oauth, admin_user):
    """Mehr Faelle als review.bulk_max_cases: je Block ein bulk_execute mit Ausschluss der roten Zeilen; ein
    vollstaendig blockierter Block loest kein bulk_execute aus."""
    obj = welt["objects"]["623"]
    _bulk_max(2, admin_user)
    b1 = _fall(obj, _doc(obj, "b1.pdf"), subtype="below_threshold", intended=OHNE_UNTERART, confidence=0.9)
    b2 = _fall(obj, _doc(obj, "b2.pdf"), subtype="below_threshold", intended=OHNE_UNTERART, confidence=0.9)
    f1 = _fall(obj, _doc(obj, "f1.pdf"), subtype="below_threshold", intended=TEILUNG, confidence=0.9)
    f2 = _fall(obj, _doc(obj, "f2.pdf"), subtype="below_threshold", intended=TEILUNG, confidence=0.9)
    out = _run("--aktion", "kandidat-bestaetigen", "--echt", "--benutzer", admin_user.email)
    assert "Ausgewaehlt: 4 | ausfuehrbar: 2" in out and "Blockiert (Unterart wählen): 2" in out
    assert "Ausgefuehrt: 2 erledigt, 0 fehlgeschlagen" in out
    assert out.index("bulk_key cmd:kandidat-bestaetigen:") < out.index("Ausgewaehlt:")  # vor dem ersten Block
    bloecke = list(AuditEvent.objects.filter(action="review.bulk_execute").order_by("id"))
    assert len(bloecke) == 1  # Block 1 (b1, b2) vollstaendig blockiert, Block 2 (f1, f2) ausgefuehrt
    assert sorted(bloecke[0].after_state["cases"]) == [f1.pk, f2.pk]
    for case in (f1, f2):
        case.refresh_from_db()
        assert case.status == CaseStatus.RESOLVED
    for case in (b1, b2):
        case.refresh_from_db()
        assert case.status == CaseStatus.OPEN
    # gemischte Bloecke: rote Zeile wird an bulk_execute ausgeschlossen, die gruene laeuft
    f3 = _fall(obj, _doc(obj, "f3.pdf"), subtype="below_threshold", intended=TEILUNG, confidence=0.9)
    out = _run("--aktion", "kandidat-bestaetigen", "--echt", "--benutzer", admin_user.email)
    assert "Ausgewaehlt: 3 | ausfuehrbar: 1" in out and "Ausgefuehrt: 1 erledigt, 0 fehlgeschlagen" in out
    bloecke = list(AuditEvent.objects.filter(action="review.bulk_execute").order_by("id"))
    assert len(bloecke) == 2 and bloecke[1].after_state["cases"] == [f3.pk]
    f3.refresh_from_db()
    assert f3.status == CaseStatus.RESOLVED
    assert AuditEvent.objects.filter(action="review.bulk_command").count() == 2
    audit = AuditEvent.objects.filter(action="review.bulk_command").order_by("id").first()
    assert audit.after_state["blockiert"] == {"Unterart wählen": 2} and audit.after_state["ok"] == 2


def test_benutzer_muss_aktiv_und_berechtigt_sein(welt, fake_oauth, admin_user):
    from apps.accounts.models import Role, User

    obj = welt["objects"]["623"]
    fall = _fall(obj, _doc(obj, "probe.pdf"), subtype="ai_sample", case_type="move_proposal")
    gesperrt = User.objects.create_user(
        "gesperrt@example.test", "Startpasswort-12x", role=Role.objects.get(code="admin"), display_name="G"
    )
    gesperrt.status = User.Status.DISABLED
    gesperrt.save(update_fields=["status"])
    leser = User.objects.create_user(
        "leser@example.test",
        "Startpasswort-12x",
        role=Role.objects.create(
            code="leser_test", name="Leser", permissions=["audit.read_own"], is_system=False
        ),
        display_name="L",
    )
    with pytest.raises(CommandError, match="nicht aktiv"):
        _run("--aktion", "verwerfen", "--echt", "--benutzer", gesperrt.email)
    with pytest.raises(CommandError, match="review.decide"):
        _run("--aktion", "verwerfen", "--echt", "--benutzer", leser.email)
    with pytest.raises(CommandError, match="nicht gefunden"):
        _run("--aktion", "verwerfen", "--echt", "--benutzer", "niemand@example.test")
    fall.refresh_from_db()
    assert fall.status == CaseStatus.OPEN and ReviewDecision.objects.count() == 0
    assert not AuditEvent.objects.filter(action__in=["review.bulk_command", "review.dismiss"]).exists()


def test_fehlertexte_werden_maskiert(welt, fake_oauth, admin_user, monkeypatch):
    """Ausnahmetexte aus dismiss landen maskiert und gekuerzt als Zaehlerschluessel (stdout, Audit after_state)."""
    from apps.review import sammelaktion, services

    obj = welt["objects"]["623"]
    _fall(obj, _doc(obj, "probe.pdf"), subtype="ai_sample", case_type="move_proposal")
    iban = "DE89 3704 0044 0532 0130 00"

    def _kaputt(*args, **kwargs):
        raise RuntimeError(f"Duplicate entry '{iban}' for key " + "x" * 200)

    monkeypatch.setattr(services, "dismiss", _kaputt)
    out = _run("--aktion", "verwerfen", "--echt", "--benutzer", admin_user.email)
    assert "Ausgefuehrt: 0 erledigt, 1 fehlgeschlagen" in out
    assert "0532" not in out and "Fehler (Duplicate entry" in out
    audit = AuditEvent.objects.get(action="review.bulk_command")
    (text,) = audit.after_state["fehler"].keys()
    assert "0532" not in text and len(text) <= 120
    assert sammelaktion._fehlertext(None) == ""
