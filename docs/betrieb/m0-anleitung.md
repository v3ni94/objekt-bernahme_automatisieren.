# Anleitung Meilenstein M0: Serverbefund und OCR-Probelauf

Stand: 10.09.2026. M0 misst und baut nichts. Er darf vor der Freigabe FG-1 bis FG-3 laufen (Umsetzungsplan 1.3) und braucht nur den SSH-Zugang für einen Deploy-Nutzer (V-01) und die Freigabe für den temporären Messcontainer (V-03).

## Ergebnis von M0

| Datei | Inhalt | Wird zu |
|---|---|---|
| `serverbefund-<datum>.md` | Kerne, RAM, Platte, Docker, Traefik-Werte, Host-Sicherheit, Ergebnisblatt, `.env`-Vorschlag | `docs/betrieb/serverbefund.md` (nach Prüfung auf Geheimnisse) |
| `probe-out/<zeit>/performance-entscheidung.md` | t_ocr, e, m_ocr, t_txt, Zeichenfehlerrate, Szenariotabelle, Empfehlung zu P und Tarif | `docs/betrieb/performance-entscheidung.md` |
| `probe-out/<zeit>/results.json` | Rohdaten der Messreihe | Anlage zum Prüfpunktbericht P0 |

## Durchführung auf dem VPS

1. Als Deploy-Nutzer anmelden (kein root). Repository klonen oder die Verzeichnisse `scripts/`, `tests/performance/` und `docker/` kopieren.
2. Serverbefund (nur lesend, rund eine Minute):

   ```bash
   bash scripts/measure_server.sh
   ```

   Die Datei `serverbefund-<datum>.md` vor der Übernahme in das Repository auf Geheimnisse prüfen (Suche nach password, secret, token, E-Mail-Adressen). Das Skript liest `acme.json` nicht und gibt Umgebungsvariablen nur mit Namen aus.

3. OCR-Probelauf (temporärer Container, kein Netzwerk, keine Ports, wird nach dem Lauf entfernt; Dauer je nach Kernzahl 10 bis 30 Minuten für 100 Seiten mit drei Parallelitätsstufen):

   ```bash
   bash scripts/perf_probe.sh --pages 100 --scan-share 0.5 --procs 1,2,auto
   ```

   `auto` ist Kerne minus 1. Für den Vergleich mit `tessdata_fast` den Lauf mit einem Image wiederholen, das die schnellen Sprachdaten enthält, und `perf_probe.py --tessdata-dir` verwenden; ob die Debian-Pakete die Variante fast oder standard liefern, zeigt die Größe von `deu.traineddata` im Bericht.

4. Kontrolle, dass nichts zurückbleibt:

   ```bash
   docker ps -a | grep objektakte-probe    # leer
   docker images | grep objektakte-probe   # leer, sofern nicht --keep-image
   ```

5. Beide Ergebnisdateien prüfen, nach `docs/betrieb/` übernehmen, `.env.example` mit den Traefik-Werten und der Prozesszahl befüllen (Umsetzungsplan 2.1 Schritt 9) und den Prüfpunktbericht P0 erstellen (Umsetzungsplan 2.2).

## Entscheidungsregel am Prüfpunkt P0 (Frage F18)

Der Bericht `performance-entscheidung.md` rechnet den Rechenweg aus Umsetzungsplan 2.8 mit den Messwerten und markiert, ob mit P = Kerne minus 1 das OCR-Budget von zwei Stunden im Basisfall (ANNAHME A-04: 40 Prozent Digitalseiten) und im ungünstigen Fall (keine Digitalseiten) gehalten wird. Ergibt der Lauf weniger als fünf nutzbare OCR-Prozesse oder mehr als 5 Sekunden je Seite, ist die Entscheidung nach Frage F18 zu treffen (Tarifwechsel, `tessdata_fast`, Zwei-Phasen-OCR oder längere Laufzeit), bevor M1 beginnt.

## Grenzen der Messung

- Der Korpus ist synthetisch (gerenderte Helvetica-Seiten, leicht gedreht, JPEG-komprimiert) und sauberer als echte Scans. Die Zeichenfehlerrate liegt deshalb nahe null und sagt wenig über echte Unterlagen; die OCR-Zeit je Seite ist davon wenig abhängig. Optional 50 anonymisierte echte Scanseiten ergänzen (V-16).
- Die Größe von `deu.traineddata` im Bericht zeigt, welche Sprachdatenvariante das Paket liefert: rund 1,5 MB spricht für die schnelle Variante (tessdata_fast), rund 15 MB für die Standard- oder Best-Variante. Erfahrungswert, gegen die Tesseract-Dokumentation zum Umsetzungszeitpunkt zu prüfen. Liefert das Paket bereits die schnelle Variante, entfällt der Stellhebel `tessdata_fast` aus Frage F18, und die Genauigkeit ist mit der Standardvariante gegenzuprüfen.
- MaxRSS je Prozess misst den größten Einzelprozess bei kurzen Dokumenten; `docker-stats.log` enthält die Speicherspitze des gesamten Containers als zweiten Wert. Der Speicherbedarf je OCR-Block wird in M5 mit dem 1.000-Seiten-Messlauf gegengeprüft.
- Die Messung läuft ohne CPU-Limit, damit die tatsächliche Leistung des Servers sichtbar wird. Im Betrieb begrenzt `WORKER_CPUS` den Worker auf P Kerne (docs/architektur.md 4.4).
- Die Skalierungseffizienz e wird am größten gemessenen P bestimmt; auf einem Rechner mit wenigen Kernen fällt sie niedriger aus als auf dem Zielserver mit mehr Kernen und weniger Fremdlast.

## Validierung des Messverfahrens

Die Skripte wurden am 10.09.2026 in der Entwicklungsumgebung (4 vCPU, nicht der VPS) funktional geprüft: `tests/unit/test_perf_model.py` grün (10 Tests), Serverbefund erzeugt (Ergebnisblatt und `.env`-Vorschlag vollständig, Traefik-Abschnitt ohne Traefik leer), Korpus mit 12 Seiten erzeugt (Scans nachweislich ohne Textebene), Probelauf mit P = 1, 2, 3 durchgelaufen, Bericht und `results.json` erzeugt. Die dort gemessenen Zahlen sind keine Aussage über den Zielserver und wurden deshalb nicht in das Repository übernommen.

Hinweis aus der Validierung: Das Mess-Image verwendet ausschließlich Debian-Pakete und einen einzigen Python-Interpreter. Eine Mischung aus pip-installierten und paketierten Python-Bibliotheken (Pillow, pikepdf) hat in der Entwicklungsumgebung ocrmypdf lahmgelegt; diese Erfahrung gilt auch für das spätere Worker-Image (docs/architektur/image.md in M1).
