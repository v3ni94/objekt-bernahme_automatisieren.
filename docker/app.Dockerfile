# Anwendungs-Image mit zwei Zielen (Beschluss B-44, docs/architektur.md 4.1):
#   web    Django, gunicorn, Celery Beat; ohne OCR-Binaerdateien (oeffentlich erreichbarer Dienst)
#   worker zusaetzlich Tesseract mit deu, ocrmypdf, Ghostscript, qpdf, poppler und die ML-Bibliotheken
# Ein einziger Python-Interpreter je Ziel (Erfahrung aus M0: keine Mischung aus pip und Distributionspaketen).
# Basis-Image und Paketstand zum Umsetzungszeitpunkt pruefen (docs/architektur/image.md).

ARG PYTHON_IMAGE=python:3.12-slim-bookworm

# ---------------------------------------------------------------- Builder: Abhaengigkeiten kompilieren
FROM ${PYTHON_IMAGE} AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential pkg-config libmariadb-dev \
 && rm -rf /var/lib/apt/lists/*
COPY requirements-web.lock.txt requirements-worker.lock.txt /tmp/
RUN python -m venv /opt/venv-web && /opt/venv-web/bin/pip install -r /tmp/requirements-web.lock.txt
RUN python -m venv /opt/venv-worker && /opt/venv-worker/bin/pip install -r /tmp/requirements-worker.lock.txt

# ---------------------------------------------------------------- Gemeinsame Laufzeitbasis
FROM ${PYTHON_IMAGE} AS runtime-base
ARG APP_UID=10001
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=objektakte.settings.production \
    PYTHONPATH=/app/src PATH=/opt/venv/bin:$PATH
RUN apt-get update && apt-get install -y --no-install-recommends libmariadb3 ca-certificates tzdata \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --gid ${APP_UID} app && useradd --uid ${APP_UID} --gid app --create-home --shell /usr/sbin/nologin app \
 && mkdir -p /app /data && chown app:app /app /data
WORKDIR /app
COPY --chown=app:app manage.py pyproject.toml ./
COPY --chown=app:app src ./src
COPY --chown=app:app db ./db
COPY --chown=app:app docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# ---------------------------------------------------------------- Ziel web
FROM runtime-base AS web
COPY --from=builder /opt/venv-web /opt/venv
# Statische Dateien zur Bauzeit sammeln; die Bauzeit-Settings brauchen keine Geheimnisse und keine Datenbank
RUN DJANGO_SETTINGS_MODULE=objektakte.settings.build python manage.py collectstatic --noinput \
 && chown -R app:app /app/staticfiles
USER app
EXPOSE 8000
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["gunicorn", "objektakte.wsgi:application", "--bind", "0.0.0.0:8000"]

# ---------------------------------------------------------------- Ziel worker
FROM runtime-base AS worker
# TESSDATA_VARIANT: debian (Sprachdaten des Distributionspakets, Groesse im OCR-Probelauf pruefen),
# fast (tessdata_fast) oder standard (tessdata). Entscheidung nach M0 (Frage F18).
ARG TESSDATA_VARIANT=debian
RUN apt-get update && apt-get install -y --no-install-recommends \
      tesseract-ocr tesseract-ocr-deu ghostscript qpdf poppler-utils unpaper pngquant \
      fonts-dejavu-core curl \
 && rm -rf /var/lib/apt/lists/* \
 && TD=/usr/share/tesseract-ocr/5/tessdata \
 && case "${TESSDATA_VARIANT}" in \
      fast)     curl -fsSL -o "$TD/deu.traineddata" https://github.com/tesseract-ocr/tessdata_fast/raw/main/deu.traineddata ;; \
      standard) curl -fsSL -o "$TD/deu.traineddata" https://github.com/tesseract-ocr/tessdata/raw/main/deu.traineddata ;; \
      debian)   : ;; \
      *) echo "Unbekannte TESSDATA_VARIANT ${TESSDATA_VARIANT}" && exit 1 ;; \
    esac \
 && ls -l "$TD/deu.traineddata"
COPY --from=builder /opt/venv-worker /opt/venv
ENV OMP_THREAD_LIMIT=1
USER app
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["celery", "-A", "objektakte", "worker", "-Q", "ocr", "--concurrency", "1"]
