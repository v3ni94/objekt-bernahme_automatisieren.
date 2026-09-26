#!/usr/bin/env bash
# Test T8 (docs/betrieb.md 9): jede Migration vorwaerts und rueckwaerts auf leerer Datenbank.
set -euo pipefail
# ROUNDTRIP_REPO (26.09.2026): Deploy-Aktion migrations-roundtrip reicht das Skript per Standardeingabe in den
# Web-Container (bash -s, $0 ist dann kein Pfad) und nennt das Repository im Container (/app) ausdruecklich.
cd "${ROUNDTRIP_REPO:-$(dirname "$0")/..}"
PY="${PYTHON:-.venv/bin/python}"
export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-objektakte.settings.test}" PYTHONPATH=src
$PY manage.py migrate --noinput
# Rueckwaerts in Abhaengigkeitsreihenfolge, dann wieder vorwaerts
# Fachtabellen zuerst (Django nimmt abhaengige Migrationen automatisch mit zurueck), danach Sicherheit und Konfiguration
for app in sync requirements lists ai review imports pipeline drive documents parties objects appconfig audit mfa account accounts; do
  $PY manage.py migrate --noinput "$app" zero
done
$PY manage.py migrate --noinput
$PY manage.py migrate --check --noinput
echo "Migrationen vorwaerts und rueckwaerts fehlerfrei"
