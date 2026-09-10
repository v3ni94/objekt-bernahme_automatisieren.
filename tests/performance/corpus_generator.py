#!/usr/bin/env python3
"""Synthetischer Testkorpus fuer den OCR-Probelauf (M0) und den Performance-Test (M13).

Erzeugt PDF-Dokumente mit erfundenem Inhalt in zwei Ausfuehrungen:

- digital: PDF mit Textebene (ReportLab), wie ein aus einer Verwaltungssoftware exportiertes Dokument
- scan: dieselben Seiten als reine Bild-PDF ohne Textebene (gerastert mit der angegebenen
  Aufloesung), wie ein Scan; optional leicht gedreht und JPEG-komprimiert

Je Seite wird der Quelltext als Wahrheit (truth/<dokument>/p<nr>.txt) abgelegt, damit der
Probelauf die Zeichenfehlerrate der OCR bestimmen kann.

Es werden ausschliesslich synthetische Daten verwendet (Namen Mustermann, Beispiel, Altmuster,
Neumuster; Objektnummern aus dem reservierten Bereich 623, 624, 625, 631, 700; keine echten
IBAN, keine echten Anschriften). Der Generator ist deterministisch (Parameter --seed).

Aufruf (Beispiel M0, 100 Seiten, Haelfte Scan):
    python3 corpus_generator.py --out /out/corpus --pages 100 --scan-share 0.5 --dpi 300

Abhaengigkeiten: reportlab, Pillow; fuer die Rasterung pdftoppm (poppler-utils) oder gs (Ghostscript).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas
except ImportError:  # pragma: no cover
    sys.exit("reportlab fehlt: pip install reportlab")

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    sys.exit("Pillow fehlt: pip install pillow")


OBJECTS = [
    ("623", "Musterstadt", "Musterstraße 49"),
    ("624", "Beispielhausen", "Beispielweg 7"),
    ("625", "Musterstadt", "Am Musterpark 12"),
    ("631", "Probedorf", "Probegasse 3"),
    ("700", "Musterstadt", "Testallee 100"),
]
SURNAMES = ["Mustermann", "Beispiel", "Altmuster", "Neumuster", "Musterfrau", "Probemann"]
FIRSTNAMES = ["Anna", "Max", "Erika", "Peter", "Julia", "Thomas"]
UNIT_PREFIXES = ["WE", "WE ", "GE ", "ST", "TG"]

# Dokumentvorlagen: Titel, Zielkategorie laut CR (nur zur Information im Manifest), Absaetze
TEMPLATES = [
    {
        "title": "Einzelabrechnung {year} für Einheit {unit}",
        "category": "05/05_Abrechnungen",
        "body": [
            "Wohnungseigentümergemeinschaft {city}, {street}. Abrechnungszeitraum 01.01.{year} bis 31.12.{year}.",
            "Eigentümer: {first} {last}, Einheit {unit}, Miteigentumsanteil {mea}/10.000.",
            "Die Gesamtkosten der Gemeinschaft betragen {total} EUR. Der auf Ihre Einheit entfallende Anteil beträgt {share} EUR.",
            "Geleistete Hausgeldvorauszahlungen: {prepaid} EUR. Abrechnungsergebnis: {result} EUR {result_kind}.",
            "Die Instandhaltungsrücklage wurde mit {reserve} EUR dotiert. Der Anteil Ihrer Einheit beträgt {reserve_share} EUR.",
        ],
        "table": ["Heizkosten", "Wasser und Abwasser", "Allgemeinstrom", "Hausmeister", "Gebäudeversicherung", "Verwaltung", "Aufzug", "Gartenpflege"],
    },
    {
        "title": "Einzelwirtschaftsplan {year} für Einheit {unit}",
        "category": "05/06_Wirtschaftsplaene",
        "body": [
            "Wirtschaftsplan der Wohnungseigentümergemeinschaft {city}, {street} für das Wirtschaftsjahr {year}.",
            "Eigentümer: {first} {last}. Einheit {unit}. Verteilungsschlüssel: Miteigentumsanteile {mea}/10.000.",
            "Monatliches Hausgeld ab 01.01.{year}: {monthly} EUR, davon Zuführung zur Rücklage {reserve_monthly} EUR.",
            "Das Hausgeld ist jeweils zum dritten Werktag eines Monats fällig.",
        ],
        "table": ["Heizung", "Wasser", "Strom", "Hausmeister", "Versicherung", "Verwaltung", "Rücklage"],
    },
    {
        "title": "Zahlungserinnerung Hausgeld {month}/{year}",
        "category": "05/09_Mahnwesen",
        "body": [
            "Sehr geehrte Frau {last}, sehr geehrter Herr {last},",
            "für die Einheit {unit} im Objekt {number} {city}, {street} ist das Hausgeld für den Monat {month}/{year} in Höhe von {monthly} EUR bisher nicht eingegangen.",
            "Wir bitten Sie, den offenen Betrag bis zum {due} auf das Konto der Gemeinschaft zu überweisen.",
            "Sollte die Zahlung inzwischen erfolgt sein, betrachten Sie dieses Schreiben bitte als gegenstandslos.",
        ],
        "table": [],
    },
    {
        "title": "Protokoll der Eigentümerversammlung vom {date}",
        "category": "02_Stammakte",
        "body": [
            "Wohnungseigentümergemeinschaft {city}, {street}. Versammlungsort: Gemeinschaftsraum. Beginn 18:00 Uhr, Ende 20:15 Uhr.",
            "Anwesend oder vertreten: {present} von {units} Einheiten mit {mea_present} von 10.000 Miteigentumsanteilen. Die Versammlung ist beschlussfähig.",
            "TOP 1 Jahresabrechnung {prev_year}: Die Gesamtabrechnung und die Einzelabrechnungen werden mit {yes} Ja-Stimmen und {no} Nein-Stimmen beschlossen.",
            "TOP 2 Wirtschaftsplan {year}: Der Gesamtwirtschaftsplan wird einstimmig beschlossen.",
            "TOP 3 Instandsetzung Treppenhaus: Der Antrag des Eigentümers {last} (Einheit {unit}) wird mit {yes} zu {no} Stimmen angenommen.",
            "TOP 4 Entlastung der Verwaltung: Die Verwaltung wird mit {yes} Ja-Stimmen entlastet.",
        ],
        "table": [],
    },
    {
        "title": "SEPA-Lastschriftmandat",
        "category": "05/03_SEPA",
        "body": [
            "Zahlungsempfänger: Wohnungseigentümergemeinschaft {city}, {street}, vertreten durch die Verwaltung.",
            "Ich ermächtige den Zahlungsempfänger, Zahlungen von meinem Konto mittels Lastschrift einzuziehen. Zugleich weise ich mein Kreditinstitut an, die Lastschriften einzulösen.",
            "Kontoinhaber: {first} {last}. Kreditinstitut: Musterbank. IBAN: DE00 1234 5678 9012 3456 78 (Prüfziffer ungültig, Testdaten). Mandatsreferenz: {mandate}.",
            "Hinweis: Ich kann innerhalb von acht Wochen, beginnend mit dem Belastungsdatum, die Erstattung des belasteten Betrages verlangen.",
        ],
        "table": [],
    },
    {
        "title": "Mitteilung über den Eigentumswechsel Einheit {unit}",
        "category": "05/02_Eigentumsnachweise",
        "body": [
            "Hiermit teilen wir mit, dass das Eigentum an der Einheit {unit} im Objekt {number} {city}, {street} zum {date} von {first} Altmuster auf {first2} Neumuster übergegangen ist.",
            "Der Übergang von Nutzen und Lasten erfolgte zum {date}. Die Eintragung im Grundbuch wird nachgereicht.",
            "Ab dem Übergangszeitpunkt ist das Hausgeld von der Erwerberin bzw. dem Erwerber zu leisten.",
        ],
        "table": [],
    },
    {
        "title": "Gebäudeversicherung Objekt {number}: Nachtrag zum Vertrag",
        "category": "02_Stammakte",
        "body": [
            "Versicherungsnehmer: Wohnungseigentümergemeinschaft {city}, {street}. Versichertes Gebäude: Wohnhaus mit {units} Einheiten.",
            "Der Jahresbeitrag beträgt ab dem {date} {total} EUR einschließlich Versicherungsteuer. Versicherte Gefahren: Feuer, Leitungswasser, Sturm und Hagel, Elementarschäden.",
            "Die Selbstbeteiligung je Schadensfall beträgt {share} EUR.",
        ],
        "table": [],
    },
]


def rnd_amount(rng: random.Random, low: float, high: float) -> str:
    value = rng.uniform(low, high)
    return f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def make_fields(rng: random.Random) -> dict:
    number, city, street = rng.choice(OBJECTS)
    year = rng.randint(2022, 2026)
    prefix = rng.choice(UNIT_PREFIXES)
    unit_no = rng.randint(1, 40)
    result = rng.uniform(-900, 900)
    return {
        "number": number,
        "city": city,
        "street": street,
        "year": year,
        "prev_year": year - 1,
        "month": f"{rng.randint(1, 12):02d}",
        "date": f"{rng.randint(1, 28):02d}.{rng.randint(1, 12):02d}.{year}",
        "due": f"{rng.randint(1, 28):02d}.{rng.randint(1, 12):02d}.{year}",
        "unit": f"{prefix}{unit_no:02d}",
        "first": rng.choice(FIRSTNAMES),
        "first2": rng.choice(FIRSTNAMES),
        "last": rng.choice(SURNAMES),
        "mea": rng.randint(120, 900),
        "total": rnd_amount(rng, 40_000, 180_000),
        "share": rnd_amount(rng, 900, 6_000),
        "prepaid": rnd_amount(rng, 1_500, 5_000),
        "result": f"{abs(result):,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
        "result_kind": "Nachzahlung" if result > 0 else "Guthaben",
        "reserve": rnd_amount(rng, 5_000, 30_000),
        "reserve_share": rnd_amount(rng, 100, 900),
        "monthly": rnd_amount(rng, 120, 650),
        "reserve_monthly": rnd_amount(rng, 20, 120),
        "present": rng.randint(4, 30),
        "units": rng.randint(8, 40),
        "mea_present": rng.randint(4_000, 9_800),
        "yes": rng.randint(3, 25),
        "no": rng.randint(0, 5),
        "mandate": f"WEG{number}-{unit_no:03d}-{rng.randint(1000, 9999)}",
    }


def build_page_texts(rng: random.Random, template: dict, fields: dict, pages: int) -> list[list[str]]:
    """Liefert je Seite eine Liste von Zeilen (Wahrheit fuer die OCR-Pruefung)."""
    title = template["title"].format(**fields)
    header = f"Objekt {fields['number']} {fields['city']}, {fields['street']}"
    result = []
    for page_no in range(1, pages + 1):
        lines = [header, title if page_no == 1 else f"{title} (Fortsetzung)", ""]
        body = [p.format(**fields) for p in template["body"]]
        rng.shuffle(body)
        lines.extend(body)
        if template["table"]:
            lines.append("")
            lines.append("Position                          Gesamt EUR      Anteil EUR")
            for pos in template["table"]:
                lines.append(f"{pos:<32} {rnd_amount(rng, 800, 25_000):>12}  {rnd_amount(rng, 20, 900):>12}")
            lines.append(f"{'Summe':<32} {rnd_amount(rng, 30_000, 120_000):>12}  {rnd_amount(rng, 900, 6_000):>12}")
        lines.append("")
        lines.append(
            "Dieses Dokument wurde maschinell erstellt und ist ohne Unterschrift gültig. "
            "Bei Rückfragen wenden Sie sich bitte an die Verwaltung."
        )
        lines.append(f"Seite {page_no} von {pages}")
        result.append(lines)
    return result


def write_digital_pdf(path: Path, page_texts: list[list[str]]) -> None:
    c = canvas.Canvas(str(path), pagesize=A4)
    width, height = A4
    for lines in page_texts:
        y = height - 20 * mm
        c.setFont("Helvetica-Bold", 12)
        for idx, line in enumerate(lines):
            if idx == 2:
                c.setFont("Helvetica", 10)
            # einfache Zeilenumbruchlogik fuer lange Absaetze
            for chunk in wrap(line, 95):
                c.drawString(20 * mm, y, chunk)
                y -= 5.2 * mm
                if y < 20 * mm:
                    break
        c.showPage()
    c.save()


def wrap(text: str, width: int) -> list[str]:
    if len(text) <= width or text.startswith("Position") or "  " in text:
        return [text]
    words = text.split(" ")
    out, cur = [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width and cur:
            out.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        out.append(cur)
    return out


def rasterize(pdf: Path, dpi: int, workdir: Path) -> list[Path]:
    """Rastert alle Seiten als Graustufen-PNG. Nutzt pdftoppm, sonst Ghostscript."""
    prefix = workdir / "page"
    if shutil.which("pdftoppm"):
        subprocess.run(["pdftoppm", "-r", str(dpi), "-gray", "-png", str(pdf), str(prefix)],
                       check=True, capture_output=True)
    elif shutil.which("gs"):
        subprocess.run(["gs", "-q", "-dNOPAUSE", "-dBATCH", "-sDEVICE=pnggray", f"-r{dpi}",
                        f"-sOutputFile={prefix}-%03d.png", str(pdf)], check=True, capture_output=True)
    else:
        sys.exit("Weder pdftoppm (poppler-utils) noch gs (Ghostscript) gefunden.")
    return sorted(workdir.glob("page-*.png"))


def write_scan_pdf(digital_pdf: Path, scan_pdf: Path, dpi: int, degrade: bool, rng: random.Random) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        pngs = rasterize(digital_pdf, dpi, Path(tmp))
        images = []
        for png in pngs:
            img = Image.open(png).convert("L")
            if degrade:
                angle = rng.uniform(-0.4, 0.4)
                img = img.rotate(angle, resample=Image.BICUBIC, expand=False, fillcolor=255)
            images.append(img)
        first, rest = images[0], images[1:]
        # Pillow schreibt eine reine Bild-PDF ohne Textebene (JPEG-Kodierung fuer Graustufen)
        first.save(str(scan_pdf), "PDF", save_all=True, append_images=rest, resolution=dpi,
                   quality=75 if degrade else 90)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, type=Path, help="Zielverzeichnis des Korpus")
    ap.add_argument("--pages", type=int, default=100, help="Gesamtzahl Seiten (Standard 100)")
    ap.add_argument("--scan-share", type=float, default=0.5, help="Anteil Scan-Seiten 0 bis 1 (Standard 0,5)")
    ap.add_argument("--max-pages-per-doc", type=int, default=6, help="Seiten je Dokument hoechstens (Standard 6)")
    ap.add_argument("--dpi", type=int, default=300, help="Rasteraufloesung der Scans (Standard 300)")
    ap.add_argument("--degrade", action="store_true", help="Scans leicht drehen und staerker komprimieren")
    ap.add_argument("--seed", type=int, default=20260910, help="Zufallsstartwert (deterministisch)")
    args = ap.parse_args()

    if not 0.0 <= args.scan_share <= 1.0:
        sys.exit("--scan-share muss zwischen 0 und 1 liegen")
    rng = random.Random(args.seed)
    out = args.out
    (out / "digital").mkdir(parents=True, exist_ok=True)
    (out / "scan").mkdir(parents=True, exist_ok=True)
    (out / "truth").mkdir(parents=True, exist_ok=True)

    target_scan = round(args.pages * args.scan_share)
    target_digital = args.pages - target_scan
    manifest = {"seed": args.seed, "dpi": args.dpi, "degrade": args.degrade, "documents": []}
    counters = {"scan": 0, "digital": 0}
    doc_index = 0

    for kind, target in (("scan", target_scan), ("digital", target_digital)):
        while counters[kind] < target:
            pages = min(rng.randint(1, args.max_pages_per_doc), target - counters[kind])
            template = rng.choice(TEMPLATES)
            fields = make_fields(rng)
            page_texts = build_page_texts(rng, template, fields, pages)
            doc_index += 1
            name = f"doc{doc_index:04d}_{kind}"
            digital_pdf = out / "digital" / f"{name}.pdf" if kind == "digital" else out / "scan" / f"{name}.src.pdf"
            write_digital_pdf(digital_pdf, page_texts)
            final_pdf = digital_pdf
            if kind == "scan":
                final_pdf = out / "scan" / f"{name}.pdf"
                write_scan_pdf(digital_pdf, final_pdf, args.dpi, args.degrade, rng)
                digital_pdf.unlink()  # Quelle mit Textebene nicht neben dem Scan lassen
            truth_dir = out / "truth" / name
            truth_dir.mkdir(exist_ok=True)
            for i, lines in enumerate(page_texts, start=1):
                (truth_dir / f"p{i:03d}.txt").write_text("\n".join(lines), encoding="utf-8")
            manifest["documents"].append({
                "name": name,
                "kind": kind,
                "pages": pages,
                "path": str(final_pdf.relative_to(out)),
                "truth_dir": str(truth_dir.relative_to(out)),
                "category_hint": template["category"],
                "sha256": sha256_of(final_pdf),
                "bytes": final_pdf.stat().st_size,
            })
            counters[kind] += pages

    manifest["pages_scan"] = counters["scan"]
    manifest["pages_digital"] = counters["digital"]
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Korpus erzeugt unter {out}: {len(manifest['documents'])} Dokumente, "
          f"{counters['scan']} Scan-Seiten, {counters['digital']} Digitalseiten, {args.dpi} dpi")
    return 0


if __name__ == "__main__":
    sys.exit(main())
