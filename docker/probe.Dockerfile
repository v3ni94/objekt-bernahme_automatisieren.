# Temporaerer Messcontainer fuer den OCR-Probelauf (Meilenstein M0).
# Kein Bestandteil der Anwendung. Wird nach dem Lauf entfernt (scripts/perf_probe.sh).
#
# Bewusst ausschliesslich Debian-Pakete und ein einziger Python-Interpreter, damit ocrmypdf,
# pikepdf, Pillow und ReportLab aus derselben Paketquelle stammen und zueinander passen.
# Basis-Image und Paketstand zum Umsetzungszeitpunkt pruefen; die Werkzeuge entsprechen der
# CR-Vorgabe (Tesseract mit Sprachpaket deu, ocrmypdf).
FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    OMP_THREAD_LIMIT=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      python3 \
      python3-reportlab \
      python3-pil \
      tesseract-ocr \
      tesseract-ocr-deu \
      ocrmypdf \
      ghostscript \
      poppler-utils \
      time \
      fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

# Nicht privilegierter Nutzer; das Ausgabeverzeichnis wird gemountet
RUN useradd --uid 10001 --create-home probe
USER probe
WORKDIR /out
