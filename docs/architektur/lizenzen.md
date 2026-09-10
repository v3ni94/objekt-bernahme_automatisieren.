# Lizenzen der Abhängigkeiten

Stand: 10.09.2026, aus den Paketmetadaten der Entwicklungsumgebung (`importlib.metadata`), Versionen laut Lockfiles zum Zeitpunkt der Erzeugung. Für den internen Betrieb der Hausverwaltung Müller GmbH sind die Lizenzen unkritisch; bei Weitergabe oder Verkauf der Software ist eine rechtliche Prüfung nach F29 einzuholen (keine Rechtsberatung durch dieses Dokument).

## Python-Pakete

| Paket | Version | Lizenz (Metadaten) | Ziel |
|---|---|---|---|
| Django | 5.2.17 | BSD-3-Clause | web |
| django-allauth | 65.19.2 | MIT | web |
| gunicorn | 26.2.0 | MIT | web |
| whitenoise | 6.12.0 | MIT | web |
| mysqlclient | 2.2.8 | GPL-2.0-or-later | web, worker |
| celery | 5.6.3 | BSD-3-Clause | web, worker |
| redis | 6.4.0 | MIT | web, worker |
| cryptography | 50.0.1 | Apache-2.0 OR BSD-3-Clause | web, worker |
| jsonschema | 4.26.0 | MIT | web, worker |
| argon2-cffi | 25.1.0 | MIT | web |
| qrcode | 8.2 | BSD | web |
| ocrmypdf | 17.11.0 | MPL-2.0 | worker |
| pikepdf | 10.13.0.post1 | MPL-2.0 | worker |
| pypdfium2 | 5.13.0 | BSD-3-Clause, Apache-2.0, dependency licenses | worker |
| pdfplumber | 0.11.10 | MIT License | worker |
| img2pdf | 0.6.3 | GNU Lesser General Public License v3 (LGPLv3) | worker |
| Pillow | 12.3.0 | MIT-CMU | worker |
| rapidfuzz | 3.14.6 | MIT | worker |
| scikit-learn | 1.9.0 | BSD-3-Clause | worker |
| spacy | 3.8.16 | MIT | worker |
| openpyxl | 3.1.5 | MIT | worker |
| reportlab | 5.0.1 | BSD License | worker |
| python-docx | 1.2.0 | MIT | worker |
| google-api-python-client | 2.200.0 | Apache 2.0 | worker |
| google-auth | 2.58.0 | Apache 2.0 | worker |
| google-auth-oauthlib | 1.4.1 | Apache 2.0 | worker |
| openai | 3.11.0 | Apache-2.0 | worker |
| anthropic | 1.4.0 | MIT | worker |
| tenacity | 9.1.4 | Apache 2.0 | worker |
| pydantic | 2.13.5 | MIT | worker |

## Systemkomponenten in den Images und auf dem Host

| Komponente | Lizenz | Hinweis |
|---|---|---|
| Tesseract OCR mit Sprachdaten deu | Apache-2.0 | über das Betriebssystem im Worker-Image |
| Ghostscript | AGPL-3.0 | Abhängigkeit von ocrmypdf; Aufruf als Kommandozeilenprogramm im eigenen Betrieb; bei Weitergabe der Software Prüfung nach F29 (AGPL-Pflichten) |
| qpdf, unpaper, pngquant, poppler-utils | Apache-2.0, GPL-2.0, BSD-2, GPL-2.0 (poppler) | Kommandozeilenwerkzeuge der OCR-Kette |
| MariaDB | GPL-2.0 | Datenbankserver als eigener Container; der Python-Treiber mysqlclient steht unter GPL-2.0-or-later, Alternative bei Bedarf PyMySQL (MIT) |
| Redis | Lizenzlage seit 2024 geändert (RSALv2 und SSPLv1 für Version 7.4 und später, ab 8.0 zusätzlich AGPL-3.0); Version im Compose prüfen | Alternative Valkey (BSD-3-Clause) als Ersatz möglich, Kompatibilität mit dem Python-Client beachten |
| Traefik | MIT | Reverse Proxy |
| Docker Engine, Compose | Apache-2.0 | Host |

## Bewertung

- Alle Python-Pakete stehen unter permissiven Lizenzen (MIT, BSD, Apache-2.0, MPL-2.0, LGPL-3.0 für img2pdf, GPL-2.0-or-later für mysqlclient).
- Copyleft-Komponenten (Ghostscript AGPL, mysqlclient GPL, MariaDB GPL) werden im eigenen Betrieb genutzt und nicht weitergegeben; damit entstehen keine Weitergabepflichten. Vor einer Weitergabe an Dritte ist die Lage anwaltlich zu prüfen (F29).
- Die Lizenzangaben stammen aus den Paketmetadaten und sind bei jedem Versionswechsel der Lockfiles zu aktualisieren (Wartungsplan, quartalsweise).
