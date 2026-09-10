#!/usr/bin/env python3
"""OCR-Probelauf fuer Meilenstein M0 (Umsetzungsplan 2.1, Schritte 6 bis 8).

Misst auf dem Rechner, auf dem das Skript laeuft:

- t_ocr   Sekunden je Scan-Seite je Prozess (ocrmypdf mit Tesseract deu, ein Thread)
- e       Skalierungseffizienz bei P parallelen OCR-Prozessen
- m_ocr   Speicherspitze je OCR-Prozess (Maximum Resident Set Size)
- t_txt   Sekunden je Digitalseite (Textextraktion plus Seitenbild)
- CER     Zeichenfehlerrate der OCR gegen den bekannten Quelltext des Korpus
- Plattenfaktor OCR-Ausgabe gegen Original, Zeit und Bytes je Seitenbild

und rechnet damit den Rechenweg aus Umsetzungsplan 2.8 neu (perf_model.py). Ergebnis:
results.json und performance-entscheidung.md im Ausgabeverzeichnis.

Das Skript baut nichts und aendert nichts am System. Es braucht: ocrmypdf, tesseract mit deu,
pdftotext und pdftoppm (poppler-utils), pdfinfo, GNU time (/usr/bin/time). Auf dem VPS laeuft
es in einem temporaeren Container (scripts/perf_probe.sh, docker/probe.Dockerfile).

Beispiel:
    python3 perf_probe.py --corpus /out/corpus --out /out --procs 1,2,auto --host-label "VPS"
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import perf_model as pm  # noqa: E402

GNU_TIME = "/usr/bin/time"


# ----------------------------------------------------------------------------- Hilfsfunktionen

def run(cmd: list[str], env: dict | None = None, timeout: int | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=timeout)


def tool_version(cmd: list[str]) -> str:
    try:
        r = run(cmd, timeout=30)
        out = (r.stdout or r.stderr).strip().splitlines()
        return out[0] if out else "unbekannt"
    except Exception as exc:  # pragma: no cover
        return f"nicht verfuegbar ({exc.__class__.__name__})"


def page_count(pdf: Path) -> int:
    r = run(["pdfinfo", str(pdf)])
    for line in r.stdout.splitlines():
        if line.startswith("Pages:"):
            return int(line.split(":", 1)[1].strip())
    raise RuntimeError(f"Seitenzahl von {pdf} nicht ermittelbar (pdfinfo)")


def timed(cmd: list[str], env: dict) -> tuple[float, int, int, str]:
    """Fuehrt cmd unter GNU time aus. Liefert (Wandzeit s, MaxRSS KB, Returncode, stderr)."""
    with_time = [GNU_TIME, "-f", "%e %M"] + cmd
    start = time.perf_counter()
    r = subprocess.run(with_time, capture_output=True, text=True, env=env)
    wall_fallback = time.perf_counter() - start
    stderr_lines = r.stderr.strip().splitlines()
    wall, rss = wall_fallback, 0
    if stderr_lines:
        try:
            w, m = stderr_lines[-1].split()
            wall, rss = float(w), int(m)
            stderr_lines = stderr_lines[:-1]
        except ValueError:
            pass
    return wall, rss, r.returncode, "\n".join(stderr_lines[-5:])


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        for j, cb in enumerate(b, start=1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def normalize(text: str) -> str:
    return " ".join(text.replace("­", "").split())


def fmt_de(value: float, digits: int = 2) -> str:
    s = f"{value:,.{digits}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


# ----------------------------------------------------------------------------- Messungen

def ocr_one(doc: dict, corpus: Path, outdir: Path, lang: str, env: dict, tag: str) -> dict:
    src = corpus / doc["path"]
    dst = outdir / f"{doc['name']}.{tag}.pdf"
    sidecar = outdir / f"{doc['name']}.{tag}.txt"
    cmd = ["ocrmypdf", "--skip-text", "--jobs", "1", "-l", lang, "--output-type", "pdf",
           "--optimize", "0", "--sidecar", str(sidecar), str(src), str(dst)]
    wall, rss, rc, err = timed(cmd, env)
    return {"name": doc["name"], "pages": doc["pages"], "wall": wall, "max_rss_kb": rss,
            "returncode": rc, "stderr": err, "out": str(dst) if dst.exists() else None,
            "sidecar": str(sidecar) if sidecar.exists() else None, "in_bytes": doc["bytes"],
            "out_bytes": dst.stat().st_size if dst.exists() else 0}


def measure_ocr(scans: list[dict], corpus: Path, outdir: Path, lang: str, env: dict, procs: int,
                min_tasks: int) -> dict:
    """Laeuft alle Scans mit `procs` parallelen ocrmypdf-Prozessen; wiederholt Dateien, bis
    mindestens min_tasks Auftraege vorliegen, damit die Parallelitaet ausgelastet ist."""
    tasks = list(scans)
    rep = 0
    while len(tasks) < max(min_tasks, procs * 2):
        rep += 1
        tasks.extend(dict(d, name=f"{d['name']}_r{rep}") for d in scans)
    tag = f"p{procs}"
    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=procs) as pool:
        results = list(pool.map(lambda d: ocr_one(d, corpus, outdir, lang, env, tag), tasks))
    total_wall = time.perf_counter() - start
    pages = sum(r["pages"] for r in results if r["returncode"] == 0)
    failed = [r for r in results if r["returncode"] != 0]
    return {
        "procs": procs,
        "tasks": len(tasks),
        "pages": pages,
        "failed": len(failed),
        "failed_examples": [f["stderr"] for f in failed[:2]],
        "wall_total": total_wall,
        "pages_per_second": pages / total_wall if total_wall > 0 else 0.0,
        "pages_per_minute": pages * 60.0 / total_wall if total_wall > 0 else 0.0,
        "sum_wall_single_tasks": sum(r["wall"] for r in results),
        "max_rss_kb_per_process": max((r["max_rss_kb"] for r in results), default=0),
        "results": results,
    }


def measure_digital(digitals: list[dict], corpus: Path, outdir: Path, env: dict) -> dict:
    t_text = t_preview = 0.0
    preview_bytes = 0
    pages = 0
    for doc in digitals:
        src = corpus / doc["path"]
        t0 = time.perf_counter()
        run(["pdftotext", "-layout", str(src), str(outdir / f"{doc['name']}.txt")], env=env)
        t_text += time.perf_counter() - t0
        prefix = outdir / f"{doc['name']}.preview"
        t0 = time.perf_counter()
        run(["pdftoppm", "-jpeg", "-r", "100", "-scale-to", "1200", str(src), str(prefix)], env=env)
        t_preview += time.perf_counter() - t0
        for jpg in outdir.glob(f"{doc['name']}.preview*.jpg"):
            preview_bytes += jpg.stat().st_size
        pages += doc["pages"]
    return {
        "pages": pages,
        "t_text_total": t_text,
        "t_preview_total": t_preview,
        "t_txt_per_page": (t_text + t_preview) / pages if pages else 0.0,
        "preview_seconds_per_page": t_preview / pages if pages else 0.0,
        "preview_bytes_per_page": preview_bytes / pages if pages else 0.0,
    }


def measure_cer(single_run: dict, corpus: Path) -> dict:
    """Zeichenfehlerrate je Seite: Sidecar-Text (Seitentrenner Form Feed) gegen Wahrheit."""
    per_page = []
    for r in single_run["results"]:
        if not r["sidecar"]:
            continue
        truth_dir = corpus / "truth" / r["name"]
        text = Path(r["sidecar"]).read_text(encoding="utf-8", errors="replace")
        pages = text.split("\f")
        for i, truth_file in enumerate(sorted(truth_dir.glob("p*.txt"))):
            truth = normalize(truth_file.read_text(encoding="utf-8"))
            got = normalize(pages[i]) if i < len(pages) else ""
            if not truth:
                continue
            per_page.append(levenshtein(truth, got) / len(truth))
    if not per_page:
        return {"pages_compared": 0, "cer_mean": None, "cer_max": None}
    return {"pages_compared": len(per_page), "cer_mean": sum(per_page) / len(per_page),
            "cer_max": max(per_page)}


# ----------------------------------------------------------------------------- Bericht

def build_report(res: dict, args: argparse.Namespace) -> str:
    C = res["environment"]["cpu_count"]
    P_default = max(1, C - 1)
    single = res["ocr_runs"][0]
    t_ocr = res["derived"]["t_ocr"]
    e = res["derived"]["e"]
    m_ocr_gib = res["derived"]["m_ocr_gib"]
    t_txt = res["derived"]["t_txt"]

    scenarios = [
        pm.Scenario("Basis (ANNAHME A-04: 40 Prozent Digitalseiten)", 0.40, t_ocr, t_txt),
        pm.Scenario("Ungünstig (0 Prozent Digitalseiten)", 0.0, t_ocr, t_txt),
    ]
    proc_cols = sorted({P_default, 3, 5, 7} | {r["procs"] for r in res["ocr_runs"]})
    rows = pm.scenario_table(scenarios, proc_cols, e)
    res["scenarios"] = rows

    basis_pmin, ung_pmin = rows[0]["p_min"], rows[1]["p_min"]
    if P_default >= ung_pmin:
        verdict = f"Mit P = {P_default} OCR-Prozessen hält die 3-Stunden-Vorgabe im Basisfall und im ungünstigen Fall mit Reserve."
    elif P_default >= basis_pmin:
        verdict = (f"Mit P = {P_default} OCR-Prozessen hält die Vorgabe im Basisfall, nicht im ungünstigen Fall "
                   f"(dafür wären {ung_pmin} Prozesse nötig). Stellhebel nach Umsetzungsplan 2.8 prüfen.")
    else:
        verdict = (f"Mit P = {P_default} OCR-Prozessen wird das OCR-Budget von 2 Stunden im Basisfall verfehlt "
                   f"(nötig: {basis_pmin} Prozesse). Entscheidungsvorlage nach Frage F18: Tarifwechsel, "
                   f"tessdata_fast, Zwei-Phasen-OCR oder längere Laufzeit.")
    f18 = P_default < 5 or t_ocr > 5.0
    res["recommendation"] = {"P_default": P_default, "p_min_basis": basis_pmin, "p_min_unguenstig": ung_pmin,
                             "verdict": verdict, "f18_decision_required": f18}

    worker_mem = P_default * max(m_ocr_gib, 0.25) + 0.5
    lines = []
    lines.append("# Performance-Entscheidung (Meilenstein M0, OCR-Probelauf)")
    lines.append("")
    lines.append(f"Stand: {res['timestamp']}. Messrechner: {args.host_label}. "
                 "Alle Werte in diesem Dokument sind Messwerte dieses Laufs, außer den ausdrücklich als ANNAHME gekennzeichneten.")
    if not args.on_target_host:
        lines.append("")
        lines.append("**Hinweis: Dieser Lauf fand nicht auf dem Zielserver statt. Die Werte belegen nur die Funktion des "
                     "Messverfahrens und sind keine Grundlage für die Tarif- oder Prozessentscheidung.**")
    lines.append("")
    lines.append("## 1. Umgebung")
    lines.append("")
    lines.append("| Größe | Wert |")
    lines.append("|---|---|")
    env = res["environment"]
    for k, label in [("cpu_count", "Kerne (os.cpu_count)"), ("platform", "System"), ("tesseract", "Tesseract"),
                     ("ocrmypdf", "ocrmypdf"), ("languages", "Sprachdaten"), ("deu_traineddata_bytes", "Größe deu.traineddata (Bytes)"),
                     ("omp_thread_limit", "OMP_THREAD_LIMIT")]:
        lines.append(f"| {label} | {env.get(k)} |")
    lines.append("")
    lines.append("## 2. Korpus")
    lines.append("")
    lines.append(f"Synthetischer Korpus ({res['corpus']['documents']} Dokumente): {res['corpus']['pages_scan']} Scan-Seiten "
                 f"ohne Textebene bei {res['corpus']['dpi']} dpi, {res['corpus']['pages_digital']} Digitalseiten. "
                 "Keine realen Daten. Synthetische Scans sind sauberer als echte Scans; die Zeichenfehlerrate echter Unterlagen liegt höher.")
    lines.append("")
    lines.append("## 3. Messwerte")
    lines.append("")
    lines.append("| Größe | Messwert | Ersetzt Annahme |")
    lines.append("|---|---|---|")
    lines.append(f"| t_ocr (Sekunden je Scan-Seite, ein Prozess) | {fmt_de(t_ocr, 2)} s | A-03 / A1 |")
    lines.append(f"| Durchsatz ein Prozess | {fmt_de(single['pages_per_minute'], 1)} Seiten je Minute | |")
    for r in res["ocr_runs"][1:]:
        lines.append(f"| Durchsatz P = {r['procs']} | {fmt_de(r['pages_per_minute'], 1)} Seiten je Minute, e = {fmt_de(r['efficiency'], 2)} | A-06 / A4 |")
    lines.append(f"| e (verwendet) | {fmt_de(e, 2)}, {res['derived']['e_source']} | A-06 / A4 |")
    lines.append(f"| m_ocr (Speicherspitze je OCR-Prozess) | {fmt_de(m_ocr_gib, 2)} GiB | A-41 / A8 |")
    lines.append(f"| t_txt (Sekunden je Digitalseite, Text plus Seitenbild) | {fmt_de(t_txt, 3)} s | A-05 / A3 |")
    lines.append(f"| Seitenbild | {fmt_de(res['digital']['preview_seconds_per_page'], 3)} s, {fmt_de(res['digital']['preview_bytes_per_page'] / 1024, 0)} KB je Seite | A-14 / A18 |")
    lines.append(f"| Plattenfaktor OCR-Ausgabe gegen Original | {fmt_de(res['derived']['disk_factor'], 2)} | A36 |")
    cer = res["cer"]
    if cer["cer_mean"] is not None:
        lines.append(f"| Zeichenfehlerrate (Mittel, {cer['pages_compared']} Seiten) | {fmt_de(cer['cer_mean'] * 100, 2)} Prozent (Maximum {fmt_de(cer['cer_max'] * 100, 2)} Prozent) | A39 |")
    if single["failed"]:
        lines.append(f"| Fehlgeschlagene OCR-Aufträge | {single['failed']} | |")
    lines.append("")
    lines.append("## 4. Rechenweg (Umsetzungsplan 2.8) mit Messwerten")
    lines.append("")
    lines.append("```text")
    lines.append("T_ocr = S × (1 − d) × t_ocr / (P × e) + S × d × t_txt / P")
    lines.append(f"S = {fmt_de(pm.PAGES_PER_OBJECT, 0)}, t_ocr = {fmt_de(t_ocr, 2)} s, t_txt = {fmt_de(t_txt, 3)} s, "
                 f"e = {fmt_de(e, 2)}, OCR-Budget = {fmt_de(pm.OCR_BUDGET_SECONDS, 0)} s")
    lines.append("```")
    lines.append("")
    header = "| Szenario | CPU-Sekunden | P_min (Budget 2 h) | " + " | ".join(f"P = {p}" for p in proc_cols) + " |"
    lines.append(header)
    lines.append("|" + "---|" * (3 + len(proc_cols)))
    for row in rows:
        cells = []
        for p in proc_cols:
            c = row["cells"][p]
            mark = "" if c["within_ocr_budget"] else (" (ohne Reserve)" if c["within_total_budget"] else " (verfehlt)")
            cells.append(f"{fmt_de(c['wall_minutes'], 0)} min{mark}")
        lines.append(f"| {row['scenario']} | {fmt_de(row['cpu_seconds'], 0)} | {row['p_min']} | " + " | ".join(cells) + " |")
    lines.append("")
    lines.append("Zu jeder Zelle kommen 10 bis 15 Minuten Nachlauf (ANNAHME A-07).")
    lines.append("")
    lines.append("## 5. Ergebnis und Empfehlung")
    lines.append("")
    lines.append(f"Gemessene Kerne C = {C}, Standard P = C minus 1 = {P_default}.")
    lines.append("")
    if res["derived"]["e_measured_at_p"] is None:
        lines.append("**Die Skalierungseffizienz wurde nicht gemessen (nur P = 1). Die folgende Aussage beruht auf "
                     "ANNAHME A-06 und ist keine Entscheidungsgrundlage; Lauf mit --procs 1,2,auto wiederholen.**")
        lines.append("")
    lines.append(verdict)
    lines.append("")
    if f18:
        lines.append("Kriterium aus Frage F18 erfüllt (weniger als fünf nutzbare OCR-Prozesse oder t_ocr über 5 s): "
                     "Entscheidungsvorlage an den Auftraggeber vor M1.")
    else:
        lines.append("Kriterium aus Frage F18 nicht erfüllt: kein Tarifwechsel erforderlich; Bau mit P = C minus 1.")
    lines.append("")
    lines.append("Vorschlag für `.env` (nach Prüfung der Speicherformel in docs/architektur.md 4.4 gegen den gemessenen Arbeitsspeicher):")
    lines.append("")
    lines.append("```dotenv")
    lines.append(f"OCR_PROCESSES={P_default}")
    lines.append(f"WORKER_CPUS={P_default}")
    lines.append(f"WORKER_MEM={worker_mem:.2f}g")
    lines.append(f"WORKER_MAX_MEMORY_PER_CHILD_KB={int(max(m_ocr_gib, 0.25) * 1024 * 1024)}")
    lines.append("```")
    lines.append("")
    lines.append("Hinweise: Konfigurationswerte stehen mit Dezimalpunkt, wie Compose sie erwartet. m_ocr wurde an kurzen "
                 "Dokumenten gemessen; die Blockgröße der OCR (ocr.chunk_pages, ANNAHME A-09) wird in M5 mit dem "
                 "1.000-Seiten-Messlauf gegengeprüft. Randbedingung: Summe aller Speicherlimits höchstens 0,85 × M.")
    lines.append("")
    lines.append("## 6. Rohdaten")
    lines.append("")
    lines.append("Vollständige Messreihe in `results.json` im selben Verzeichnis.")
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------------- Hauptprogramm

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", required=True, type=Path, help="Korpusverzeichnis aus corpus_generator.py")
    ap.add_argument("--out", required=True, type=Path, help="Ausgabeverzeichnis")
    ap.add_argument("--procs", default="1,2,auto", help="Prozesszahlen, Komma getrennt; auto = Kerne minus 1")
    ap.add_argument("--lang", default="deu")
    ap.add_argument("--tessdata-dir", default=None, help="Alternative Sprachdaten (zum Beispiel tessdata_fast)")
    ap.add_argument("--min-tasks", type=int, default=8, help="Mindestzahl OCR-Aufträge je Parallelitätsstufe")
    ap.add_argument("--host-label", default=platform.node())
    ap.add_argument("--on-target-host", action="store_true", help="Setzen, wenn der Lauf auf dem Zielserver stattfindet")
    args = ap.parse_args()

    for tool in ("ocrmypdf", "tesseract", "pdfinfo", "pdftotext", "pdftoppm"):
        if not shutil.which(tool):
            sys.exit(f"Werkzeug fehlt: {tool}")
    if not Path(GNU_TIME).exists():
        sys.exit("GNU time fehlt (/usr/bin/time, Paket time)")

    manifest = json.loads((args.corpus / "manifest.json").read_text(encoding="utf-8"))
    docs = manifest["documents"]
    scans = [d for d in docs if d["kind"] == "scan"]
    digitals = [d for d in docs if d["kind"] == "digital"]
    if not scans:
        sys.exit("Korpus enthält keine Scan-Dokumente")

    env = dict(os.environ)
    env["OMP_THREAD_LIMIT"] = "1"
    if args.tessdata_dir:
        env["TESSDATA_PREFIX"] = args.tessdata_dir

    cpu_count = os.cpu_count() or 1
    procs = []
    for p in args.procs.split(","):
        p = p.strip()
        procs.append(max(1, cpu_count - 1) if p == "auto" else int(p))
    procs = sorted(set(procs))
    if 1 not in procs:
        procs.insert(0, 1)

    out = args.out
    work = out / "ocr-out"
    work.mkdir(parents=True, exist_ok=True)

    langs = run(["tesseract", "--list-langs"], env=env)
    deu_size = None
    for cand in [Path(env.get("TESSDATA_PREFIX", "")) / f"{args.lang}.traineddata",
                 Path("/usr/share/tesseract-ocr/5/tessdata") / f"{args.lang}.traineddata",
                 Path("/usr/share/tesseract-ocr/4.00/tessdata") / f"{args.lang}.traineddata",
                 Path("/usr/share/tessdata") / f"{args.lang}.traineddata"]:
        if cand.exists():
            deu_size = cand.stat().st_size
            break

    res: dict = {
        "timestamp": datetime.now().strftime("%d.%m.%Y %H:%M"),
        "host_label": args.host_label,
        "on_target_host": args.on_target_host,
        "environment": {
            "cpu_count": cpu_count,
            "platform": platform.platform(),
            "tesseract": tool_version(["tesseract", "--version"]),
            "ocrmypdf": tool_version(["ocrmypdf", "--version"]),
            "languages": " ".join(l for l in langs.stdout.split() if l not in ("List", "of", "available", "languages", "in")),
            "deu_traineddata_bytes": deu_size,
            "omp_thread_limit": env["OMP_THREAD_LIMIT"],
        },
        "corpus": {"documents": len(docs), "pages_scan": manifest.get("pages_scan"),
                   "pages_digital": manifest.get("pages_digital"), "dpi": manifest.get("dpi")},
        "ocr_runs": [],
    }

    print(f"[probe] Kerne: {cpu_count}, Prozessstufen: {procs}, Scans: {len(scans)} Dokumente", flush=True)
    single = None
    for p in procs:
        print(f"[probe] OCR mit P = {p} ...", flush=True)
        r = measure_ocr(scans, args.corpus, work, args.lang, env, p, args.min_tasks)
        if single is None:
            single = r
            r["efficiency"] = 1.0
        else:
            r["efficiency"] = pm.scaling_efficiency(single["pages_per_second"], r["pages_per_second"], p)
        print(f"[probe]   {r['pages']} Seiten in {r['wall_total']:.1f} s, {r['pages_per_minute']:.1f} Seiten/min, "
              f"e = {r['efficiency']:.2f}, Fehler: {r['failed']}", flush=True)
        res["ocr_runs"].append(r)

    print("[probe] Digitalseiten ...", flush=True)
    res["digital"] = measure_digital(digitals, args.corpus, work, env) if digitals else {
        "pages": 0, "t_txt_per_page": 0.0, "preview_seconds_per_page": 0.0, "preview_bytes_per_page": 0.0}
    print("[probe] Zeichenfehlerrate ...", flush=True)
    res["cer"] = measure_cer(single, args.corpus)

    in_bytes = sum(r["in_bytes"] for r in single["results"])
    out_bytes = sum(r["out_bytes"] for r in single["results"])
    largest = res["ocr_runs"][-1]
    # Ohne Parallelmessung wird die Skalierungseffizienz nicht mit 1,0 geschoent, sondern mit der
    # Planungsgroesse aus dem Umsetzungsplan (ANNAHME A-06: 0,85) gerechnet und so gekennzeichnet.
    e_measured = largest is not single
    res["derived"] = {
        "t_ocr": single["wall_total"] / single["pages"] if single["pages"] else float("nan"),
        "e": largest["efficiency"] if e_measured else 0.85,
        "e_source": f"gemessen bei P = {largest['procs']}" if e_measured else "ANNAHME A-06 (nicht gemessen, nur P = 1)",
        "e_measured_at_p": largest["procs"] if e_measured else None,
        "m_ocr_gib": max(r["max_rss_kb_per_process"] for r in res["ocr_runs"]) / (1024 * 1024),
        "t_txt": res["digital"]["t_txt_per_page"],
        "disk_factor": out_bytes / in_bytes if in_bytes else 0.0,
    }

    report = build_report(res, args)
    for r in res["ocr_runs"]:
        for item in r["results"]:
            item.pop("stderr", None)
    (out / "results.json").write_text(json.dumps(res, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    (out / "performance-entscheidung.md").write_text(report, encoding="utf-8")
    print(f"[probe] fertig: {out / 'performance-entscheidung.md'}", flush=True)
    print(res["recommendation"]["verdict"], flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
