#!/usr/bin/env bash
# Prueft Repository-Dokumentation und Tests gegen die Stammdaten-CSVs des Bestands (Beschluss B-39).
# Gibt nur Trefferzaehler aus, nie Namen. Ohne CSVs (CI) wird die Pruefung uebersprungen.
set -uo pipefail
cd "$(dirname "$0")/.."
CSV_DIR="${PII_CSV_DIR:-}"
if [ -z "$CSV_DIR" ] || [ ! -f "$CSV_DIR/kontakte.csv" ]; then
  echo "PII-Pruefung uebersprungen: PII_CSV_DIR nicht gesetzt oder kontakte.csv fehlt"; exit 0
fi
python3 - "$CSV_DIR" <<'PY'
import csv, re, sys, pathlib
csv_dir = pathlib.Path(sys.argv[1])
emails, phones, addresses = set(), set(), set()
with open(csv_dir / "kontakte.csv", encoding="utf-8") as fh:
    for row in csv.DictReader(fh, delimiter=";"):
        if row.get("E-Mail"): emails.add(row["E-Mail"].strip().lower())
        if row.get("Telefon"): phones.add(re.sub(r"\D", "", row["Telefon"])[-8:])
        if row.get("Adresse") and len(row["Adresse"]) > 8: addresses.add(row["Adresse"].strip())
emails.discard("info@muellerhv.de")
text = ""
for p in list(pathlib.Path("docs").rglob("*.md")) + list(pathlib.Path("tests").rglob("*.py")) + list(pathlib.Path("db").rglob("*.json")) + [pathlib.Path("README.md")]:
    if "entwurf" in p.parts and p.name == "befundakte.md":
        continue
    text += p.read_text(encoding="utf-8", errors="ignore").lower() + "\n"
digits = re.sub(r"\D", "", text)
hits = sum(1 for e in emails if e in text) + sum(1 for a in addresses if a.lower() in text and "rheinpromenade" not in a.lower()) + sum(1 for ph in phones if len(ph) == 8 and ph in digits and ph not in ("92331850",))
print(f"PII-Pruefung: {hits} Treffer")
sys.exit(1 if hits else 0)
PY
