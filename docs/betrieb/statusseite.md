# Statusseite status.mueller-holding.ag (Stand 25.09.2026)

Statusseite der Müller Holding AG für den VPS: Auslastung, Verfügbarkeit und Verlauf aller Messwerte des Wirts und
aller Container, gespeichert für 400 Tage, Anzeige im CI der Müller Holding AG. Die Seite ist ohne Anmeldung
öffentlich lesbar (Entscheidung Vorstand 25.09.2026). Quelle: Verzeichnis `monitoring/` im
Repository, eigenes Compose-Projekt `status`, unabhängig von der Objektakte.

## Bestandteile und Messtakt

| Dienst | Aufgabe | Takt |
|---|---|---|
| `node-exporter` | Werte des Wirts: CPU je Kern und Betriebsart, Last, PSI, Speicher, Swap, Netz je Schnittstelle, Datenträger (Durchsatz, Vorgänge, Antwortzeit, Auslastung), Dateisysteme, Prozesse, Zombies, Dateideskriptoren, Zeitabweichung, Temperaturen | 15 s |
| `cadvisor` | Werte je Container: CPU, Arbeitsspeicher und Grenze, Netz, Datenträger, Drosselung, Startzeit | 30 s |
| `blackbox` | HTTP-Prüfungen der eigenen Dienste: Erreichbarkeit, Antwortzeit, HTTP-Status, Zertifikatslaufzeit | 60 s |
| `prometheus` | Speicher der Messwerte, Aufbewahrung 400 Tage oder höchstens 60 GB, nur intern erreichbar | |
| `web` | Oberfläche (statisch, nginx) und Durchreichen der beiden Leseabfragen `query` und `query_range` an Prometheus | |

Der Takt ist bewusst so gewählt, dass der Wirt nicht spürbar belastet wird; die Statusseite zeigt ihre eigenen
Container mit an, die Last des Messstacks ist damit selbst nachvollziehbar.

Oberfläche: Kennzahlen (CPU, Last, CPU-Druck, Arbeitsspeicher, Swap, Platte, Netz, Container, Dienste, Zombies,
Betriebszeit), Zeitbereiche 15 Minuten, 1 Stunde, 24 Stunden, 7 Tage, 30 Tage, 1 Jahr, Abschnitte zum Auf- und
Zuklappen, CPU je Kern mit Klick auf den Verlauf des Kerns, Containertabelle mit Klick auf den Verlauf des
Containers, Tabellen der Dateisysteme und der Prüfungen. Ziehen mit der Maus vergrößert einen Ausschnitt, Doppelklick
setzt zurück. Aktualisierung alle 30 Sekunden (Bereiche bis 24 Stunden) beziehungsweise alle 5 Minuten, abschaltbar.

## Einrichtung (als root auf dem VPS)

1. Deploy-Checkout aktualisieren: `d pull $B`.
2. Teilnetz des Messnetzes prüfen (muss frei sein, sonst `STATUS_SUBNET` und `STATUS_GATEWAY` in der `.env` ändern):

   ```
   docker network inspect -f '{{range .IPAM.Config}}{{.Subnet}}{{"\n"}}{{end}}' $(docker network ls -q) | sort | grep 172.31.250 || echo frei
   ```

3. Konfiguration anlegen und die Traefik-Werte aus der Objektakte übernehmen:

   ```
   cd /opt/objektakte/monitoring && cp .env.example .env
   grep -E '^TRAEFIK_(NETWORK|ENTRYPOINT|CERTRESOLVER)=' /opt/objektakte/.env
   ```

   Die drei Werte in die `.env` der Statusseite eintragen.
4. Starten und prüfen:

   ```
   docker compose up -d --build
   docker compose ps
   docker compose logs --tail 20 node-exporter prometheus
   ```

   `node-exporter` muss ohne Bindungsfehler laufen (er lauscht auf der Gateway-Adresse des Messnetzes). Danach
   `https://status.mueller-holding.ag` aufrufen; die ersten Verläufe füllen sich innerhalb von zwei Minuten, längere
   Zeitbereiche mit der Zeit.

## Betrieb

- Aktualisieren nach Änderungen im Repository: `d pull $B`, dann `cd /opt/objektakte/monitoring && docker compose up -d --build`.
- Weitere Dienste prüfen: Ziele in `monitoring/prometheus/prometheus.yml` ergänzen (Job `pruefungen` für Dienste
  mit Antwort 2xx, Job `pruefungen_erreichbar` für Dienste mit Anmeldung davor). Änderung im Repository, nicht im
  Deploy-Checkout, danach `docker compose kill -s HUP prometheus` zum Neuladen.
- Speicherbedarf: der Abschnitt "System und Messung" zeigt den Platz der Messdatenbank; nach einer Woche ablesen und
  auf 400 Tage hochrechnen. Die Obergrenze `PROM_RETENTION_SIZE` (60 GB) greift vorher, ältere Werte werden dann
  verworfen. Prometheus ist auf 2 Kerne und 4 GB begrenzt.
- Sicherung: das Volume `status_prometheus-data` ist nicht Teil der Sicherung der Objektakte. Messwerte gelten als
  entbehrlich; soll die Historie gesichert werden, ist das Volume gesondert aufzunehmen.
- Störungsbilder: Hinweis "Messdatenbank antwortet mit HTTP 502" auf der Seite bedeutet, Prometheus läuft nicht
  (`docker compose logs prometheus`). Fehlen die Kerne und Kennzahlen des Wirts, ist `node-exporter` nicht erreichbar
  (`docker compose logs node-exporter`, Bindung an `STATUS_GATEWAY`). Fehlen nur die Container, `cadvisor` prüfen.

## Sicherheit

- Die Seite ist öffentlich ohne Anmeldung erreichbar (Entscheidung Vorstand 25.09.2026). Sichtbar sind damit
  Containernamen, Auslastung, Dateisysteme, Schnittstellen und die Ergebnisse der HTTP-Prüfungen; Geheimnisse,
  Dokumente oder personenbezogene Daten enthält sie nicht. Die Seite ist für Suchmaschinen gesperrt, und Traefik
  begrenzt Anfragen je Adresse (30 je Sekunde, Spitze 80), damit die Messdatenbank über die offene Leseschnittstelle
  nicht überlastet wird. Soll die Anmeldung wieder eingeschaltet werden: Middleware `basicauth` mit
  `users` aus einer htpasswd-Zeile in `docker-compose.yml` ergänzen und in `mhag-status.middlewares` eintragen.
- Prometheus, cAdvisor und Blackbox sind nur im Messnetz erreichbar; nginx reicht ausschließlich die beiden
  Leseabfragen durch, alle anderen Pfade der Prometheus-Schnittstelle liefern 404.
- `node-exporter` läuft im Netz des Wirts (nötig für die echten Schnittstellen), lauscht aber nur auf der
  Gateway-Adresse des Messnetzes, nicht auf öffentlichen Adressen.
- `cadvisor` läuft privilegiert mit ausschließlich lesend eingebundenen Verzeichnissen, wie vom Projekt vorgegeben.
- Die Oberfläche lädt keine Fremdressourcen; Schriftstapel und Diagrammbibliothek (uPlot, MIT) liegen lokal.
