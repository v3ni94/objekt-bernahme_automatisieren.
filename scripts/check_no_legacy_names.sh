#!/usr/bin/env bash
# Definition of Done (CR 14): Die Altbezeichnung des Auffangordners kommt im Code nicht vor.
# Gepruefte Pfade: src/, tests/, docs/, scripts/, docker/. Dokumentierte Ausnahmen (Umsetzungsplan F20, B-22):
# db/seeds/ (Konfigurationswert drive.legacy_folder_aliases), docs/anforderungen/ (der CR selbst),
# docs/entwurf/ (historische Arbeitspapiere). Das Muster wird hier zusammengesetzt, damit dieses Skript sich nicht selbst meldet.
set -uo pipefail
cd "$(dirname "$0")/.."
PATTERN="05_$(printf 'Sonst')iges"
HITS=$(grep -rIl --exclude-dir=.git --exclude-dir=.venv --exclude-dir=node_modules --exclude-dir=entwurf --exclude-dir=anforderungen \
        -e "$PATTERN" src tests docs scripts docker README.md 2>/dev/null || true)
if [ -n "$HITS" ]; then
  echo "Altbezeichnung des Auffangordners gefunden in:"; echo "$HITS"; exit 1
fi
echo "Grep-Pruefung leer: keine Altbezeichnung ausserhalb von db/seeds/, docs/anforderungen/ und docs/entwurf/"
