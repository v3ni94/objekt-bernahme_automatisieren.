"""perf_run: Messlauf der Pipeline mit einem synthetischen Korpus (Umsetzungsplan M5 Schritt 12, M13).

Registriert die Dateien eines Korpusverzeichnisses (corpus_generator.py) als Uploads eines Testobjekts, startet den
Lauf und schreibt ein Messprotokoll: Seiten je Minute je Schritt, Dauer je Jobtyp, Plattenverbrauch work/, ocr-cache/,
previews/, RAM-Spitze des eigenen Prozesses und der OCR-Kindprozesse (nur im Modus --local messbar; im Betrieb liefert
docker stats die Werte je Container, docs/betrieb.md T10).

Modi:
  --local   Jobs laufen im aufrufenden Prozess (kein Worker noetig, Entwicklungsumgebung).
  Standard  Jobs gehen an die Worker; das Kommando wartet bis zum Laufende (--wait-minutes).
"""

from __future__ import annotations

import json
import resource
import time
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Avg, Count, Max, Sum
from django.utils import timezone

from apps.documents import ingest
from apps.documents.models import Document, DocumentPage
from apps.objects.models import ManagedObject
from apps.pipeline import storage
from apps.pipeline.models import JobStatus, ProcessingJob, ProcessingRun, RunStatus
from apps.pipeline.runs import start_run

SUFFIXES = {".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".docx", ".xlsx", ".csv", ".txt"}


class Command(BaseCommand):
    help = "Messlauf: Korpus als Uploads registrieren, Lauf starten, Messprotokoll schreiben"

    def add_arguments(self, parser):
        parser.add_argument(
            "--corpus", required=True, help="Verzeichnis mit PDF-Dateien (corpus_generator.py)"
        )
        parser.add_argument("--object", default="700", help="Objektnummer des Testobjekts (Standard 700)")
        parser.add_argument("--local", action="store_true", help="Jobs im aufrufenden Prozess ausfuehren")
        parser.add_argument("--wait-minutes", type=int, default=240)
        parser.add_argument(
            "--out", help="Zielpfad des Messprotokolls (JSON); Standard <corpus>/messprotokoll.json"
        )
        parser.add_argument(
            "--limit", type=int, default=0, help="hoechstens n Dateien registrieren (0 = alle)"
        )

    def handle(self, *args, **options):
        corpus = Path(options["corpus"])
        if not corpus.is_dir():
            raise CommandError(f"Korpus {corpus} nicht gefunden")
        obj = ManagedObject.active.filter(object_number=options["object"]).first()
        if obj is None:
            obj = ManagedObject.objects.create(
                object_number=options["object"],
                name="Messobjekt (synthetisch)",
                management_type="weg",
                is_test=True,
                city="Musterstadt",
                street="Testallee",
                house_number="100",
            )
        manifest = corpus / "manifest.json"
        if manifest.exists():
            entries = json.loads(manifest.read_text(encoding="utf-8")).get("documents", [])
            files = [corpus / e["path"] for e in entries if (corpus / e["path"]).is_file()]
        else:
            files = sorted(
                p
                for p in corpus.rglob("*")
                if p.is_file() and p.suffix.lower() in SUFFIXES and "truth" not in p.parts
            )
        if options["limit"]:
            files = files[: options["limit"]]
        if not files:
            raise CommandError("Keine Dateien im Korpus")
        self.stdout.write(f"{len(files)} Dateien werden für Objekt {obj.object_number} registriert")
        t0 = time.monotonic()
        for path in files:
            ingest.register_upload(obj, filename=path.name, data=path.read_bytes())
        t_register = time.monotonic() - t0
        run = start_run(obj)
        started = timezone.now()
        if options["local"]:
            from apps.pipeline.local import run_pending_jobs

            while True:
                ran = run_pending_jobs(obj, max_jobs=100_000)
                run.refresh_from_db()
                if run.status in (RunStatus.DONE, RunStatus.FAILED) or ran == 0:
                    break
        else:
            deadline = time.monotonic() + options["wait_minutes"] * 60
            while time.monotonic() < deadline:
                run.refresh_from_db()
                if run.status in (RunStatus.DONE, RunStatus.FAILED):
                    break
                time.sleep(10)
        run.refresh_from_db()
        report = self.report(obj, run, started, t_register, len(files), options["local"])
        out = Path(options["out"] or corpus / "messprotokoll.json")
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        self.stdout.write(json.dumps(report["zusammenfassung"], indent=2, ensure_ascii=False, default=str))
        self.stdout.write(f"Messprotokoll: {out}")

    def report(self, obj, run: ProcessingRun, started, t_register: float, files: int, local: bool) -> dict:
        jobs = ProcessingJob.objects.filter(object=obj, run=run)
        per_type = []
        for row in (
            jobs.filter(status=JobStatus.DONE)
            .values("job_type")
            .annotate(
                n=Count("id"),
                dauer_ms=Sum("duration_ms"),
                mittel_ms=Avg("duration_ms"),
                max_ms=Max("duration_ms"),
                seiten=Sum("pages_processed"),
            )
            .order_by("job_type")
        ):
            minutes = (row["dauer_ms"] or 0) / 60000
            row["seiten_je_minute"] = (
                round((row["seiten"] or 0) / minutes, 2) if minutes and row["seiten"] else None
            )
            per_type.append(row)
        pages = DocumentPage.objects.filter(document__object=obj)
        docs = Document.objects.filter(object=obj, deleted_at__isnull=True)
        elapsed_min = max(((run.finished_at or timezone.now()) - started).total_seconds() / 60, 0.001)
        usage_self = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        usage_children = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
        disk = {
            "work": storage.dir_size(storage.data_dir() / "work"),
            "ocr_cache": storage.dir_size(storage.data_dir() / "ocr-cache"),
            "previews": storage.dir_size(storage.data_dir() / "previews"),
        }
        summary = {
            "objekt": obj.object_number,
            "lauf": run.pk,
            "status": run.status,
            "dateien": files,
            "registrierung_s": round(t_register, 1),
            "dokumente": {r["status"]: r["c"] for r in docs.values("status").annotate(c=Count("id"))},
            "seiten_gesamt": pages.count(),
            "seiten_ocr": pages.filter(text_source="ocr").count(),
            "seiten_textebene": pages.filter(text_source="text_layer").count(),
            "laufzeit_min": round(elapsed_min, 2),
            "seiten_je_minute_gesamt": round(pages.count() / elapsed_min, 2),
            "modus": "lokal (Entwicklungsumgebung)" if local else "Worker",
            "ram_spitze_mb_eigener_prozess": round(usage_self / 1024, 1) if local else None,
            "ram_spitze_mb_ocr_kindprozesse": round(usage_children / 1024, 1) if local else None,
            "platte_bytes": disk,
            "platte_mb": {k: round(v / 1024**2, 2) for k, v in disk.items()},
            "platte_faktor_je_seite_kb": round(sum(disk.values()) / 1024 / max(pages.count(), 1), 1),
            "hinweis": "RAM je Container im Betrieb über docker stats (T10); Messwerte der Entwicklungsumgebung sind keine VPS-Werte",
        }
        return {"zusammenfassung": summary, "je_jobtyp": per_type, "erzeugt": timezone.now().isoformat()}
