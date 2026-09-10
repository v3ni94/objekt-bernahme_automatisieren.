#!/usr/bin/env bash
# Test T8 (docs/betrieb.md 9): jede Migration vorwaerts und rueckwaerts auf leerer Datenbank.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-.venv/bin/python}"
export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-objektakte.settings.test}" PYTHONPATH=src
$PY manage.py migrate --noinput
# Rueckwaerts in Abhaengigkeitsreihenfolge, dann wieder vorwaerts
for app in appconfig audit mfa account accounts; do
  $PY manage.py migrate --noinput "$app" zero
done
$PY manage.py migrate --noinput
$PY manage.py migrate --check --noinput
echo "Migrationen vorwaerts und rueckwaerts fehlerfrei"
