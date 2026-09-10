"""Testkatalog E 8 (CR 14): 32 synthetische Dokumente laufen als Uploads durch extract_entities, classify, decide und
file_to_drive gegen den Fake-Drive. Prueft Kategorie, Unterordner, Unterart, Fallart, physische Ablage, Eigentuemerakten,
Segmente (T03, T10), T20 (historische Zuordnung), Pruefabfrage "06 ohne Fall" leer und KPI je Lauf."""

from __future__ import annotations

import pytest

from apps.documents.models import Document, DocumentClassification, DocumentOwnerLink, DocumentTenantLink
from apps.parties.models import OwnerFile
from apps.pipeline.models import ProcessingRun, RunStatus
from apps.pipeline.runs import start_run
from apps.review.models import ReviewCase

from .conftest import make_document

pytestmark = pytest.mark.django_db


def _owner_of(link: DocumentOwnerLink) -> str:
    return link.owner.last_name if link.owner else ""


def test_katalog_vollstaendig(welt, katalog, fake_oauth, run_all):
    objects = welt["objects"]
    docs: dict[str, Document] = {}
    for entry in katalog["documents"]:
        docs[entry["id"]] = make_document(objects[entry["object"]], entry)
    for number in ("623", "624", "625"):
        start_run(objects[number])
        run_all(objects[number])
    failures = []
    for entry in katalog["documents"]:
        doc = Document.objects.get(pk=docs[entry["id"]].pk)
        exp = entry["expected"]
        got = {
            "category": doc.category_id,
            "subfolder": doc.subfolder.code if doc.subfolder else None,
            "document_type": doc.document_type.code if doc.document_type else None,
        }
        want = {
            "category": exp["category"],
            "subfolder": exp["subfolder"],
            "document_type": exp["document_type"],
        }
        if got != want:
            if exp.get("physical_category") == "06" and got["category"] == "06":
                final = DocumentClassification.objects.filter(document=doc, is_final=True).first()
                got = {
                    "category": final.category_id,
                    "subfolder": final.subfolder.code if final.subfolder else None,
                    "document_type": final.document_type.code if final.document_type else None,
                }
            if got != want:
                failures.append((entry["id"], "klassifikation", got, want, doc.final_confidence))
                continue
        cases = list(ReviewCase.objects.filter(document=doc, status="open"))
        if exp["case_type"]:
            if not any(c.case_type == exp["case_type"] for c in cases):
                failures.append((entry["id"], "fall", [c.case_type for c in cases], exp["case_type"]))
        elif exp.get("segments") is None and cases:
            failures.append(
                (entry["id"], "unerwarteter Fall", [(c.case_type, c.case_subtype) for c in cases])
            )
        if exp.get("period_year") and doc.period_year != exp["period_year"]:
            failures.append((entry["id"], "jahr", doc.period_year, exp["period_year"]))
        if exp.get("owner_files"):
            links = list(
                DocumentOwnerLink.objects.filter(document=doc, link_kind="whole_document").select_related(
                    "owner"
                )
            )
            names = sorted(_owner_of(link) for link in links)
            want_names = sorted(welt["owners"][k].last_name for k in exp["owner_files"])
            if names != want_names:
                failures.append((entry["id"], "akten", names, want_names))
            primary = [link for link in links if link.owner_file_id]
            if not primary or not primary[0].owner_file.folder_name.startswith("WE") and entry["id"] != "T12":
                failures.append((entry["id"], "akte", [link.owner_file.folder_name for link in primary]))
        if exp.get("segments") is not None:
            segs = list(
                DocumentOwnerLink.objects.filter(document=doc, link_kind="page_range")
                .select_related("owner")
                .order_by("page_from")
            )
            got_segs = [(s.page_from, s.page_to, _owner_of(s), s.subfolder.code) for s in segs]
            want_segs = [
                (s["page_from"], s["page_to"], welt["owners"][s["owner"]].last_name, s["subfolder"])
                for s in exp["segments"]
            ]
            if got_segs != want_segs:
                failures.append((entry["id"], "segmente", got_segs, want_segs))
            if exp["segments"] and not doc.is_master_with_segments:
                failures.append((entry["id"], "master"))
        # Ablage in Drive: ohne Fall filed im Zielordner
        if (
            not exp["case_type"]
            and doc.status != "filed"
            and not (exp.get("segments") and doc.status == "review")
        ):
            failures.append(
                (
                    entry["id"],
                    "status",
                    doc.status,
                    [j for j in doc.jobs.values_list("job_type", "status", "last_error")],
                )
            )
    assert failures == [], "\n".join(str(f) for f in failures)
    # T20: Zahlungserinnerung 03/2025 gehoert zum Alteigentuemer, Zuordnung mit valid_to 30.06.2026
    t20 = DocumentOwnerLink.objects.get(document=docs["T20"])
    assert t20.owner.last_name == "Altmann" and t20.assignment.valid_to.isoformat() == "2026-06-30"
    assert t20.period_from.isoformat() == "2025-03-01" and t20.period_to.isoformat() == "2025-03-31"
    # T10: eine Masterdatei in 03, zwoelf Segmente mit disjunkten Seitenbereichen, keine Drive-Duplikate
    t10 = Document.objects.get(pk=docs["T10"].pk)
    spans = list(
        DocumentOwnerLink.objects.filter(document=t10, link_kind="page_range").values_list(
            "page_from", "page_to"
        )
    )
    assert len(spans) == 12 and len({p for a, b in spans for p in range(a, b + 1)}) == 12
    drive = welt["drive"]
    assert (
        sum(
            1
            for op in drive.ops
            if op[0] in ("copy", "upload") and "Jahresabrechnung_2025_komplett" in str(op)
        )
        == 1
    )
    we05 = DocumentOwnerLink.objects.get(document=t10, page_from=7)
    assert we05.owner.last_name == "Altmann"  # Eigentuemer 2025
    # T18, T19: relational zu beiden Akten, physisch beim zuerst genannten (Altmann)
    for key in ("T18", "T19"):
        links = list(DocumentOwnerLink.objects.filter(document=docs[key]).order_by("id"))
        assert {link.owner.last_name for link in links} == {"Altmann", "Neumann"}
        d = Document.objects.get(pk=docs[key].pk)
        assert d.status == "filed" and d.drive_node.owner_file.folder_name.startswith("WE05_")
        assert "Altmann" in d.drive_node.owner_file.folder_name
    # T27: Mieterakte mit Verknuepfung zum Mieter
    assert DocumentTenantLink.objects.filter(document=docs["T27"], tenant__last_name="Mieterling").exists()
    # T28: Vorschlag Eigentuemerakte 08_Korrespondenz
    case28 = ReviewCase.objects.get(document=docs["T28"])
    assert case28.case_subtype == "tenant_document_in_weg" and case28.proposed_action["subfolder"] == "08"
    assert case28.misc_subfolder.code == "02"
    # T30: Vorschlag In Objekt 631 uebernehmen
    case30 = ReviewCase.objects.get(document=docs["T30"])
    assert case30.proposed_action == {"action": "transfer_to_object", "object_number": "631"}
    assert Document.objects.get(pk=docs["T30"].pk).drive_node.subfolder.code == "04"
    # T23: Kandidatenliste Altmann und Neumann, kein Raten
    case23 = ReviewCase.objects.get(document=docs["T23"])
    assert {c["owner"].split()[-1] for c in case23.candidates} == {"Altmann", "Neumann"}
    assert Document.objects.get(pk=docs["T23"].pk).category_id == "06"
    # Pruefabfrage CR 8: kein Dokument in 06 ohne offenen Fall
    ohne_fall = [
        d.pk
        for d in Document.objects.filter(category_id="06")
        if not ReviewCase.objects.filter(document=d, status__in=["open", "in_progress"]).exists()
    ]
    assert ohne_fall == []
    # KPI je Lauf
    run = ProcessingRun.objects.get(object=objects["623"])
    assert run.status == RunStatus.DONE
    misc = Document.objects.filter(object=objects["623"], category_id="06").count()
    assert run.documents_misc == misc and run.misc_share_pct is not None
    assert run.misc_share_adjusted_pct is not None and run.misc_share_adjusted_pct <= run.misc_share_pct
    # Ordnernamen der Eigentuemerakten
    names = set(OwnerFile.objects.filter(object=objects["623"]).values_list("folder_name", flat=True))
    assert "WE03_Mustermann" in names and "WE07_Beispiel" in names
    assert any(n.startswith("WE05_Altmann") for n in names) and any(
        n.startswith("WE05_Neumann") for n in names
    )
