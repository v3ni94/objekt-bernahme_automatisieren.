# Kurzbefehle fuer Entwicklung und Betrieb (Ziele aus docs/architektur.md 11)
PY ?= .venv/bin/python
export DJANGO_SETTINGS_MODULE ?= objektakte.settings.development
export PYTHONPATH := src

.PHONY: venv lint format test test-integration migrate seed run check-legacy check-pii perf migrations-roundtrip

venv:
	uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements-dev.lock.txt

lint:
	.venv/bin/ruff check src tests manage.py && .venv/bin/ruff format --check src tests manage.py

format:
	.venv/bin/ruff format src tests manage.py && .venv/bin/ruff check --fix src tests manage.py

test:
	DJANGO_SETTINGS_MODULE=objektakte.settings.test $(PY) -m pytest -q

migrate:
	$(PY) manage.py migrate --noinput

seed:
	$(PY) manage.py seed

run:
	$(PY) manage.py runserver 127.0.0.1:8000

check-legacy:
	scripts/check_no_legacy_names.sh

check-pii:
	scripts/check_no_pii.sh

migrations-roundtrip:
	scripts/check_migrations_roundtrip.sh

perf:
	scripts/perf_probe.sh --pages 100 --scan-share 0.5 --procs 1,2,auto
