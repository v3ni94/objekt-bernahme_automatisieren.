# Images und Paketlisten

Stand: 10.09.2026 (M1). Ein Dockerfile (`docker/app.Dockerfile`) mit zwei Zielen (Beschluss B-44), dazu das Backup-Image und das temporäre Mess-Image für M0. Versionen werden nicht als Fakt genannt; Basis-Images und Paketstände sind zum Umsetzungszeitpunkt zu prüfen und über die Lockfiles festgeschrieben.

## Grundsatz aus M0

Je Image genau ein Python-Interpreter mit einem Lockfile. Eine Mischung aus pip-installierten und paketierten Python-Bibliotheken (Pillow, pikepdf) hat in der Entwicklungsumgebung ocrmypdf unbenutzbar gemacht. Deshalb kommen im Ziel `worker` Tesseract, Ghostscript, qpdf und poppler aus dem Betriebssystem, alle Python-Pakete (ocrmypdf, pikepdf, Pillow) aus dem Lockfile in einer eigenen virtuellen Umgebung.

## Lockfiles

| Datei | Inhalt | Verwendet von |
|---|---|---|
| `requirements-web.lock.txt` | Django, allauth mit MFA, gunicorn, whitenoise, mysqlclient, Celery mit Redis, cryptography, jsonschema, argon2 | Ziel `web` (web, beat) |
| `requirements-worker.lock.txt` | zusätzlich ocrmypdf, pikepdf, pypdfium2, pdfplumber, img2pdf, Pillow, rapidfuzz, scikit-learn, spaCy, openpyxl, reportlab, python-docx, Google-Drive-Client, OpenAI- und Anthropic-SDK, tenacity | Ziel `worker` (worker, worker-nlp, worker-io, classifier) |
| `requirements-dev.lock.txt` | alles plus pytest, pytest-django, factory-boy, freezegun, hypothesis, ruff, mypy | Entwicklung, CI |

Erzeugung: `uv pip compile pyproject.toml [--extra worker] [--extra dev] -o <datei>`; Anhebung geplant quartalsweise mit Testsuite (docs/umsetzungsplan.md R-19).

## Ziel web

- Basis: offizielles Python-Slim-Image auf Debian (Tag über `PYTHON_IMAGE` steuerbar).
- Systempakete: `libmariadb3`, `ca-certificates`, `tzdata`.
- Kein Tesseract, kein Ghostscript, kein Compiler im Laufzeit-Image (Build-Stage getrennt).
- Nutzer `app` mit `APP_UID` (Standard 10001, ANNAHME AB1), `read_only: true` mit tmpfs in Compose.
- Statische Dateien werden zur Bauzeit gesammelt (`objektakte.settings.build`, keine Geheimnisse, keine Datenbank).

## Ziel worker

- Zusätzlich: `tesseract-ocr`, `tesseract-ocr-deu`, `ghostscript`, `qpdf`, `poppler-utils`, `unpaper`, `pngquant`, `fonts-dejavu-core`, `curl`.
- Sprachdaten über das Build-Argument `TESSDATA_VARIANT`: `debian` (Paketstand; im OCR-Probelauf war die Datei rund 1,5 MB groß, was auf die schnelle Variante hindeutet), `fast` (tessdata_fast), `standard` (tessdata). Entscheidung nach M0 (Frage F18).
- `OMP_THREAD_LIMIT=1`, damit ein OCR-Prozess genau einen Kern belegt.

## Backup-Image

`debian:bookworm-slim` mit `mariadb-client`, `tar`, `gzip`, `jq`, `age`, `rclone`, `cron`. Skripte `backup.sh`, `restore.sh`, `entrypoint.sh` (Crontab aus `BACKUP_CRON`, `status.json` beim ersten Start).

## Mess-Image (nur M0)

`docker/probe.Dockerfile`: `debian:bookworm-slim` mit Distributionspaketen für Python, ReportLab, Pillow, Tesseract, ocrmypdf, Ghostscript, poppler und GNU time. Wird nach dem Probelauf entfernt.

## Lizenzhinweise

Siehe docs/betrieb.md Anhang C (Ghostscript AGPL, mysqlclient GPL nach Kenntnisstand, Redis-Lizenzlage). Für den internen Betrieb unkritisch; bei Weitergabe Prüfung durch einen Rechtsanwalt (Frage F29).
